#!/usr/bin/env python3
"""
tools/record_wave_receipt.py

Cryptographic receipt generator and sequential wave lock manager.
Enforces wave progression governance:
1. Calculates content SHA-256 digests for all wave deliverables.
2. Validates parent receipt chaining to prevent skipped waves.
3. Performs atomic writes (.tmp -> fsync -> os.replace).
4. Creates sequential lock file: artifacts/locks/wave-<N>.complete.
5. Applies immutable read-only permissions (0o444) when requested.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    if not path.is_file():
        return "MISSING_FILE"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_dict_digest(payload: Dict[str, Any]) -> str:
    """Computes deterministic RFC-8259 SHA-256 digest of a JSON-serializable dictionary."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_atomic_json(target_path: Path, payload: Dict[str, Any], immutable: bool = False, allow_overwrite: bool = False) -> None:
    """Writes JSON payload atomically using .tmp -> fsync -> os.replace."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".tmp")

    if target_path.exists():
        if not allow_overwrite:
            raise FileExistsError(
                f"Receipt destination already exists at {target_path}. Exclusive creation prohibits overwrites."
            )
        try:
            os.chmod(target_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, target_path)

    if immutable:
        # Read-only permissions: 0o444
        os.chmod(target_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _check_pre_wave_decisions(wave: int, plan_path: Optional[Path], ledger_path: Optional[Path]) -> None:
    pre_gate_script = Path(__file__).resolve().parent / "pre_wave_decision_gate.py"
    if not pre_gate_script.exists():
        return
    import subprocess
    pre_cmd = [sys.executable, str(pre_gate_script), "--wave", str(wave)]
    if plan_path:
        pre_cmd.extend(["--plan-path", str(plan_path)])
    if ledger_path:
        pre_cmd.extend(["--ledger-path", str(ledger_path)])
    pre_res = subprocess.run(pre_cmd, capture_output=True, text=True)
    if pre_res.returncode == 2:
        raise RuntimeError(
            f"WAVE_RECEIPT_BLOCKED: OPERATOR_INPUT_REQUIRED - Wave {wave} has OPEN deferred decisions requiring human operator review. Receipt sealing strictly prohibited."
        )
    elif pre_res.returncode == 3:
        raise RuntimeError(
            "WAVE_RECEIPT_BLOCKED: NO_DECISIONS_FOUND - Automated checkpoint found 0 decision records in scope. Receipt sealing strictly prohibited under Decision D6 policy."
        )
    elif pre_res.returncode != 0:
        raise RuntimeError(f"WAVE_RECEIPT_BLOCKED: Pre-wave decision check failed with exit code {pre_res.returncode}.")


def _validate_parent_receipt(wave: int, parent_path: Optional[Path]) -> Optional[str]:
    if wave > 1:
        if not parent_path:
            raise RuntimeError(f"WAVE_RECEIPT_BLOCKED: Wave {wave} requires parent receipt but none was provided.")
        if not parent_path.is_file():
            raise FileNotFoundError(f"Parent receipt not found: {parent_path}")
        parent_data = json.loads(parent_path.read_text(encoding="utf-8"))
        if parent_data.get("status") != "COMPLETE":
            raise RuntimeError(f"WAVE_RECEIPT_BLOCKED: Parent receipt status is '{parent_data.get('status')}', expected 'COMPLETE'.")
        if parent_data.get("wave") is not None and parent_data.get("wave") != wave - 1:
            raise RuntimeError(
                f"WAVE_RECEIPT_BLOCKED: Wave sequence discontinuity: parent receipt is Wave {parent_data.get('wave')}, expected Wave {wave - 1}."
            )
        return parent_data.get("receipt_digest") or compute_file_sha256(parent_path)
    if parent_path:
        if not parent_path.is_file():
            raise FileNotFoundError(f"Parent receipt not found: {parent_path}")
        parent_data = json.loads(parent_path.read_text(encoding="utf-8"))
        return parent_data.get("receipt_digest") or compute_file_sha256(parent_path)
    return None


def _parse_manifest_deliverables(manifest_path: Optional[Path]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    deliverables = []
    manifest_digest = None
    if manifest_path and manifest_path.is_file():
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_digest = compute_file_sha256(manifest_path)
        raw_items = manifest_data.get("deliverables", []) if isinstance(manifest_data, dict) else []
        for item in raw_items:
            item_path = Path(item.get("path", ""))
            if not item_path.is_file():
                raise RuntimeError(f"WAVE_RECEIPT_BLOCKED: Deliverable artifact is missing: {item_path}")
            item_digest = compute_file_sha256(item_path)
            deliverables.append({
                "path": str(item_path),
                "type": item.get("type", "FILE"),
                "status": "VERIFIED",
                "sha256": item_digest,
            })
    return deliverables, manifest_digest


def _write_wave_lock(lock_dir: Path, wave: int, receipt_digest: str, now_iso: str, allow_overwrite: bool) -> None:
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / f"wave-{wave}.complete"
    if lock_file.exists() and not allow_overwrite:
        raise FileExistsError(
            f"Sequential lock file already exists at {lock_file}. Exclusive creation prohibits overwrites."
        )
    lock_content = f"WAVE_{wave}_COMPLETE\nRECEIPT_DIGEST={receipt_digest}\nTIMESTAMP={now_iso}\n"
    lock_file.write_text(lock_content, encoding="utf-8")


def record_wave_receipt(
    wave: int,
    manifest_path: Optional[Path],
    output_path: Path,
    parent_path: Optional[Path] = None,
    immutable: bool = False,
    lock_dir: Path = Path("artifacts/locks"),
    plan_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    allow_overwrite: bool = False,
) -> Dict[str, Any]:
    """Generates immutable wave receipt and creates sequential completion lock."""
    _check_pre_wave_decisions(wave, plan_path, ledger_path)
    parent_digest = _validate_parent_receipt(wave, parent_path)
    deliverables, manifest_digest = _parse_manifest_deliverables(manifest_path)

    now_iso = datetime.now(timezone.utc).isoformat()
    receipt_body = {
        "wave": wave,
        "wave_id": f"Wave {wave}",
        "status": "COMPLETE",
        "timestamp": now_iso,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest_digest": manifest_digest,
        "parent_receipt": str(parent_path) if parent_path else None,
        "parent_receipt_digest": parent_digest,
        "deliverables": deliverables,
        "verified_by": "record_wave_receipt.py",
    }
    receipt_digest = compute_dict_digest(receipt_body)
    receipt_body["receipt_digest"] = receipt_digest

    write_atomic_json(output_path, receipt_body, immutable=immutable, allow_overwrite=allow_overwrite)
    _write_wave_lock(lock_dir, wave, receipt_digest, now_iso, allow_overwrite)
    return receipt_body


def verify_receipt(receipt_path: Path) -> bool:
    """Verifies cryptographic integrity of a generated receipt."""
    if not receipt_path.is_file():
        print(f"[ERROR] Receipt file not found: {receipt_path}", file=sys.stderr)
        return False

    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    recorded_digest = data.get("receipt_digest")

    copy_body = dict(data)
    copy_body.pop("receipt_digest", None)
    recalculated = compute_dict_digest(copy_body)

    if recorded_digest != recalculated:
        copy_empty = dict(data)
        copy_empty["receipt_digest"] = ""
        recalculated_empty = compute_dict_digest(copy_empty)
        if recorded_digest == recalculated_empty:
            recalculated = recalculated_empty

    if recorded_digest != recalculated:
        print(f"[ERROR] Digest mismatch for {receipt_path}!", file=sys.stderr)
        print(f"  Recorded:     {recorded_digest}", file=sys.stderr)
        print(f"  Recalculated: {recalculated}", file=sys.stderr)
        return False

    print(f"[PASS] Receipt verified: {receipt_path} (Digest: {recorded_digest})")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Cryptographic Wave Receipt & Sequential Lock Manager")
    parser.add_argument("--wave", type=int, required=True, help="Wave number (e.g. 1, 2, 3)")
    parser.add_argument("--manifest", type=Path, help="Path to input manifest JSON")
    parser.add_argument("--output", type=Path, required=True, help="Path to write output receipt JSON")
    parser.add_argument("--parent", type=Path, help="Path to parent wave receipt JSON")
    parser.add_argument("--plan-path", type=Path, help="Path to wave plan markdown file")
    parser.add_argument("--ledger-path", type=Path, help="Path to decision ledger")
    parser.add_argument("--lock-dir", type=Path, default=Path("artifacts/locks"), help="Directory for sequential wave locks")
    parser.add_argument("--immutable", action="store_true", help="Set read-only permissions on receipt")
    parser.add_argument("--allow-overwrite", action="store_true", help="Allow overwriting existing receipt and lock files")
    parser.add_argument("--verify", action="store_true", help="Verify integrity of existing receipt")

    args = parser.parse_args()

    if args.verify:
        return 0 if verify_receipt(args.output) else 1

    receipt = record_wave_receipt(
        wave=args.wave,
        manifest_path=args.manifest,
        output_path=args.output,
        parent_path=args.parent,
        immutable=args.immutable,
        lock_dir=args.lock_dir,
        plan_path=args.plan_path,
        ledger_path=args.ledger_path,
        allow_overwrite=args.allow_overwrite,
    )
    print(f"[SUCCESS] Wave {args.wave} receipt recorded: {args.output}")
    print(f"  Digest: {receipt['receipt_digest']}")
    print(f"  Lock:   {args.lock_dir}/wave-{args.wave}.complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
