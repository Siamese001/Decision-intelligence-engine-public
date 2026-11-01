#!/usr/bin/env python3
"""Bounded receipt runner for HITL autonomous resolution wave execution.

Enforces:
1. Manifest-bound test file scoping and rejection of ambient test modifications.
2. Verified parent wave receipt chaining before wave N execution.
3. Explicit disposable sandbox validation (no production output/ contamination).
4. Strict test criteria: positive nonzero collection, 0 failed, 0 errors, 0 skips, 0 unexpected xfails.
5. Immutable, exclusive receipt persistence under reports/hitl_resolution/<run_id>/.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.hitl_resolution_runner_core import (  # noqa: E402
    ExecutionResult,
    WaveReceipt,
    WaveSpec,
    compute_file_digest,
    compute_tree_digest,
    execute_wave_tests,
    persist_wave_receipt,
    validate_environment,
    validate_parent_receipt,
)
from tools.test_discovery_core.discovery import resolve_all_tests  # noqa: E402


def _check_ci_enrollment(test_files: List[str], repo_root: Path) -> Dict[str, Any]:
    """Verifies that declared wave test files resolve under test_discovery_core."""
    det, e2e, bge = resolve_all_tests(repo_root=repo_root)
    all_resolved = set(det + e2e + bge)

    enrolled = {}
    missing = []
    for tf in test_files:
        mod_name = tf.replace("/", ".").replace(".py", "")
        if mod_name in all_resolved:
            enrolled[tf] = "RESOLVED"
        else:
            missing.append(tf)

    return {
        "all_enrolled": len(missing) == 0,
        "enrolled_tests": enrolled,
        "missing_tests": missing,
        "total_active_ci_tests": len(all_resolved),
    }


def _load_and_validate_spec(
    spec_path: Path,
    plan_path: Path,
    wave: int,
) -> Tuple[Optional[WaveSpec], Optional[str]]:
    """Loads and validates wave specification and plan path existence."""
    if not spec_path.is_file():
        return None, f"Wave spec file not found: {spec_path}"

    try:
        spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
        spec = WaveSpec.from_dict(spec_data)
    except Exception as e:
        return None, f"Invalid wave spec JSON: {e}"

    if spec.wave != wave:
        return None, f"Spec wave ({spec.wave}) does not match requested wave ({wave})"

    if not plan_path.is_file():
        return None, f"Wave plan file not found: {plan_path}"

    return spec, None


def _evaluate_acceptance(exec_res: ExecutionResult, spec: WaveSpec) -> List[str]:
    """Checks execution metrics against acceptance criteria and returns list of defects."""
    defects = []
    if exec_res.exit_code != 0:
        defects.append(f"Subprocess exited with non-zero exit code: {exec_res.exit_code}")
    if exec_res.total_collected == 0:
        defects.append("Zero tests collected from declared wave manifests.")
    if exec_res.failed_count > 0:
        defects.append(f"{exec_res.failed_count} test(s) failed.")
    if exec_res.error_count > 0:
        defects.append(f"{exec_res.error_count} test error(s) encountered.")
    if exec_res.skipped_count > 0:
        defects.append(f"Unexpected skipped test(s) ({exec_res.skipped_count}). Wave criteria mandate 0 skips.")
    if exec_res.passed_count < spec.required_min_pass_count:
        defects.append(
            f"Passed count ({exec_res.passed_count}) is below required minimum ({spec.required_min_pass_count})."
        )
    return defects


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="HITL Resolution Wave Runner")
    parser.add_argument("--wave", type=int, required=True, help="Wave number (1-7)")
    parser.add_argument("--plan", type=Path, required=True, help="Path to wave plan markdown")
    parser.add_argument("--spec", type=Path, required=True, help="Path to wave spec JSON")
    parser.add_argument("--runtime-root", type=Path, required=True, help="Path to disposable test root")
    parser.add_argument("--receipt-root", type=Path, required=True, help="Path to run receipt directory")
    parser.add_argument("--timeout", type=int, default=60, help="Per-test timeout in seconds")
    parser.add_argument("--run-id", type=str, default="", help="Optional run identifier")

    args = parser.parse_args(argv)
    wave = args.wave
    plan_path = args.plan.resolve()
    spec_path = args.spec.resolve()
    runtime_root = args.runtime_root.resolve()
    receipt_root = args.receipt_root.resolve()
    run_id = args.run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    print("================================================================================")
    print(f"  HITL RESOLUTION WAVE RUNNER — WAVE {wave}")
    print(f"  Run ID: {run_id}")
    print(f"  Spec:   {spec_path}")
    print(f"  Plan:   {plan_path}")
    print("================================================================================")

    # 1. Spec & Plan Validation
    spec, spec_err = _load_and_validate_spec(spec_path, plan_path, wave)
    if spec_err or spec is None:
        print(f"🛑 [FAIL] {spec_err}", file=sys.stderr)
        return 1

    # 2. Environment Validation
    env_valid, env_reason = validate_environment(
        runtime_root=runtime_root,
        receipt_root=receipt_root,
        wave=wave,
        repo_root=REPO_ROOT,
    )
    if not env_valid:
        print(f"🛑 [FAIL] Environment validation error: {env_reason}", file=sys.stderr)
        return 1

    # 3. Predecessor Wave Receipt Check
    parent_valid, parent_digest, parent_reason = validate_parent_receipt(
        receipt_root=receipt_root,
        parent_wave=spec.parent_wave,
    )
    if not parent_valid:
        print(f"🛑 [FAIL] Predecessor receipt validation error: {parent_reason}", file=sys.stderr)
        return 1
    print(f"  ✔ Parent check: {parent_reason}")

    # 4. CI Discovery Check
    ci_info = _check_ci_enrollment(spec.test_files, REPO_ROOT)
    if not ci_info["all_enrolled"]:
        print(f"⚠️  [WARN] Unenrolled test files in CI: {ci_info['missing_tests']}")

    # 5. Execute Wave Tests
    start_time_iso = datetime.now(timezone.utc).isoformat()
    report_xml = receipt_root / f"wave_{wave}_junit_{run_id}.xml"

    print(f"  ⏳ Running declared wave tests: {', '.join(spec.test_files)}...")
    exec_result = execute_wave_tests(
        test_files=spec.test_files,
        repo_root=REPO_ROOT,
        report_path=report_xml,
        per_test_timeout=args.timeout,
        whole_run_timeout=spec.whole_run_timeout_seconds,
    )
    completed_time_iso = datetime.now(timezone.utc).isoformat()
    print(f"  ✔ Execution completed in {exec_result.duration_seconds}s (exit code: {exec_result.exit_code})")

    # 6. Evaluate Acceptance Criteria
    defects = _evaluate_acceptance(exec_result, spec)
    terminal_result = "COMPLETE" if not defects else "FAILED"

    # 7. Persist Wave Receipt
    receipt = WaveReceipt(
        schema_version="1.0.0",
        wave=wave,
        title=spec.title,
        run_id=run_id,
        plan_digest=compute_file_digest(plan_path),
        spec_digest=compute_file_digest(spec_path),
        code_tree_digest=compute_tree_digest(REPO_ROOT, spec.test_files),
        runtime_root=str(runtime_root),
        receipt_root=str(receipt_root),
        parent_receipt_digest=parent_digest,
        command=" ".join(sys.argv),
        started_at=start_time_iso,
        completed_at=completed_time_iso,
        terminal_result=terminal_result,
        exit_code=exec_result.exit_code,
        test_counts={
            "total_collected": exec_result.total_collected,
            "passed": exec_result.passed_count,
            "failed": exec_result.failed_count,
            "errors": exec_result.error_count,
            "skipped": exec_result.skipped_count,
            "xfail": exec_result.xfail_count,
            "xpass": exec_result.xpass_count,
        },
        test_ids=exec_result.test_ids,
        ci_profile_resolution=ci_info,
        output_digests={"junit_xml": compute_file_digest(report_xml) if report_xml.exists() else "0" * 64},
        static_gate_results={"failure_reasons": defects},
    )

    try:
        receipt_file, receipt_digest = persist_wave_receipt(receipt, receipt_root)
        print(f"  ✔ Wave receipt sealed at: {receipt_file} (digest: {receipt_digest[:16]}...)")
    except Exception as e:
        print(f"🛑 [FAIL] Failed persisting wave receipt: {e}", file=sys.stderr)
        return 1

    if terminal_result == "COMPLETE":
        print(f"\n✅ [WAVE {wave} COMPLETE] All criteria satisfied. Clearance granted for next wave.")
        return 0

    print(f"\n🛑 [WAVE {wave} FAILED] Wave criteria failed with {len(defects)} defect(s):", file=sys.stderr)
    for d in defects:
        print(f"  - {d}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
