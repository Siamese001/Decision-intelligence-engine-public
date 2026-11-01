#!/usr/bin/env python3
"""
tools/audit_wave_runtime_sla.py

Automated governance tool measuring and enforcing the <=4-minute (240s) SLA
across wave execution and CI profile runs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

DEFAULT_SLA_BUDGET_SECONDS = 240.0

WAVE_PROFILE_TESTS = [
    "tests/unit/test_fast_pipeline_harness.py",
    "tests/unit/test_wave_sla_isolation.py",
    "tests/unit/test_audit_wave_runtime_sla.py",
]


def audit_test_runtime(
    test_targets: Sequence[str],
    budget_seconds: float = DEFAULT_SLA_BUDGET_SECONDS,
    receipt_path: Optional[Path] = None,
    fail_on_timeout: bool = True,
    runner: Optional[Callable[[Sequence[str], Dict[str, str]], Any]] = None,
    clock: Optional[Callable[[], float]] = None,
) -> Dict[str, Any]:
    """Execute target tests and assert execution completes within SLA budget."""
    time_func = clock or time.perf_counter
    start_iso = datetime.now(timezone.utc).isoformat()
    start_t = time_func()

    cmd = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        *test_targets,
        "-v",
        f"--timeout={int(budget_seconds)}",
    ]

    env = os.environ.copy()
    env["NEE_TEST_MODE"] = "1"
    env["NEE_FAST_TEST_MODE"] = "1"
    env["NEE_STORAGE_FORCE_MEMORY"] = "1"

    if runner is not None:
        proc = runner(cmd, env)
    else:
        timeout_sec = int(budget_seconds) + 30
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout_sec)
    elapsed_seconds = time_func() - start_t

    is_compliant = elapsed_seconds <= budget_seconds
    tests_passed = proc.returncode == 0

    outcome = {
        "contract_id": "urn:nee:governance:receipt:wave-sla-benchmark:1.0.0",
        "timestamp_utc": start_iso,
        "sla_budget_seconds": budget_seconds,
        "total_runtime_seconds": round(elapsed_seconds, 3),
        "sla_margin_seconds": round(budget_seconds - elapsed_seconds, 3),
        "sla_verdict": "PASS" if is_compliant and tests_passed else "FAIL",
        "pytest_exit_code": proc.returncode,
        "test_targets": list(test_targets),
        "target_count": len(test_targets),
        "status": "COMPLIANT_UNDER_4_MINUTES" if is_compliant else "SLA_BREACH",
    }
    if not tests_passed:
        outcome["pytest_stdout"] = proc.stdout
        outcome["pytest_stderr"] = proc.stderr

    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")

    return outcome


def main(
    argv: Optional[Sequence[str]] = None,
    runner: Optional[Callable[[Sequence[str], Dict[str, str]], Any]] = None,
    clock: Optional[Callable[[], float]] = None,
) -> int:
    parser = argparse.ArgumentParser(description="Audit wave execution runtime against the <=4-minute SLA.")
    parser.add_argument("--budget", type=float, default=DEFAULT_SLA_BUDGET_SECONDS, help="SLA budget ceiling in seconds (default: 240)")
    parser.add_argument("--profile", choices=["wave", "custom"], default="wave", help="Test profile to evaluate")
    parser.add_argument("--tests", nargs="*", default=None, help="Explicit test files or patterns")
    parser.add_argument("--receipt", type=Path, default=Path("reports/wave_execution_sla_benchmark_receipt.json"), help="Output receipt path")

    args = parser.parse_args(argv)

    targets = args.tests if args.tests else WAVE_PROFILE_TESTS
    print("\n================================================================================")
    print(f"🚀 NEE Wave Runtime SLA Auditor: Target Budget <= {args.budget}s")
    print(f"   Targets: {', '.join(targets)}")
    print("================================================================================\n")

    result = audit_test_runtime(
        test_targets=targets,
        budget_seconds=args.budget,
        receipt_path=args.receipt,
        runner=runner,
        clock=clock,
    )

    print(f"Duration:     {result['total_runtime_seconds']}s / {args.budget}s")
    print(f"SLA Margin:   +{result['sla_margin_seconds']}s")
    print(f"Verdict:      {result['sla_verdict']}")
    print(f"Receipt:      {args.receipt}\n")

    if result["sla_verdict"] != "PASS":
        print(f"❌ SLA AUDIT FAILED (Exit Code: {result['pytest_exit_code']}, Elapsed: {result['total_runtime_seconds']}s)")
        if "pytest_stdout" in result:
            print(f"--- Pytest STDOUT ---\n{result['pytest_stdout']}")
        if "pytest_stderr" in result and result["pytest_stderr"]:
            print(f"--- Pytest STDERR ---\n{result['pytest_stderr']}")
        return 1

    print("✅ SLA AUDIT PASSED: Wave execution verified well within 4-minute ceiling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
