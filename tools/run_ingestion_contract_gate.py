#!/usr/bin/env python3
"""
tools/run_ingestion_contract_gate.py

Ingestion Contract Gate Runner & Runtime Receipt Generator for Ingestion Efficiency v2.
Enforces:
1. Sequential lock progression (Wave N requires Wave N-1 complete).
2. Top-level dotted module enrollment in tests/manifests/ingestion_modality_hardening.json.
3. Exact test node resolution from reports/ingestion_hardening/wave_acceptance_map.json.
4. Non-empty mode coverage (document, transcript, notes, live).
5. At least one E2E and one adversarial test per pipeline wave.
6. Execution via tools/run_tests_with_progress.py with live progress.
7. Atomic generation of immutable runtime receipt with cryptographic artifact bindings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.test_manifest_loader import validate_manifest_payload, TEST_IDENTIFIER_RE


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingestion Modality Contract Gate Runner")
    parser.add_argument("--wave", type=int, required=True, help="Wave number to execute (1-8)")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "tests" / "manifests" / "ingestion_modality_hardening.json",
        help="Path to decentralized test registration manifest",
    )
    parser.add_argument(
        "--wave-map",
        type=Path,
        default=REPO_ROOT / "reports" / "ingestion_hardening" / "wave_acceptance_map.json",
        help="Path to wave acceptance mapping JSON",
    )
    parser.add_argument(
        "--require-modes",
        type=str,
        default="document,transcript,notes,live",
        help="Comma-separated required modes to qualify",
    )
    parser.add_argument("--timeout", type=int, default=60, help="Per-test hard timeout in seconds")
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        default=REPO_ROOT / "reports" / "ingestion_hardening" / "receipts",
        help="Directory to persist immutable receipts and lock files",
    )
    parser.add_argument("--allow-overwrite", action="store_true", help="Allow overwriting existing receipt")
    parser.add_argument("--collect-only", action="store_true", help="Collect test nodes without executing")
    return parser.parse_args(argv)


def compute_file_sha256(path: Path) -> str:
    if not path.is_file():
        return "MISSING_FILE"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_git_state() -> Tuple[str, bool, str]:
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        rev = "unversioned"

    try:
        status_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        is_dirty = bool(status_res.stdout.strip())
        diff_res = subprocess.run(["git", "diff", "HEAD"], capture_output=True)
        diff_digest = hashlib.sha256(diff_res.stdout).hexdigest()
    except Exception:
        is_dirty = True
        diff_digest = "unknown"

    return rev, is_dirty, diff_digest


def verify_sequential_lock(wave: int, receipt_dir: Path) -> Tuple[bool, str]:
    if wave <= 1:
        return True, "Wave 1 initial wave; no predecessor lock required."

    pred_receipt = receipt_dir / f"ingestion_hardening_wave_{wave - 1}_receipt.json"
    pred_lock = receipt_dir / f"wave-{wave - 1}.complete"

    if not pred_receipt.is_file():
        return False, f"Missing predecessor receipt for wave {wave - 1} at {pred_receipt}"
    if not pred_lock.is_file():
        return False, f"Missing predecessor lock for wave {wave - 1} at {pred_lock}"

    try:
        data = json.loads(pred_receipt.read_text(encoding="utf-8"))
        if data.get("terminal_outcome") != "PASS":
            return False, f"Predecessor wave {wave - 1} did not PASS (outcome={data.get('terminal_outcome')})"
    except Exception as exc:
        return False, f"Corrupted predecessor receipt for wave {wave - 1}: {exc}"

    return True, f"Predecessor wave {wave - 1} lock and PASS receipt verified."


def validate_test_manifest(manifest_path: Path) -> Tuple[bool, List[str], List[str]]:
    if not manifest_path.is_file():
        return False, [f"Manifest file not found: {manifest_path}"], []

    try:
        raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Invalid JSON in manifest {manifest_path}: {exc}"], []

    valid, errors = validate_manifest_payload(raw_data, source=str(manifest_path))
    if not valid:
        return False, errors, []

    modules: List[str] = []
    for key in ("deterministic_tests", "e2e_tests", "bge_tests"):
        for mod in raw_data.get(key, []):
            if not TEST_IDENTIFIER_RE.match(mod):
                errors.append(f"Module '{mod}' in '{key}' violates dotted-identifier requirement.")
            modules.append(mod)

    return len(errors) == 0, errors, modules


def load_wave_acceptance(
    wave: int, wave_map_path: Path, required_modes: Set[str]
) -> Tuple[bool, str, Dict[str, Any], List[str]]:
    if not wave_map_path.is_file():
        return False, f"Wave map not found: {wave_map_path}", {}, []

    try:
        data = json.loads(wave_map_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Invalid JSON in wave map: {exc}", {}, []

    wave_key = str(wave)
    waves = data.get("waves", {})
    if wave_key not in waves:
        return False, f"Wave {wave} not registered in wave map {wave_map_path}", {}, []

    wave_entry = waves[wave_key]
    entry_modes = set(wave_entry.get("modes_required", []))
    missing_modes = required_modes - entry_modes
    if missing_modes:
        return False, f"Wave {wave} missing required modes: {sorted(missing_modes)}", wave_entry, []

    exact_nodes = wave_entry.get("exact_test_nodes", [])
    if not exact_nodes:
        return False, f"Wave {wave} has empty exact_test_nodes list", wave_entry, []

    has_e2e = any("e2e" in node.lower() for node in exact_nodes)
    has_adversarial = any("adversarial" in node.lower() or "edge_case" in node.lower() for node in exact_nodes)
    if not has_e2e or not has_adversarial:
        return (
            False,
            f"Wave {wave} scope must contain at least one E2E test and at least one adversarial test. "
            f"(has_e2e={has_e2e}, has_adversarial={has_adversarial})",
            wave_entry,
            exact_nodes,
        )

    return True, "Wave acceptance configuration valid", wave_entry, exact_nodes


def write_atomic_json(target_path: Path, payload: Dict[str, Any], allow_overwrite: bool = False) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".tmp")
    if target_path.exists() and not allow_overwrite:
        raise FileExistsError(f"Receipt already exists at {target_path}. Overwrite prohibited.")
    if target_path.exists():
        try:
            os.chmod(target_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, target_path)
    os.chmod(target_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    wave = args.wave
    receipt_dir = args.receipt_dir
    receipt_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Ingestion Modality Contract Gate: Wave {wave} ===")

    # 1. Verify Sequential Lock
    ok, msg = verify_sequential_lock(wave, receipt_dir)
    if not ok:
        print(f"[FAIL] Sequential lock error: {msg}", file=sys.stderr)
        return 1
    print(f"[PASS] {msg}")

    # 2. Validate Test Manifest
    ok, errors, modules = validate_test_manifest(args.manifest)
    if not ok:
        print(f"[FAIL] Manifest validation errors: {errors}", file=sys.stderr)
        return 1
    print(f"[PASS] Manifest validated with {len(modules)} enrolled test modules.")

    # 3. Load Wave Acceptance Map
    required_modes = {m.strip().lower() for m in args.require_modes.split(",") if m.strip()}
    ok, msg, wave_entry, exact_nodes = load_wave_acceptance(wave, args.wave_map, required_modes)
    if not ok:
        print(f"[FAIL] Wave acceptance error: {msg}", file=sys.stderr)
        return 1
    print(f"[PASS] {msg} ({len(exact_nodes)} exact test nodes in scope)")

    if args.collect_only:
        print("\n--- Test Nodes in Scope ---")
        for node in exact_nodes:
            print(f"  {node}")
        return 0

    # 4. Execute exact test nodes via tools/run_tests_with_progress.py
    runner_script = REPO_ROOT / "tools" / "run_tests_with_progress.py"
    cmd = [
        sys.executable,
        str(runner_script),
        "--runner",
        "pytest",
        f"--timeout={args.timeout}",
        *exact_nodes,
    ]

    print(f"\n[RUN] Launching {len(exact_nodes)} test target(s) via progress runner...")
    t0_mono = time.monotonic()
    t0_wall = datetime.now(timezone.utc)

    result = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True)
    t1_mono = time.monotonic()
    t1_wall = datetime.now(timezone.utc)

    elapsed_s = round(t1_mono - t0_mono, 3)
    outcome = "PASS" if result.returncode == 0 else "FAIL"

    git_rev, is_dirty, diff_digest = get_git_state()
    plan_path = REPO_ROOT / "docs" / "plans" / "docs_rca_document_ingestion_efficiency_20261008.md"

    # 5. Build and Write Immutable Runtime Receipt
    receipt_payload: Dict[str, Any] = {
        "schema_version": "2.0",
        "receipt_type": "ingestion_contract_wave_receipt",
        "wave": wave,
        "terminal_outcome": outcome,
        "timestamp_start": t0_wall.isoformat(),
        "timestamp_end": t1_wall.isoformat(),
        "monotonic_elapsed_s": elapsed_s,
        "git_state": {
            "revision": git_rev,
            "dirty": is_dirty,
            "diff_sha256": diff_digest,
        },
        "coverage": {
            "modes_required": sorted(required_modes),
            "modes_verified": wave_entry.get("modes_required", []),
            "cases_evaluated": wave_entry.get("case_ids", []),
            "permanent_cases": wave_entry.get("permanent_cases", []),
            "exact_test_nodes_count": len(exact_nodes),
            "exact_test_nodes": exact_nodes,
        },
        "evidence_bindings": {
            "manifest_path": str(args.manifest.relative_to(REPO_ROOT) if args.manifest.is_relative_to(REPO_ROOT) else args.manifest),
            "manifest_sha256": compute_file_sha256(args.manifest),
            "wave_map_path": str(args.wave_map.relative_to(REPO_ROOT) if args.wave_map.is_relative_to(REPO_ROOT) else args.wave_map),
            "wave_map_sha256": compute_file_sha256(args.wave_map),
            "plan_path": str(plan_path.relative_to(REPO_ROOT) if plan_path.is_relative_to(REPO_ROOT) else plan_path),
            "plan_sha256": compute_file_sha256(plan_path),
            "prerequisite_ledger_sha256": compute_file_sha256(REPO_ROOT / "reports" / "ingestion_hardening" / "prerequisite_defect_ledger.json"),
            "normative_oracle_sha256": compute_file_sha256(REPO_ROOT / "reports" / "ingestion_hardening" / "normative_oracle.json"),
        },
        "runner_execution": {
            "command": cmd[:5] + [f"... ({len(exact_nodes)} nodes)"],
            "exit_code": result.returncode,
        },
    }

    receipt_file = receipt_dir / f"ingestion_hardening_wave_{wave}_receipt.json"
    try:
        write_atomic_json(receipt_file, receipt_payload, allow_overwrite=args.allow_overwrite)
        print(f"\n[RECEIPT] Sealed runtime receipt: {receipt_file}")
    except Exception as exc:
        print(f"[FAIL] Unable to write runtime receipt: {exc}", file=sys.stderr)
        return 1

    if outcome == "PASS":
        lock_file = receipt_dir / f"wave-{wave}.complete"
        lock_file.write_text(f"Wave {wave} completed at {t1_wall.isoformat()} (git={git_rev})\n", encoding="utf-8")
        print(f"[LOCK] Created sequential wave lock: {lock_file}")
        print(f"=== Wave {wave} Contract Gate: PASS (elapsed {elapsed_s}s) ===")
        return 0
    else:
        print(f"=== Wave {wave} Contract Gate: FAIL (exit code {result.returncode}) ===", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
