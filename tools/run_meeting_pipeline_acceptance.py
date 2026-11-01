#!/usr/bin/env python3
"""
tools/run_meeting_pipeline_acceptance.py — Governed Meeting Acceptance Runner

Enforces sequential wave receipts, isolated runtime roots, and test-grounded
qualification for meeting speaker attribution and runtime reduction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


def compute_file_sha256(filepath: Path) -> str:
    """Compute SHA256 of a file if it exists."""
    if not filepath.exists():
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_revision() -> str:
    """Get current git commit hash."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unversioned"


def validate_runtime_isolation(runtime_root: Path) -> tuple[bool, str]:
    """Ensure runtime root is an isolated sandbox directory, never shared production .local."""
    resolved = runtime_root.resolve()
    shared_prod_local = (REPO_ROOT / ".local").resolve()
    if resolved == shared_prod_local:
        return False, "Cannot use shared production root (.local); must use an isolated sandbox directory."
    return True, "Isolated runtime root verified."


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Governed Meeting Pipeline Acceptance Runner")
    parser.add_argument("--wave", type=int, required=True, help="Wave number (0 through 8)")
    parser.add_argument("--runtime-root", type=str, required=True, help="Path to isolated runtime root")
    parser.add_argument("--receipt", type=str, required=True, help="Target receipt output path")
    parser.add_argument(
        "--manifest",
        type=str,
        default=str(REPO_ROOT / "tests" / "manifests" / "meeting_pipeline_acceptance_manifest.json"),
        help="Path to acceptance test manifest",
    )
    parser.add_argument("--require-receipt", type=str, default=None, help="Explicit override for prerequisite receipt")
    parser.add_argument("--check-only", action="store_true", help="Validate prerequisites without running tests")
    return parser.parse_args(args)


def verify_prerequisites(
    wave: int,
    runtime_root: Path,
    wave_cfg: Dict[str, Any],
    require_receipt_override: Optional[str] = None,
) -> tuple[bool, str, Optional[Dict[str, Any]]]:
    """Verify prerequisite receipts and required baseline assets."""
    # Check required artifacts for any wave declaring them
    req_artifacts = wave_cfg.get("required_artifacts", [])
    for rel_path in req_artifacts:
        art_path = REPO_ROOT / rel_path
        if not art_path.exists():
            return False, f"Missing required artifact: {rel_path}", None

    if wave == 0:
        return True, "Wave 0 baseline artifacts verified.", None

    # Wave > 0 requires prior receipt
    prereq_name = require_receipt_override or wave_cfg.get("prerequisite_receipt")
    if not prereq_name:
        return True, "No prerequisite receipt declared.", None

    # Search in runtime receipts
    candidates = [
        runtime_root / "receipts" / prereq_name,
        runtime_root / prereq_name,
    ]
    if runtime_root == REPO_ROOT or str(runtime_root.resolve()).endswith("/.local"):
        candidates.extend([
            REPO_ROOT / "receipts" / prereq_name,
            REPO_ROOT / "reports" / prereq_name,
        ])
    found_receipt: Optional[Path] = None
    for cand in candidates:
        if cand.exists():
            found_receipt = cand
            break

    if not found_receipt:
        return (
            False,
            f"BLOCKED: Prerequisite receipt '{prereq_name}' for Wave {wave} not found in {runtime_root}/receipts.",
            None,
        )

    try:
        data = json.loads(found_receipt.read_text(encoding="utf-8"))
        outcome = data.get("terminal_outcome") or data.get("status")
        if outcome not in ("PASS", "COMPLETE"):
            return False, f"Prerequisite receipt '{prereq_name}' outcome is '{outcome}', expected PASS.", data
        return True, f"Prerequisite receipt '{prereq_name}' verified [PASS].", data
    except Exception as e:
        return False, f"Failed to parse prerequisite receipt '{prereq_name}': {e}", None


def execute_wave_tests(test_files: List[str]) -> tuple[bool, Dict[str, int]]:
    """Execute declared wave test files using pytest with --timeout=60."""
    if not test_files:
        return True, {"passed": 0, "failed": 0, "total": 0}

    valid_files = [f for f in test_files if (REPO_ROOT / f).exists()]
    if not valid_files:
        return False, {"passed": 0, "failed": len(test_files), "total": len(test_files)}

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *valid_files,
        "-v",
        "--timeout=60",
    ]

    env = dict(os.environ)
    env["NEE_RUN_BGE_TESTS"] = "1"
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, env=env)
    passed_cnt = 0
    failed_cnt = 0

    for line in res.stdout.splitlines():
        if " passed in " in line:
            parts = line.split(" passed in ")[0].split()
            if parts and parts[-1].isdigit():
                passed_cnt = int(parts[-1])
        if " failed" in line:
            parts = line.split(" failed")[0].split()
            if parts and parts[-1].isdigit():
                failed_cnt = int(parts[-1])

    total_cnt = passed_cnt + failed_cnt
    is_success = (res.returncode == 0) and (failed_cnt == 0)
    return is_success, {"passed": passed_cnt, "failed": failed_cnt, "total": total_cnt}


def _load_wave_config(manifest_path: Path, wave: int) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not manifest_path.exists():
        return None, f"Acceptance manifest not found: {manifest_path}"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    wave_key = str(wave)
    if wave_key not in manifest_data.get("waves", {}):
        return None, f"Wave {wave} not declared in manifest {manifest_path.name}"
    return manifest_data["waves"][wave_key], None


def _build_receipt_payload(
    wave: int,
    wave_cfg: Dict[str, Any],
    terminal_outcome: str,
    runtime_root: Path,
    manifest_path: Path,
    start_wall: str,
    end_wall: str,
    mono_duration_s: float,
    test_counts: Dict[str, int],
) -> Dict[str, Any]:
    evidence_digests: Dict[str, str] = {}
    for art in wave_cfg.get("required_artifacts", []):
        art_p = REPO_ROOT / art
        if art_p.exists():
            evidence_digests[art] = compute_file_sha256(art_p)

    code_hash = get_git_revision()
    config_hash = compute_file_sha256(manifest_path)
    run_id = f"run-karyn-wave-{wave}-{uuid.uuid4().hex[:8]}"

    payload: Dict[str, Any] = {
        "receipt_id": f"receipt-meeting-wave-{wave}-{uuid.uuid4().hex[:8]}",
        "wave": wave,
        "title": wave_cfg.get("title", ""),
        "terminal_outcome": terminal_outcome,
        "run_id": run_id,
        "code_hash": code_hash,
        "config_hash": config_hash,
        "runtime_root": str(runtime_root),
        "started_at": start_wall,
        "ended_at": end_wall,
        "monotonic_duration_s": mono_duration_s,
        "test_counts": test_counts,
        "evidence_digests": evidence_digests,
        "rollback_reference": code_hash,
        "prerequisite_receipt": wave_cfg.get("prerequisite_receipt"),
        "receipt_digest": "",
    }

    if wave == 8:
        for qp in (runtime_root / "reports" / "wave8_qualification_summary.json", REPO_ROOT / "reports" / "wave8_qualification_summary.json"):
            if qp.is_file():
                try:
                    payload["qualification_summary"] = json.loads(qp.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

    canonical_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    payload["receipt_digest"] = hashlib.sha256(canonical_bytes).hexdigest()
    return payload


def _write_sealed_receipt(receipt_path: Path, payload: Dict[str, Any]) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path.exists():
        try:
            os.chmod(receipt_path, 0o644)
        except Exception:
            pass
    receipt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(receipt_path, 0o444)
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    start_mono = time.perf_counter()
    start_wall = datetime.now(timezone.utc).isoformat()
    args = parse_args(argv)

    runtime_root = Path(args.runtime_root).resolve()
    receipt_path = Path(args.receipt).resolve()
    manifest_path = Path(args.manifest).resolve()

    print("=" * 80)
    print(f"  MEETING PIPELINE ACCEPTANCE RUNNER: WAVE {args.wave}")
    print(f"  Runtime Root: {runtime_root}")
    print(f"  Receipt Path: {receipt_path}")
    print("=" * 80)

    # 1. Isolation check
    iso_ok, iso_msg = validate_runtime_isolation(runtime_root)
    if not iso_ok:
        print(f"❌ {iso_msg}")
        return 1
    print(f"✔ {iso_msg}")

    # 2. Load manifest
    wave_cfg, err = _load_wave_config(manifest_path, args.wave)
    if err or not wave_cfg:
        print(f"❌ {err}")
        return 1

    # 3. Verify prerequisites
    prereq_ok, prereq_msg, _ = verify_prerequisites(args.wave, runtime_root, wave_cfg, args.require_receipt)
    if not prereq_ok:
        print(f"❌ {prereq_msg}")
        return 1
    print(f"✔ {prereq_msg}")

    if args.check_only:
        print("✔ Check-only mode: Prerequisites verified successfully.")
        return 0

    # 4. Execute tests
    test_files = list(dict.fromkeys(wave_cfg.get("deterministic_tests", []) + wave_cfg.get("e2e_tests", [])))
    print(f"Executing {len(test_files)} declared test suites for Wave {args.wave}...")
    tests_ok, test_counts = execute_wave_tests(test_files)

    end_wall = datetime.now(timezone.utc).isoformat()
    mono_duration_s = round(time.perf_counter() - start_mono, 6)
    terminal_outcome = "PASS" if tests_ok else "FAIL"

    # 5. Build and seal receipt
    payload = _build_receipt_payload(
        args.wave, wave_cfg, terminal_outcome, runtime_root, manifest_path,
        start_wall, end_wall, mono_duration_s, test_counts
    )
    _write_sealed_receipt(receipt_path, payload)

    print("-" * 80)
    print(f"Terminal Outcome: {terminal_outcome}")
    print(f"Receipt Sealed:   {receipt_path} (digest: {payload['receipt_digest'][:16]}...)")
    print(f"Duration:         {mono_duration_s}s | Tests: {test_counts}")
    print("=" * 80)

    return 0 if tests_ok else 1


if __name__ == "__main__":
    sys.exit(main())
