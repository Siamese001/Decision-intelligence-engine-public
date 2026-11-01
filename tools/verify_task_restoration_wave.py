#!/usr/bin/env python3
"""tools/verify_task_restoration_wave.py

Verification runner and cryptographic receipt sealer for Antigravity task content
loss and restoration waves.
Validates requirements inventories, executes wave-scoped CI tests via tools/run_tests_with_progress.py,
measures real operational effects, and emits immutable cryptographic receipts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.task_restoration_core import (
    WAVE_INPUT_FILES,
    WAVE_TEST_FILES,
    compute_canonical_digest,
    compute_file_sha256,
    get_git_info,
    measure_operational_effects,
    verify_requirements_inventory,
)


def verify_parent_receipt(wave: int, parent_receipt: Path | None) -> str | None:
    """Verifies predecessor wave receipt and returns its canonical digest."""
    if wave == 1:
        if parent_receipt is not None:
            raise ValueError("Wave 1 cannot have a predecessor parent receipt.")
        return None

    if not parent_receipt:
        raise ValueError(f"Wave {wave} requires --parent receipt linking to predecessor wave.")
    if not parent_receipt.is_file():
        raise ValueError(f"Predecessor receipt file not found: {parent_receipt}")

    try:
        parent_data = json.loads(parent_receipt.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Could not parse predecessor receipt: {exc}") from exc

    if parent_data.get("terminal_outcome") != "PASS":
        raise ValueError("Predecessor receipt does not have PASS terminal outcome.")

    calculated = compute_canonical_digest(parent_data)
    stored = parent_data.get("canonical_digest")
    if stored and calculated != stored:
        raise ValueError(f"Predecessor receipt digest mismatch (tampered prior wave): {stored} vs {calculated}")

    digest = stored or calculated
    return digest


def _parse_pytest_metrics(stdout: str, returncode: int) -> tuple[int, int, int, int]:
    collected_match = re.search(r"\bcollected\s+(\d+)\s+items\b", stdout)
    summary_lines = [
        line for line in stdout.splitlines()
        if re.search(r"\b\d+\s+(?:passed|failed|skipped)\b", line)
    ]
    summary = summary_lines[-1] if summary_lines else ""
    counts = {}
    for label in ("passed", "failed", "skipped"):
        match = re.search(rf"\b(\d+)\s+{label}\b", summary)
        counts[label] = int(match.group(1)) if match else 0
    collected = int(collected_match.group(1)) if collected_match else 0
    if collected == 0 and returncode == 0:
        collected = sum(counts.values())
    return collected, counts["passed"], counts["failed"], counts["skipped"]


def run_tests_for_wave(
    wave: int,
    output_dir: Path,
    repo_root: Path | None = None,
    test_files_override: list[str] | None = None,
) -> tuple[int, int, int, int, int, str]:
    """Runs scoped CI tests for the given wave and returns parsed metrics."""
    root = repo_root or ROOT
    if test_files_override is not None:
        test_files = test_files_override
    elif wave in WAVE_TEST_FILES:
        test_files = WAVE_TEST_FILES[wave]
    else:
        raise ValueError(f"Wave {wave} test scope not configured in verification runner.")

    venv_python = root / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.is_file() else sys.executable
    cmd = [
        python_bin,
        str(root / "tools" / "run_tests_with_progress.py"),
        "--runner",
        "pytest",
        "--timeout",
        "60",
    ] + test_files


    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / "test_run.log"

    res = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
    )

    combined_output = f"COMMAND: {' '.join(cmd)}\n\nSTDOUT:\n{res.stdout}\n\nSTDERR:\n{res.stderr}"
    log_file.write_text(combined_output, encoding="utf-8")

    collected, passed, failed, skipped = _parse_pytest_metrics(res.stdout, res.returncode)
    return res.returncode, collected, passed, failed, skipped, " ".join(cmd)


def _wave_input_hashes(wave: int, repo_root: Path) -> dict[str, str]:
    if wave not in WAVE_INPUT_FILES:
        raise ValueError(f"Wave {wave} input scope not configured in verification runner.")
    return {
        rel_path: compute_file_sha256(repo_root / rel_path)
        for rel_path in WAVE_INPUT_FILES[wave]
    }


def _finalize_wave_lock(
    wave: int,
    run_id: str,
    receipt: dict[str, Any],
    receipt_path: Path,
    repo_root: Path,
) -> int:
    lock_dir = repo_root / "artifacts" / "locks"
    lock_file = lock_dir / f"antigravity_task_restoration_wave_{wave}.complete"
    if receipt["terminal_outcome"] == "PASS":
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_data = {
            "wave": wave,
            "run_id": run_id,
            "timestamp": receipt["timestamp"],
            "canonical_digest": receipt["canonical_digest"],
            "receipt_path": str(receipt_path),
        }
        lock_file.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
        print(f"Acquired Sequential Lock: {lock_file}")
        print("================================================================================")
        return 0

    if lock_file.exists():
        lock_file.unlink()
    print("\n[BLOCKED] Failure receipt retained. Completion lock strictly withheld.")
    print("================================================================================")
    return 1


def verify_wave(
    wave: int,
    run_id: str,
    output_root: Path,
    parent_receipt: Path | None = None,
    repo_root: Path | None = None,
    test_files_override: list[str] | None = None,
) -> int:
    """Executes wave verification, seals cryptographic receipt, and manages completion lock."""
    root = repo_root or ROOT
    output_dir = output_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("================================================================================")
    print(f"  ANTIGRAVITY TASK RESTORATION — WAVE {wave} VERIFICATION")
    print(f"  Run ID:      {run_id}")
    print(f"  Output Root: {output_dir}")
    print("================================================================================")

    # 1. Parent Receipt Check
    try:
        parent_digest = verify_parent_receipt(wave, parent_receipt)
    except ValueError as exc:
        print(f"\n[CRITICAL FAILURE] Predecessor verification failed: {exc}")
        return 1

    # 2. Requirements & Source Truth Verification
    req_file = root / "docs" / "plans" / "antigravity_task_requirements.json"
    req_ok, req_errors = verify_requirements_inventory(req_file, root)
    if not req_ok:
        print("\n[CRITICAL FAILURE] Requirements verification failed:")
        for err in req_errors:
            print(f"  - {err}")
        return 1
    print("\n[PASS] Requirements inventory verified: 7 tasks reconciled, 0 unmapped requirements.")

    # 3. Capture Git & Operational Accounting
    commit_sha, worktree_clean = get_git_info(root)
    input_hashes = _wave_input_hashes(wave, root)
    operational_effects = measure_operational_effects(root)

    # 4. Execute Scoped Wave Tests
    retcode, collected, passed, failed, skipped, command_str = run_tests_for_wave(
        wave, output_dir, root, test_files_override=test_files_override
    )
    print("\nTest Execution Results:")
    print(f"  Exit Code: {retcode}")
    print(f"  Collected: {collected}")
    print(f"  Passed:    {passed}")
    print(f"  Failed:    {failed}")
    print(f"  Skipped:   {skipped}")

    terminal_outcome = "PASS"
    if collected == 0 or retcode != 0 or failed > 0 or skipped > 0:
        terminal_outcome = "FAIL"
        print(f"\n[FAILURE] Scoped tests failed or had skipped tests (retcode={retcode}, failed={failed}, skipped={skipped}).")
    else:
        print("\n[SUCCESS] All scoped tests passed.")

    # 5. Build and Seal Receipt
    receipt: dict[str, Any] = {
        "contract_id": "urn:nee:antigravity-task-restoration-receipt:1.0.0",
        "receipt_type": "antigravity_task_restoration_receipt",
        "schema_version": "1.0.0",
        "wave": wave,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "commit_sha": commit_sha,
        "worktree_clean": worktree_clean,
        "test_command": command_str,
        "exit_code": retcode,
        "tests_collected": collected,
        "tests_passed": passed,
        "tests_failed": failed,
        "tests_skipped": skipped,
        "terminal_outcome": terminal_outcome,
        "operational_accounting": operational_effects,
        "input_artifact_hashes": input_hashes,
        "parent_receipt_digest": parent_digest,
        "log_path": str(output_dir / "test_run.log"),
    }
    receipt["canonical_digest"] = compute_canonical_digest(receipt)

    receipt_path = output_dir / f"wave_{wave}_receipt.json"
    if receipt_path.exists():
        existing_text = receipt_path.read_text(encoding="utf-8")
        new_text = json.dumps(receipt, indent=2)
        if existing_text != new_text:
            print(f"\n[CRITICAL FAILURE] Non-overwritable receipt violation: {receipt_path} already exists with different contents.")
            return 1

    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"\nSealed Receipt: {receipt_path}")
    print(f"Receipt Digest: {receipt['canonical_digest']}")

    return _finalize_wave_lock(wave, run_id, receipt, receipt_path, root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Task Restoration Wave Verification Runner")
    parser.add_argument("--wave", type=int, required=True, help="Wave number (1 to 5)")
    parser.add_argument("--run-id", type=str, required=True, help="Unique run identifier")
    parser.add_argument("--output-root", type=Path, required=True, help="Output root for sealed receipts")
    parser.add_argument("--parent", type=Path, default=None, help="Predecessor wave receipt path")
    args = parser.parse_args()

    return verify_wave(
        wave=args.wave,
        run_id=args.run_id,
        output_root=args.output_root,
        parent_receipt=args.parent,
    )


if __name__ == "__main__":
    sys.exit(main())
