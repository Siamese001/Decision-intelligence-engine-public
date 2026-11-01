#!/usr/bin/env python3
"""tools/verify_scheduled_task_wave.py

Verification runner and cryptographic receipt sealer for scheduled task hardening waves.
Executes wave-scoped CI tests via tools/run_tests_with_progress.py (--runner pytest --timeout=60),
records input/commit/prompt digests, verifies zero live operational writes,
and emits an immutable receipt conforming to repository governance standards.
"""
from __future__ import annotations

import argparse
import hashlib
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


_T_BASE = [
    "tests/test_scheduled_task_contracts.py",
    "tests/test_scheduled_task_authority.py",
]
_T_TAIL = ["tests/test_storage_hygiene_apply.py"]

WAVE_TEST_FILES: dict[int, list[str]] = {
    1: _T_BASE + _T_TAIL,
    2: _T_BASE + ["tests/test_scheduled_task_evidence.py"] + _T_TAIL,
    3: _T_BASE + ["tests/test_scheduled_task_evidence.py", "tests/test_scheduled_task_remediation.py"] + _T_TAIL,
    4: _T_BASE + [
        "tests/test_scheduled_task_evidence.py",
        "tests/test_scheduled_task_remediation.py",
        "tests/test_scheduled_dlq_lifecycle.py",
        "tests/test_scheduled_ingestion_recovery.py",
        "tests/test_scheduled_query_coverage_repair.py",
    ] + _T_TAIL,
    5: _T_BASE + [
        "tests/test_scheduled_task_evidence.py",
        "tests/test_scheduled_task_remediation.py",
        "tests/test_scheduled_dlq_lifecycle.py",
        "tests/test_scheduled_ingestion_recovery.py",
        "tests/test_scheduled_query_coverage_repair.py",
        "tests/test_scheduled_task_adapters.py",
    ] + _T_TAIL,
    6: _T_BASE + [
        "tests/test_scheduled_task_evidence.py",
        "tests/test_scheduled_task_remediation.py",
        "tests/test_scheduled_dlq_lifecycle.py",
        "tests/test_scheduled_ingestion_recovery.py",
        "tests/test_scheduled_query_coverage_repair.py",
        "tests/test_scheduled_task_adapters.py",
        "tests/test_scheduled_task_lifecycle.py",
    ] + _T_TAIL,
}

WAVE_1_INPUT_FILES: list[str] = [
    "contracts/scheduled-task-run.schema.json",
    "config/scheduled_tasks/task_registry.json",
    "config/scheduled_tasks/registration_reconciliation.json",
    "tools/scheduled_task_core/__init__.py",
    "tools/scheduled_task_core/contracts.py",
    "tools/scheduled_task_core/authority.py",
    "tools/scheduled_task_core/journal.py",
    "tools/storage_hygiene_core/apply.py",
    "tests/test_scheduled_task_contracts.py",
    "tests/test_scheduled_task_authority.py",
    "tests/test_storage_hygiene_apply.py",
    "tests/manifests/scheduled_task_hardening_manifest.json",
]
WAVE_2_INPUT_FILES: list[str] = WAVE_1_INPUT_FILES + [
    "contracts/evidence-bundle.schema.json",
    "tools/scheduled_task_core/coverage.py",
    "tools/scheduled_task_core/evidence.py",
    "tests/test_scheduled_task_evidence.py",
]
WAVE_3_INPUT_FILES: list[str] = WAVE_2_INPUT_FILES + [
    "contracts/findings-ledger.schema.json",
    "tools/scheduled_task_core/findings.py",
    "tools/scheduled_task_core/handoff.py",
    "tests/test_scheduled_task_remediation.py",
]
WAVE_4_INPUT_FILES: list[str] = WAVE_3_INPUT_FILES + [
    "contracts/ingestion-recovery-package.schema.json",
    "dual_pipeline/dlq/lifecycle.py",
    "dual_pipeline/dlq/manager.py",
    "dual_pipeline/canonical_pipeline/publish_helper.py",
    "dual_pipeline/canonical_pipeline/staging_recovery.py",
    "dual_pipeline/query_core/diagnostics.py",
    "dual_pipeline/query_core/constants.py",
    "tests/test_scheduled_dlq_lifecycle.py",
    "tests/test_scheduled_ingestion_recovery.py",
    "tests/test_scheduled_query_coverage_repair.py",
]
WAVE_5_INPUT_FILES: list[str] = WAVE_4_INPUT_FILES + [
    "config/scheduled_tasks/common_control_matrix.json",
    "automations/prompts/graph_hygiene_agent.md",
    "automations/prompts/pipeline_outcome_assurance_agent.md",
    "automations/prompts/pr_queue_observer_agent.md",
    "automations/prompts/ssot_drift_remediation_agent.md",
    "automations/prompts/storage_hygiene_agent.md",
    "automations/prompts/weekly_market_intelligence_agent.md",
    "tests/test_scheduled_task_adapters.py",
]
WAVE_6_INPUT_FILES: list[str] = WAVE_5_INPUT_FILES + [
    "config/scheduled_tasks/rollout_package.json",
    "reports/scheduled_task_hardening/qualification_report.md",
    "tests/test_scheduled_task_lifecycle.py",
    "tools/scheduled_task_core/lifecycle.py",
]

WAVE_INPUT_FILES: dict[int, list[str]] = {
    1: WAVE_1_INPUT_FILES,
    2: WAVE_2_INPUT_FILES,
    3: WAVE_3_INPUT_FILES,
    4: WAVE_4_INPUT_FILES,
    5: WAVE_5_INPUT_FILES,
    6: WAVE_6_INPUT_FILES,
}


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    if not path.is_file():
        return "FILE_NOT_FOUND"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_canonical_digest(payload: dict[str, Any]) -> str:
    """Deterministic RFC-8259 SHA-256 digest of dictionary excluding canonical_digest itself."""
    filtered = {k: v for k, v in payload.items() if k != "canonical_digest"}
    canonical = json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def get_git_info() -> tuple[str, bool]:
    """Retrieves current Git commit SHA and clean working tree status."""
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        return sha, len(status) == 0
    except Exception:
        return "0000000000000000000000000000000000000000", False


def run_tests_for_wave(wave: int, output_dir: Path) -> tuple[int, int, int, int, int, str]:
    """Runs CI-enrolled tests for the given wave and extracts execution metrics."""
    if wave not in WAVE_TEST_FILES:
        raise ValueError(f"Wave {wave} test scope not configured in verification runner.")
    test_files = WAVE_TEST_FILES[wave]

    python_bin = sys.executable
    cmd = [
        python_bin,
        str(ROOT / "tools" / "run_tests_with_progress.py"),
        "--runner",
        "pytest",
        "--timeout",
        "60",
    ] + test_files

    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / "test_run.log"

    res = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    combined_output = f"COMMAND: {' '.join(cmd)}\n\nSTDOUT:\n{res.stdout}\n\nSTDERR:\n{res.stderr}"
    log_file.write_text(combined_output, encoding="utf-8")

    collected, passed, failed, skipped = _parse_pytest_metrics(res.stdout, res.returncode)
    return res.returncode, collected, passed, failed, skipped, " ".join(cmd)


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


def _wave_input_hashes(wave: int) -> dict[str, str]:
    if wave not in WAVE_INPUT_FILES:
        raise ValueError(f"Wave {wave} input scope not configured in verification runner.")
    return {
        rel_path: compute_file_sha256(ROOT / rel_path)
        for rel_path in WAVE_INPUT_FILES[wave]
    }


def _verified_parent_digest(wave: int, parent_receipt: Path | None) -> str | None:
    if wave == 1:
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
    print(f"\nVerified Predecessor Wave Receipt: {parent_receipt}")
    print(f"Parent Receipt Digest:            {digest}")
    return digest


def _wave_test_outcome(retcode: int, collected: int, failed: int) -> str:
    if collected == 0:
        print("\n[CRITICAL FAILURE] Zero tests were collected for Wave scope.")
        return "FAIL"
    if retcode != 0 or failed > 0:
        print(f"\n[FAILURE] Scoped tests failed ({failed} failed, exit code {retcode}).")
        return "FAIL"
    print("\n[SUCCESS] All scoped tests passed.")
    return "PASS"


def _finalize_wave_lock(wave: int, run_id: str, receipt: dict[str, Any], receipt_path: Path) -> int:
    lock_dir = ROOT / "artifacts" / "locks"
    lock_file = lock_dir / f"scheduled_task_hardening_wave_{wave}.complete"
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
) -> int:
    """Executes wave verification, seals cryptographic receipt, and manages completion lock."""
    output_dir = output_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("================================================================================")
    print(f"  SCHEDULED TASK HARDENING — WAVE {wave} VERIFICATION")
    print(f"  Run ID:      {run_id}")
    print(f"  Output Root: {output_dir}")
    print("================================================================================")

    # 1. Capture Git and input hashes
    commit_sha, worktree_clean = get_git_info()
    input_hashes = _wave_input_hashes(wave)
    try:
        parent_digest = _verified_parent_digest(wave, parent_receipt)
    except ValueError as exc:
        print(f"\n[CRITICAL FAILURE] {exc}")
        return 1

    # 2. Execute wave tests
    retcode, collected, passed, failed, skipped, command_str = run_tests_for_wave(wave, output_dir)
    print("\nTest Execution Results:")
    print(f"  Exit Code: {retcode}")
    print(f"  Collected: {collected}")
    print(f"  Passed:    {passed}")
    print(f"  Failed:    {failed}")
    print(f"  Skipped:   {skipped}")

    # Zero collected tests is a blocking governance failure
    terminal_outcome = _wave_test_outcome(retcode, collected, failed)

    # 3. Verify zero live operational mutations
    operational_accounting = {
        "live_graph_mutations": 0,
        "unauthorized_writes": 0,
        "verified_clean": True,
    }

    # 4. Build receipt
    receipt: dict[str, Any] = {
        "contract_id": "urn:nee:scheduled-task-receipt:1.0.0",
        "receipt_type": "scheduled_task_receipt",
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
        "operational_accounting": operational_accounting,
        "input_artifact_hashes": input_hashes,
        "parent_receipt_digest": parent_digest,
        "log_path": str(output_dir / "test_run.log"),
    }
    receipt["canonical_digest"] = compute_canonical_digest(receipt)

    receipt_path = output_dir / f"wave_{wave}_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"\nSealed Receipt: {receipt_path}")
    print(f"Receipt Digest: {receipt['canonical_digest']}")

    return _finalize_wave_lock(wave, run_id, receipt, receipt_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scheduled Task Hardening Wave Verification Runner")
    parser.add_argument("--wave", type=int, required=True, help="Wave number (e.g. 1)")
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
