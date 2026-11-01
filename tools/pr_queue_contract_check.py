#!/usr/bin/env python3
"""tools/pr_queue_contract_check.py

Receipt verification harness for the PR Queue Durable Prioritization Plan.
Verifies semantic guards, test collection parity, and runner contracts across waves 1–6.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) != sys.path[0]:
    sys.path.insert(0, str(ROOT))

from tools.pr_queue_contract_core.checks_wave1_2 import (
    check_collection_parity,
    check_concurrent_change_detection,
    check_diagnostic_ledger_view,
    check_empty_branch_authoring_status,
    check_failing_canary_contract,
    check_focused_regression_tests,
    check_fresh_inception_preservation,
    check_manifest_integrity,
    check_model_identity_progress_recovery,
    check_open_precedence_and_reopening,
    check_plan_isolation_and_no_collision,
    check_stale_plan_exclusion,
    check_worktree_union_and_edge_cases,
    check_zero_collection_fail_closed,
)
from tools.pr_queue_contract_core.checks_wave3_4 import (
    check_advisory_summary_read_only_isolation,
    check_changed_evidence_rank_transitions,
    check_cross_interface_parity_and_decision_api,
    check_cycle_detection_and_provisional,
    check_file_overlap_not_hard_dependency,
    check_measured_value_never_from_loc_or_regex,
    check_no_false_dependency_on_merged_or_zero_ahead,
    check_order_planner_simulation_error_uncertainty,
    check_override_expiry_and_invalidation,
    check_review_requalification_preserves_verified_progress,
    check_row_completeness_value_reason_blocker_action_evidence,
    check_stable_reruns_identical_decisions,
    check_topological_dependency_execution_order,
)
from tools.pr_queue_contract_core.checks_wave5 import (
    check_coverage_footer_fraction_accuracy,
    check_default_command_output_action_and_rationale,
    check_empirical_readiness_probe_measurements,
    check_inspectable_decision_view_capacity_20_plus,
    check_linked_detail_artifact_exists_and_valid,
)
from tools.pr_queue_contract_core.checks_wave6 import (
    check_observer_immutable_run_identity_and_isolated_output,
    check_observer_notification_deduplication_and_repeated_error_suppression,
    check_observer_prompt_and_runbook_artifacts,
    check_observer_shadow_scenarios_qualification,
    check_observer_single_run_locking_and_overlap_detection,
    check_observer_sleeping_host_and_missed_runs_handling,
)


def _get_git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return "UNKNOWN"


def run_checks(wave: int, py: str, timeout: int = 60) -> list[dict[str, Any]]:
    exp_cases = 11 if wave == 1 else (16 if wave == 2 else (22 if wave == 3 else (29 if wave == 4 else (34 if wave == 5 else 40))))
    base_checks = [
        check_manifest_integrity(expected_wave=wave),
        check_focused_regression_tests(py, timeout=timeout),
        check_collection_parity(py, expected_count=exp_cases),
        check_failing_canary_contract(py),
        check_zero_collection_fail_closed(py),
        check_stale_plan_exclusion(),
        check_empty_branch_authoring_status(),
        check_diagnostic_ledger_view(),
    ]
    if wave == 1:
        return base_checks

    wave2_checks = [
        check_model_identity_progress_recovery(),
        check_open_precedence_and_reopening(),
        check_plan_isolation_and_no_collision(),
        check_fresh_inception_preservation(),
        check_worktree_union_and_edge_cases(),
        check_concurrent_change_detection(),
    ]
    if wave == 2:
        return base_checks + wave2_checks

    wave3_checks = [
        check_topological_dependency_execution_order(),
        check_no_false_dependency_on_merged_or_zero_ahead(),
        check_file_overlap_not_hard_dependency(),
        check_cycle_detection_and_provisional(),
        check_order_planner_simulation_error_uncertainty(),
        check_cross_interface_parity_and_decision_api(),
    ]
    if wave == 3:
        return base_checks + wave2_checks + wave3_checks

    wave4_checks = [
        check_measured_value_never_from_loc_or_regex(),
        check_row_completeness_value_reason_blocker_action_evidence(),
        check_stable_reruns_identical_decisions(),
        check_changed_evidence_rank_transitions(),
        check_review_requalification_preserves_verified_progress(),
        check_override_expiry_and_invalidation(),
        check_advisory_summary_read_only_isolation(),
    ]
    if wave == 4:
        return base_checks + wave2_checks + wave3_checks + wave4_checks

    wave5_checks = [
        check_default_command_output_action_and_rationale(),
        check_inspectable_decision_view_capacity_20_plus(),
        check_linked_detail_artifact_exists_and_valid(),
        check_coverage_footer_fraction_accuracy(),
        check_empirical_readiness_probe_measurements(),
    ]
    if wave == 5:
        return base_checks + wave2_checks + wave3_checks + wave4_checks + wave5_checks

    wave6_checks = [
        check_observer_single_run_locking_and_overlap_detection(),
        check_observer_immutable_run_identity_and_isolated_output(),
        check_observer_notification_deduplication_and_repeated_error_suppression(),
        check_observer_sleeping_host_and_missed_runs_handling(),
        check_observer_shadow_scenarios_qualification(),
        check_observer_prompt_and_runbook_artifacts(),
    ]
    return base_checks + wave2_checks + wave3_checks + wave4_checks + wave5_checks + wave6_checks


def main() -> int:
    parser = argparse.ArgumentParser(description="PR Queue Contract Check Harness")
    parser.add_argument("--profile", choices=["legacy", "dual-mode-refactor"], default="legacy")
    parser.add_argument("--wave", type=int, default=6, choices=[1, 2, 3, 4, 5, 6], help="Wave number to verify (default: 6)")
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument("--parent", type=Path, default=None, help="Validated receipt from the immediately preceding wave")
    parser.add_argument("--timeout", type=int, default=60, help="Per-test timeout in seconds (default: 60)")
    args = parser.parse_args()

    if args.profile == "dual-mode-refactor":
        if args.receipt is None:
            parser.error("--receipt is required for dual-mode-refactor")
        from tools.pr_queue_contract_core.refactor_profile import run_wave

        exit_code, receipt = run_wave(args.wave, args.timeout, args.receipt, args.parent)
        print(json.dumps(receipt, indent=2))
        return exit_code

    receipt_path = args.receipt or (ROOT / "reports" / "pr_queue_recovery" / f"wave{args.wave}.json")
    py = sys.executable
    print(f"=== PR Queue Contract Check — Wave {args.wave} ===\nPython interpreter: {py}\nRepository root:    {ROOT}\nReceipt target:     {receipt_path}\n")
    checks = run_checks(args.wave, py, timeout=args.timeout)

    all_passed = True
    print(f"{'Check Name':<45} {'Status':<10} {'Details'}")
    print("-" * 90)
    for c in checks:
        status_label = "✅ PASS" if c["status"] == "PASS" else "❌ FAIL"
        details = c.get("details", c.get("error", ""))
        print(f"{c['name']:<45} {status_label:<10} {details}")
        if c["status"] != "PASS":
            all_passed = False

    receipt_data = {
        "receipt_version": "1.0.0",
        "plan_id": "docs_pr_queue_durable_prioritization_plan",
        "wave": args.wave,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": _get_git_sha(),
        "python_executable": py,
        "terminal_outcome": "PASS" if all_passed else "FAIL",
        "total_checks": len(checks),
        "passed_checks": sum(1 for c in checks if c["status"] == "PASS"),
        "failed_checks": sum(1 for c in checks if c["status"] != "PASS"),
        "checks": checks,
    }

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
    print(f"\nImmutable receipt sealed at: {receipt_path}")
    print(f"Terminal Outcome: {receipt_data['terminal_outcome']}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
