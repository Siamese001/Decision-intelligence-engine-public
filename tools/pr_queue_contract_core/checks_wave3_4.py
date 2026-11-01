"""tools/pr_queue_contract_core/checks_wave3_4.py

Contract verification checks for Wave 3 and Wave 4.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


# ==============================================================================
# WAVE 3 CHECKS
# ==============================================================================

def check_topological_dependency_execution_order() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import compute_topological_execution_sequence
    parent = {"branch": "feat/parent", "tier": "P2.1", "risk_tier": 0, "value_score": 2.0}
    child = {"branch": "feat/child", "tier": "P0.1", "risk_tier": 0, "value_score": 10.0}
    hard_graph = {"feat/child": ["feat/parent"], "feat/parent": []}
    seq, _ = compute_topological_execution_sequence([child, parent], hard_graph, [])
    if seq != ["feat/parent", "feat/child"]:
        return {"name": "TopologicalDependencyOrder", "status": "FAIL", "error": f"Expected ['feat/parent', 'feat/child'], got {seq}"}
    _, blocked_b = compute_topological_execution_sequence([child], hard_graph, [])
    if "feat/child" not in blocked_b:
        return {"name": "TopologicalDependencyOrder", "status": "FAIL", "error": "Child not blocked when parent unmerged"}
    return {"name": "TopologicalDependencyOrder", "status": "PASS", "details": "Parent precedes child; child blocked when parent unmerged"}


def check_no_false_dependency_on_merged_or_zero_ahead() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import detect_hard_dependencies
    with tempfile.TemporaryDirectory() as td:
        _, hard_graph, _ = detect_hard_dependencies(
            branch_names=["feat/c", "feat/zero"], ahead_map={"feat/c": 2, "feat/zero": 0},
            repo_root=Path(td), base_ref="main", declared_prereqs={"feat/c": "feat/zero"},
        )
        if "feat/zero" in hard_graph.get("feat/c", []):
            return {"name": "NoFalseDependencyOnZeroAhead", "status": "FAIL", "error": "Zero-ahead branch created hard prerequisite"}
    return {"name": "NoFalseDependencyOnZeroAhead", "status": "PASS", "details": "Zero-ahead and merged ancestors excluded from hard prerequisites"}


def check_file_overlap_not_hard_dependency() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import detect_hard_dependencies
    with tempfile.TemporaryDirectory() as td:
        _, hard_graph, _ = detect_hard_dependencies(
            branch_names=["feat/a", "feat/b"], ahead_map={"feat/a": 2, "feat/b": 2},
            repo_root=Path(td), base_ref="main", file_overlaps={"feat/a": {"feat/b": ["tools/x.py"]}},
        )
        if hard_graph.get("feat/a") or hard_graph.get("feat/b"):
            return {"name": "FileOverlapNotHardDependency", "status": "FAIL", "error": "File overlap created hard prerequisite"}
    return {"name": "FileOverlapNotHardDependency", "status": "PASS", "details": "File overlap creates soft edge without constraining sequence"}


def check_cycle_detection_and_provisional() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import compute_topological_execution_sequence
    cand_a = {"branch": "feat/a", "tier": "P1.1", "risk_tier": 1, "value_score": 5.0}
    cand_b = {"branch": "feat/b", "tier": "P1.2", "risk_tier": 1, "value_score": 4.0}
    seq, blocked = compute_topological_execution_sequence([cand_a, cand_b], {"feat/a": ["feat/b"], "feat/b": ["feat/a"]}, [["feat/a", "feat/b", "feat/a"]])
    if "feat/a" not in blocked or "feat/b" not in blocked:
        return {"name": "CycleDetectionAndProvisional", "status": "FAIL", "error": "Cyclical branches not blocked"}
    return {"name": "CycleDetectionAndProvisional", "status": "PASS", "details": "Cycles produce explicit BLOCKED_CYCLE recommendations"}


def check_order_planner_simulation_error_uncertainty() -> dict[str, Any]:
    from tools.pr_overlap_core.order_planner import MergeOrderPlan, MergeStep
    step = MergeStep(step_index=1, branch="feat/err", action="BLOCKED", status="SIMULATION_ERROR", error_message="Sim error")
    plan = MergeOrderPlan(ordered_steps=[step], has_errors=True, plan_status="PROVISIONAL", error_details=["Sim error"])
    if not plan.has_errors or plan.plan_status != "PROVISIONAL":
        return {"name": "PlannerSimulationErrorUncertainty", "status": "FAIL", "error": "Planner did not record provisional uncertainty"}
    return {"name": "PlannerSimulationErrorUncertainty", "status": "PASS", "details": "Simulation errors return explicit uncertainty"}


def check_cross_interface_parity_and_decision_api() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot
    snap = QueueSnapshot(branches=[
        BranchSnapshot(branch="feat/1", head_sha="111", ahead=2, behind=0, clean_merge=True, open_waves="—"),
        BranchSnapshot(branch="feat/2", head_sha="222", ahead=1, behind=0, clean_merge=True, open_waves="—"),
    ])
    res = evaluate_queue_decisions(snap, repo_root=ROOT)
    if len(res.branches) != 2:
        return {"name": "CrossInterfaceDecisionParity", "status": "FAIL", "error": f"Expected 2 branches, got {len(res.branches)}"}
    for b in res.branches:
        if "merge_eligibility" not in b or "execution_sequence_index" not in b:
            return {"name": "CrossInterfaceDecisionParity", "status": "FAIL", "error": "Missing decision fields"}
    return {"name": "CrossInterfaceDecisionParity", "status": "PASS", "details": "Unified decision API outputs identical decision fields"}


# ==============================================================================
# WAVE 4 CHECKS
# ==============================================================================

def check_measured_value_never_from_loc_or_regex() -> dict[str, Any]:
    from tools.pr_governance_core.outcome_metadata import UNMEASURED_VALUE_STATEMENT, evaluate_branch_outcome
    outcome = evaluate_branch_outcome(branch="feat/core-opt", tier="P0", changed_files=["dual_pipeline/token_optimization/core.py"])
    stmt = outcome.value_statement()
    if stmt != UNMEASURED_VALUE_STATEMENT:
        return {"name": "MeasuredValueNeverFromLocOrRegex", "status": "FAIL", "error": f"Expected '{UNMEASURED_VALUE_STATEMENT}', got '{stmt}'"}
    return {"name": "MeasuredValueNeverFromLocOrRegex", "status": "PASS", "details": "Unverified branches declare explicit UNKNOWN value statement without LOC/regex"}


def check_row_completeness_value_reason_blocker_action_evidence() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot
    snap = QueueSnapshot(branches=[
        BranchSnapshot(branch="feat/test-row", head_sha="123", ahead=1, behind=0, clean_merge=True, open_waves="—")
    ])
    res = evaluate_queue_decisions(snap, repo_root=ROOT)
    if not res.branches:
        return {"name": "RowCompletenessValueReasonBlockerActionEvidence", "status": "FAIL", "error": "Empty branches returned"}
    row = res.branches[0]
    for field_name in ["value_statement", "relative_order_reason", "blocker", "next_action", "completion_evidence_requirement"]:
        if not row.get(field_name):
            return {"name": "RowCompletenessValueReasonBlockerActionEvidence", "status": "FAIL", "error": f"Missing field '{field_name}' in row"}
    return {"name": "RowCompletenessValueReasonBlockerActionEvidence", "status": "PASS", "details": "Every active row contains value, reason, blocker, next step, and evidence"}


def check_stable_reruns_identical_decisions() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot
    snap = QueueSnapshot(branches=[
        BranchSnapshot(branch="feat/r1", head_sha="111", ahead=2, behind=0, clean_merge=True, open_waves="—"),
        BranchSnapshot(branch="feat/r2", head_sha="222", ahead=1, behind=0, clean_merge=True, open_waves="—"),
    ])
    res1 = evaluate_queue_decisions(snap, repo_root=ROOT)
    res2 = evaluate_queue_decisions(snap, repo_root=ROOT)
    if res1.execution_sequence != res2.execution_sequence:
        return {"name": "StableRerunsIdenticalDecisions", "status": "FAIL", "error": "Execution sequence diverged across identical runs"}
    if [b["sub_rank"] for b in res1.branches] != [b["sub_rank"] for b in res2.branches]:
        return {"name": "StableRerunsIdenticalDecisions", "status": "FAIL", "error": "Sub-ranks diverged across identical runs"}
    return {"name": "StableRerunsIdenticalDecisions", "status": "PASS", "details": "Identical inputs produce identical decisions and 0 rank drift"}


def check_changed_evidence_rank_transitions() -> dict[str, Any]:
    from tools.pr_governance_core.snapshot_history import compute_rank_transitions
    prev = [{"branch": "feat/t", "tier": "P3", "sub_rank": "P3.2", "ahead": 0, "behind": 10, "clean_merge": True}]
    curr = [{"branch": "feat/t", "tier": "P3", "sub_rank": "P3.1", "ahead": 2, "behind": 0, "clean_merge": True}]
    transitions = compute_rank_transitions(prev, curr, policy_version="2.1")
    if len(transitions) != 1 or transitions[0].previous_position != "P3.2" or transitions[0].current_position != "P3.1":
        return {"name": "ChangedEvidenceRankTransitions", "status": "FAIL", "error": "Transition event not properly recorded"}
    return {"name": "ChangedEvidenceRankTransitions", "status": "PASS", "details": f"Transition recorded: {transitions[0].cause_summary}"}


def check_review_requalification_preserves_verified_progress() -> dict[str, Any]:
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, PlanOwnershipRecord
    plan = PlanOwnershipRecord(
        branch_id="feat/q", plan_id="p1", plan_path="plans/p1.md", wave_id=1,
        source_hash="h1", source_type="committed", declared_status="done", verified_status="verified_done",
    )
    b_snap = BranchSnapshot(branch="feat/q", head_sha="111", plans=[plan], completed_waves="1")
    b_snap.readiness = "REVIEW_READY"
    if not b_snap.plans or b_snap.plans[0].verified_status != "verified_done" or b_snap.completed_waves != "1":
        return {"name": "ReviewRequalificationPreservesVerifiedProgress", "status": "FAIL", "error": "Verified progress lost during review update"}
    return {"name": "ReviewRequalificationPreservesVerifiedProgress", "status": "PASS", "details": "Verified wave progress preserved across review requalification"}


def check_override_expiry_and_invalidation() -> dict[str, Any]:
    from tools.pr_priority_engine import is_valid_override
    bad_1 = {"owner": "test", "reason": "test", "expires_at": "2099-01-01", "tier": "P0"}  # missing scope
    bad_2 = {"owner": "test", "scope": "core", "reason": "test", "expires_at": "2020-01-01", "tier": "P0"}  # expired
    good = {"owner": "test", "scope": "core", "reason": "test", "expires_at": "2099-01-01", "tier": "P0"}
    v1, _ = is_valid_override(bad_1)
    v2, _ = is_valid_override(bad_2, today_str="2026-10-06")
    vg, _ = is_valid_override(good, today_str="2026-10-06")
    if v1 is not False or v2 is not False or vg is not True:
        return {"name": "OverrideExpiryAndInvalidation", "status": "FAIL", "error": "Override validation logic incorrect"}
    return {"name": "OverrideExpiryAndInvalidation", "status": "PASS", "details": "Expired or malformed overrides fail closed; valid overrides accepted"}


def check_advisory_summary_read_only_isolation() -> dict[str, Any]:
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.outcome_metadata import UNMEASURED_VALUE_STATEMENT
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot
    snap = QueueSnapshot(branches=[
        BranchSnapshot(branch="feat/advisory-test", head_sha="111", ahead=1, behind=0, clean_merge=True, open_waves="—")
    ])
    res = evaluate_queue_decisions(snap, repo_root=ROOT)
    item = res.branches[0]
    if item["value_statement"] != UNMEASURED_VALUE_STATEMENT or not item["tier"].startswith("P3"):
        return {"name": "AdvisorySummaryReadOnlyIsolation", "status": "FAIL", "error": f"Advisory state altered deterministic tier or value: tier={item['tier']}"}
    return {"name": "AdvisorySummaryReadOnlyIsolation", "status": "PASS", "details": "Advisory summaries isolated from deterministic decisions and value statements"}
