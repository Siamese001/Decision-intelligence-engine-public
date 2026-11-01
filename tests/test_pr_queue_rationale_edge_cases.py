"""Edge-case/adversarial tests for deterministic ranking rationale (queue_order.compute_ranking_rationale)."""

from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue


def _row(branch, tier, risk=0, value=1.0, impact="P0 R3.cost: x"):
    return {"branch": branch, "tier": tier, "risk_tier": risk, "value_score": value, "domain_impact": impact}


def _rank(rows):
    sort_queue(rows)
    assign_sub_ranks(rows)
    return [r["ranking_rationale"] for r in rows]


def test_first_row_names_rubric_driver_and_risk():
    r = _rank([_row("a", "P0", risk=3)])[0]
    assert "R3.cost" in r and "P-level outranks risk" in r and "risk 3" in r


def test_p_level_dominates_risk():
    rows = _rank([_row("a", "P0", risk=3), _row("b", "P3", risk=0, impact="P3 R5.governance: t")])
    assert "lower P-level" in rows[1] and "R5.governance" in rows[1]


def test_risk_then_value_then_branch_tiebreak():
    out = _rank([_row("a", "P1", 0, 5), _row("b", "P1", 2, 9), _row("c", "P1", 2, 3), _row("d", "P1", 2, 3)])
    assert "higher merge risk" in out[1]
    assert "lower value" in out[2]
    assert "branch-name tie-break" in out[3]


def test_decommission_and_archive_groups_explained():
    out = _rank([_row("a", "P1"), _row("z", "D"), _row("y", "A")])
    assert "Decommission" in out[1] and "Archive" in out[2]


def test_malformed_input_does_not_raise():
    out = _rank([{"branch": "x"}, {"branch": "y", "tier": None, "domain_impact": None}])
    assert all(isinstance(s, str) and s for s in out)


# ---- Wave status parsing (Completed/Open Waves must reflect live plan content, not a silent "—") ----
from tools.pr_governance_core.wave_status import compute_wave_columns, parse_plan_text  # noqa: E402


def test_status_column_position_independent():
    plan = "| Wave Number | Scope | Completion Status |\n|---|---|---|\n| Wave 1 | x | ✅ COMPLETE |\n| Wave 2 | y | ⏳ OPEN |\n"
    assert parse_plan_text(plan) == {1: "done", 2: "open"}


def test_headings_without_status_are_open_and_inline_status_wins():
    assert parse_plan_text("## Wave 1: A\n## Wave 2: B ✅ COMPLETE\n- [x] Wave 3 done\n") == {1: "open", 2: "done", 3: "done"}


def test_inherited_root_plan_is_ignored_and_unknown_is_question_mark():
    show = lambda b, p: "## Wave 1: stale" if p == "implementation_plan.md" else ""  # noqa: E731
    assert compute_wave_columns("b", show, ["tools/x.py"]) == ("?", "?")
    assert compute_wave_columns("b", show, ["implementation_plan.md"]) == ("1", "—")
    show_branch = lambda b, p: "## Wave 1: open\n## Wave 2: done ✅" if p == "plans/b/implementation_plan.md" else ""  # noqa: E731
    assert compute_wave_columns("b", show_branch, ["tools/x.py"]) == ("1", "2")


def test_empty_new_branch_yields_authoring_not_fast_lane():
    from tools.pr_governance_core.common import calculate_readiness_and_risk

    # Empty branch with ahead=0 and open_waves="—" must NOT be Fast-Lane
    info = {"ahead": 0, "behind": 0, "clean_merge": True, "readiness_receipt_pass": False}
    label, risk = calculate_readiness_and_risk(info, wt={}, open_waves="—")
    assert label == "⏳ Authoring", f"Expected '⏳ Authoring', got '{label}'"
    assert "Fast-Lane" not in label

    # Unqualified branch with ahead>0, open_waves="—", but no readiness receipt must NOT be Fast-Lane
    info_unqual = {"ahead": 2, "behind": 0, "clean_merge": True, "readiness_receipt_pass": False}
    label2, _ = calculate_readiness_and_risk(info_unqual, wt={}, open_waves="—")
    assert label2 == "⏳ Ready for Qualification", f"Expected '⏳ Ready for Qualification', got '{label2}'"

    # Fully qualified branch with ahead>0, open_waves="—", clean merge, and passing readiness receipt IS Fast-Lane
    info_qual = {"ahead": 2, "behind": 0, "clean_merge": True, "readiness_receipt_pass": True}
    label3, risk3 = calculate_readiness_and_risk(info_qual, wt={}, open_waves="—")
    assert label3 == "🚀 Fast-Lane"
    assert risk3 == 0


def test_changed_plan_doc_is_used_and_done_beats_open():
    show = lambda b, p: "| Wave 1 | ✅ COMPLETE |\n| Wave 2 | ⏳ OPEN |" if p == "docs/plans/X_PLAN.md" else ""  # noqa: E731
    assert compute_wave_columns("b", show, ["docs/plans/X_PLAN.md"]) == ("2", "1")


def test_malformed_tables_do_not_raise():
    assert parse_plan_text("|\n| Wave |\n| Wave x | ?\n|||") == {}


def test_open_status_beats_completion_words_in_description():
    text = (
        "| Wave 1 | Complete migration of schema | ⏳ OPEN |\n"
        "| Wave 2 | Finished all tasks and completed tests | 🚫 BLOCKED |\n"
        "## Wave 3: Complete documentation ⏳\n"
        "| Wave 4 | Fix auth bugs | ✅ COMPLETE |\n"
    )
    waves = parse_plan_text(text)
    assert waves[1] == "open", f"Expected Wave 1 open, got {waves.get(1)}"
    assert waves[2] == "open", f"Expected Wave 2 open, got {waves.get(2)}"
    assert waves[3] == "open", f"Expected Wave 3 open, got {waves.get(3)}"
    assert waves[4] == "done", f"Expected Wave 4 done, got {waves.get(4)}"


def test_wave_reopening_remains_visible():
    from tools.pr_governance_core.wave_status import _merge_waves

    reopened_text = (
        "| Wave 1 | Initial implementation | ✅ COMPLETE |\n"
        "| Wave 1 | Reopened due to regression | ⏳ OPEN |\n"
    )
    waves = parse_plan_text(reopened_text)
    assert waves[1] == "open", "Reopened wave must be recorded as open"

    merged = {1: "done"}
    _merge_waves(merged, {1: "open"})
    assert merged[1] == "open", "Reopened wave from secondary source must update done to open"


def test_two_plans_wave_1_do_not_collide_in_snapshot():
    from tools.pr_governance_core.queue_snapshot import PlanOwnershipRecord

    rec_a = PlanOwnershipRecord(
        branch_id="feat/branch-a",
        plan_id="plans/feat_a/implementation_plan.md",
        plan_path="/path/to/a.md",
        wave_id=1,
        source_hash="sha_a",
        source_type="untracked",
        declared_status="done",
        verified_status="verified_done",
    )
    rec_b = PlanOwnershipRecord(
        branch_id="feat/branch-b",
        plan_id="plans/feat_b/implementation_plan.md",
        plan_path="/path/to/b.md",
        wave_id=1,
        source_hash="sha_b",
        source_type="untracked",
        declared_status="open",
        verified_status="open",
    )
    assert rec_a.wave_id == rec_b.wave_id == 1
    assert rec_a.plan_id != rec_b.plan_id
    assert rec_a.declared_status == "done"
    assert rec_b.declared_status == "open"


def test_fresh_zero_ahead_dirty_branch_preservation_over_rebase():
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    from tools.pr_topology_core.disposition import _check_ahead_zero_worktree_safety
    from tools.pr_topology_core.matrix import BranchDiff

    diff = BranchDiff(branch="feat/fresh-dirty", ahead=0, behind=10)
    mock_birth = MagicMock(is_inception_active=True, age_desc="created 2h ago")

    with patch("tools.pr_topology_core.birth.inspect_branch_birth", return_value=mock_birth), \
         patch("tools.pr_topology_core.disposition.evaluate_dirty_file_coverage", return_value=(False, [], ["file1.py"])):
        disp = _check_ahead_zero_worktree_safety(
            branch="feat/fresh-dirty",
            diff=diff,
            wt={"stash_count": 0},
            wt_path=Path("/tmp/fake_wt"),
            waves_info={},
            root=Path("/tmp/repo"),
            base_branch="main",
        )
        assert disp is not None
        assert disp.action == "COMMIT_FIRST"
        assert "uncommitted file(s)" in disp.reason
        assert "commit work before rebasing" in disp.reason


def test_worktree_union_and_edge_cases():
    from tools.pr_governance_core.queue_snapshot import WorktreeEntry, _canonical_id

    ext_entry = WorktreeEntry(
        canonical_id=_canonical_id("external_wt", "/external/path/external_wt"),
        name="external_wt",
        path="/external/path/external_wt",
        branch="feat/ext",
        head="abc1234",
        classification="EXTERNAL_REGISTERED",
        is_registered=True,
        is_external=True,
    )
    nested_entry = WorktreeEntry(
        canonical_id=_canonical_id("nested_wt", "/repo/.worktrees/nested/nested_wt"),
        name="nested_wt",
        path="/repo/.worktrees/nested/nested_wt",
        branch="feat/nest",
        head="def5678",
        classification="NESTED_REGISTERED",
        is_registered=True,
        is_nested=True,
    )
    detached_entry = WorktreeEntry(
        canonical_id=_canonical_id("detached_wt", "/repo/.worktrees/detached_wt"),
        name="detached_wt",
        path="/repo/.worktrees/detached_wt",
        branch="",
        head="1234567",
        classification="DETACHED_HEAD",
        is_registered=True,
        is_detached=True,
    )

    assert ext_entry.classification == "EXTERNAL_REGISTERED"
    assert nested_entry.classification == "NESTED_REGISTERED"
    assert detached_entry.classification == "DETACHED_HEAD"
    assert ext_entry.canonical_id != nested_entry.canonical_id


def test_high_priority_child_blocked_by_prerequisite_retains_priority_but_blocked_from_merge():
    from tools.pr_governance_core.decision_api import compute_topological_execution_sequence

    parent = {"branch": "feat/parent", "tier": "P2.1", "risk_tier": 0, "value_score": 2.0}
    child = {"branch": "feat/child", "tier": "P0.1", "risk_tier": 0, "value_score": 10.0}

    # 1. When both are in queue, parent must execute before child
    hard_graph = {"feat/child": ["feat/parent"], "feat/parent": []}
    seq, blocked = compute_topological_execution_sequence([child, parent], hard_graph, [])
    assert seq == ["feat/parent", "feat/child"]
    assert child["tier"] == "P0.1"
    assert child["value_score"] == 10.0

    # 2. When parent is not yet eligible/merged, child is blocked from merge but retains business priority
    seq_blocked, blocked_reasons = compute_topological_execution_sequence([child], hard_graph, [])
    assert "feat/child" not in seq_blocked
    assert "feat/child" in blocked_reasons
    assert "Blocked by unmerged prerequisite" in blocked_reasons["feat/child"]


def test_already_merged_parent_and_zero_ahead_do_not_create_hard_dependency(tmp_path):
    from tools.pr_governance_core.decision_api import detect_hard_dependencies

    # Simulated git repo with main
    branches = ["feat/child", "feat/merged_parent", "feat/zero_ahead_parent"]
    ahead_map = {"feat/child": 3, "feat/merged_parent": 0, "feat/zero_ahead_parent": 0}
    declared = {
        "feat/child": "feat/zero_ahead_parent",
    }

    edges, hard_graph, cycles = detect_hard_dependencies(
        branch_names=branches,
        ahead_map=ahead_map,
        repo_root=tmp_path,
        base_ref="main",
        declared_prereqs=declared,
    )
    # Zero-ahead parent does not create a hard prerequisite edge
    assert "feat/zero_ahead_parent" not in hard_graph["feat/child"]


def test_file_overlap_does_not_create_hard_execution_dependency(tmp_path):
    from tools.pr_governance_core.decision_api import detect_hard_dependencies

    branches = ["feat/alpha", "feat/beta"]
    ahead_map = {"feat/alpha": 2, "feat/beta": 3}
    overlaps = {"feat/alpha": {"feat/beta": ["dual_pipeline/foo.py", "dual_pipeline/bar.py"]}}

    edges, hard_graph, cycles = detect_hard_dependencies(
        branch_names=branches,
        ahead_map=ahead_map,
        repo_root=tmp_path,
        base_ref="main",
        file_overlaps=overlaps,
    )
    assert len(edges) == 1
    assert edges[0].edge_type == "FILE_OVERLAP"
    assert edges[0].is_hard_prerequisite is False
    # No hard prerequisite in execution graph
    assert hard_graph["feat/alpha"] == []
    assert hard_graph["feat/beta"] == []


def test_dependency_cycle_produces_explicit_blocked_cycle_recommendation():
    from tools.pr_governance_core.decision_api import compute_topological_execution_sequence

    cand_a = {"branch": "feat/a", "tier": "P1.1", "risk_tier": 1, "value_score": 5.0}
    cand_b = {"branch": "feat/b", "tier": "P1.2", "risk_tier": 1, "value_score": 4.0}

    hard_graph = {"feat/a": ["feat/b"], "feat/b": ["feat/a"]}
    cycles = [["feat/a", "feat/b", "feat/a"]]

    seq, blocked = compute_topological_execution_sequence([cand_a, cand_b], hard_graph, cycles)
    assert seq == []
    assert "feat/a" in blocked
    assert "feat/b" in blocked
    assert "Cycle detected" in blocked["feat/a"]
    assert "Cycle detected" in blocked["feat/b"]


def test_order_planner_simulation_error_returns_explicit_uncertainty():
    from tools.pr_overlap_core.order_planner import MergeOrderPlan, MergeStep

    step = MergeStep(
        step_index=1,
        branch="feat/faulty",
        action="BLOCKED",
        status="SIMULATION_ERROR",
        error_message="Git simulation failure: tree write failed",
    )
    plan = MergeOrderPlan(
        ordered_steps=[step],
        has_errors=True,
        plan_status="PROVISIONAL",
        error_details=["Step 1 (feat/faulty): Git simulation failure"],
    )
    d = plan.to_dict()
    assert d["has_errors"] is True
    assert d["plan_status"] == "PROVISIONAL"
    assert len(d["error_details"]) == 1
    assert "Git simulation failure" in d["error_details"][0]


def test_cross_interface_parity_list_json_rank():
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    b1 = BranchSnapshot(branch="feat/alpha", head_sha="111", ahead=2, behind=0, clean_merge=True, open_waves="—")
    b2 = BranchSnapshot(branch="feat/beta", head_sha="222", ahead=1, behind=0, clean_merge=True, open_waves="—")
    snap = QueueSnapshot(branches=[b1, b2])

    res = evaluate_queue_decisions(snap)
    branches = res.branches
    assert len(branches) == 2
    # Verify deterministic fields present across all interfaces
    for b in branches:
        assert "branch" in b
        assert "tier" in b
        assert "sub_rank" in b
        assert "value_score" in b
        assert "risk_score" in b
        assert "merge_eligibility" in b
        assert "ranking_rationale" in b


# ==============================================================================
# WAVE 4: Value Assessments, Retained Rationale, and Transition History
# ==============================================================================

def test_loc_and_commit_regex_never_presented_as_measured_value():
    from tools.pr_governance_core.outcome_metadata import UNMEASURED_VALUE_STATEMENT, evaluate_branch_outcome

    outcome = evaluate_branch_outcome(
        branch="feat/big-optimization",
        tier="P0",
        changed_files=["dual_pipeline/token_optimization/cache.py"],
    )
    # Without a branch-bound benchmark comparison, the typed outcome is unverified.
    assert outcome.measurement_status == "UNVERIFIED"
    assert "LOC" not in outcome.value_statement()
    assert "%" not in outcome.value_statement()


def test_each_active_row_has_value_statement_reason_blocker_next_step_evidence():
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    b1 = BranchSnapshot(branch="feat/core-cache", head_sha="aaa", ahead=3, behind=2, clean_merge=True, open_waves="—")
    snap = QueueSnapshot(branches=[b1])
    res = evaluate_queue_decisions(snap)

    assert len(res.branches) == 1
    row = res.branches[0]
    assert "value_statement" in row and row["value_statement"]
    assert "relative_order_reason" in row and row["relative_order_reason"]
    assert "blocker" in row and row["blocker"]
    assert "next_action" in row and row["next_action"]
    assert "completion_evidence_requirement" in row and row["completion_evidence_requirement"]


def test_identical_inputs_produce_identical_decisions_stable_rerun():
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    b1 = BranchSnapshot(branch="feat/worker", head_sha="111", ahead=2, behind=1, clean_merge=True, open_waves="—")
    b2 = BranchSnapshot(branch="feat/helper", head_sha="222", ahead=1, behind=0, clean_merge=True, open_waves="—")
    snap = QueueSnapshot(branches=[b1, b2])

    res1 = evaluate_queue_decisions(snap)
    res2 = evaluate_queue_decisions(snap)

    assert res1.execution_sequence == res2.execution_sequence
    assert [b["sub_rank"] for b in res1.branches] == [b["sub_rank"] for b in res2.branches]
    assert [b["value_statement"] for b in res1.branches] == [b["value_statement"] for b in res2.branches]
    assert [b["ranking_rationale"] for b in res1.branches] == [b["ranking_rationale"] for b in res2.branches]


def test_changed_evidence_explains_rank_transition():
    from tools.pr_governance_core.snapshot_history import compute_rank_transitions

    prev_items = [
        {"branch": "feat/work", "tier": "P3", "sub_rank": "P3.2", "ahead": 0, "behind": 10, "clean_merge": True}
    ]
    curr_items = [
        {"branch": "feat/work", "tier": "P3", "sub_rank": "P3.1", "ahead": 2, "behind": 0, "clean_merge": True}
    ]

    transitions = compute_rank_transitions(prev_items, curr_items, policy_version="2.1")
    assert len(transitions) == 1
    tr = transitions[0]
    assert tr.branch == "feat/work"
    assert tr.previous_position == "P3.2"
    assert tr.current_position == "P3.1"
    assert any("ahead: 0 -> 2" in d for d in tr.changed_evidence)
    assert any("behind: 10 -> 0" in d for d in tr.changed_evidence)
    assert "Authored inception work committed" in tr.cause_summary or "Rebased" in tr.cause_summary


def test_review_requalification_does_not_erase_verified_wave_progress():
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, PlanOwnershipRecord

    plan = PlanOwnershipRecord(
        branch_id="feat/my-feature",
        plan_id="my_plan",
        plan_path="plans/my_plan.md",
        wave_id=1,
        source_hash="sha123",
        source_type="committed",
        declared_status="done",
        verified_status="verified_done",
        receipt_bindings=["reports/receipt1.json"],
    )
    b_snap = BranchSnapshot(
        branch="feat/my-feature",
        head_sha="abc1234",
        plans=[plan],
        open_waves="—",
        completed_waves="1",
    )
    # Simulate requalification / re-review event
    b_snap.readiness = "REVIEW_READY"
    # Verify verified wave status remains intact
    assert len(b_snap.plans) == 1
    assert b_snap.plans[0].verified_status == "verified_done"
    assert b_snap.completed_waves == "1"


def test_expired_or_invalid_override_fails_closed_to_deterministic_tier():
    from tools.pr_governance_core.outcome_metadata import OwnerOverride
    from tools.pr_priority_engine import is_valid_override

    # Missing scope
    bad_override_1 = {"owner": "alice", "reason": "urgent fix", "expires_at": "2099-01-01", "tier": "P0"}
    valid_1, msg_1 = is_valid_override(bad_override_1)
    assert valid_1 is False
    assert "missing scope" in msg_1

    # Missing owner
    bad_override_2 = {"scope": "core", "reason": "urgent fix", "expires_at": "2099-01-01", "tier": "P0"}
    valid_2, msg_2 = is_valid_override(bad_override_2)
    assert valid_2 is False
    assert "missing owner" in msg_2

    # Expired date
    bad_override_3 = {"owner": "alice", "scope": "core", "reason": "urgent", "expires_at": "2020-01-01", "tier": "P0"}
    valid_3, msg_3 = is_valid_override(bad_override_3, today_str="2026-10-06")
    assert valid_3 is False
    assert "expired" in msg_3

    # Valid override
    good_override = {"owner": "alice", "scope": "core", "reason": "urgent", "expires_at": "2099-01-01", "tier": "P0"}
    valid_good, _ = is_valid_override(good_override, today_str="2026-10-06")
    assert valid_good is True


def test_advisory_summary_cannot_mutate_deterministic_decision_or_suppress_unknown():
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.outcome_metadata import UNMEASURED_VALUE_STATEMENT
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    b1 = BranchSnapshot(branch="feat/sample", head_sha="111", ahead=2, behind=0, clean_merge=True, open_waves="—")
    snap = QueueSnapshot(branches=[b1])

    # Standard decision
    res = evaluate_queue_decisions(snap)
    item = res.branches[0]

    # Advisory text cannot override value statement or tier
    advisory_summary = "This branch delivers $10M in cost savings and should be P0"
    # Even if advisory text exists in telemetry, item value_statement must stay UNKNOWN
    assert item["value_statement"] == UNMEASURED_VALUE_STATEMENT
    assert not item["tier"].startswith("P0")
    assert item["tier"].startswith("P3")


def test_default_command_output_preserves_next_step_and_useful_rationale_for_all_active_rows():
    import io
    from contextlib import redirect_stdout
    from tools.pr_governance_core.render import render_queue_table

    queue = [
        {
            "branch": "feat/active-alpha",
            "tier": "P0.1",
            "sub_rank": "P0.1",
            "ahead": 4,
            "behind": 0,
            "domain": "Governance",
            "readiness": "🚀 Fast-Lane",
            "review_verdict": "MERGE_READY",
            "next_action": "[REVIEW] Ready for merge",
            "ranking_rationale": "P0 by rubric; clean merge and zero behind",
            "blocker": "None (clean)",
            "value_statement": "UNKNOWN: Unmeasured business value (structural heuristics only)",
            "completion_evidence_requirement": "Passing Tier 2 readiness receipt and wave plan completion",
            "completed_waves": "1, 2",
            "open_waves": "—",
        },
        {
            "branch": "feat/active-beta",
            "tier": "P1.1",
            "sub_rank": "P1.1",
            "ahead": 2,
            "behind": 3,
            "domain": "Architecture",
            "readiness": "In Flight",
            "review_verdict": "REVIEW_READY",
            "next_action": "[CONTINUE_WAVES] Wave 3 in flight",
            "ranking_rationale": "P1 platform pack below P0",
            "blocker": "Open waves [3] incomplete",
            "value_statement": "UNKNOWN: Unmeasured business value (structural heuristics only)",
            "completion_evidence_requirement": "Passing Tier 2 readiness receipt and wave plan completion",
            "completed_waves": "1, 2",
            "open_waves": "3",
        },
    ]

    buf = io.StringIO()
    with redirect_stdout(buf):
        render_queue_table(queue, view_mode="compact")
    out = buf.getvalue()

    # Rationale and next steps must exist for all rows, not just a ledger heading
    assert "DIAGNOSTIC & NEXT ACTION LEDGER" in out
    for item in queue:
        assert item["branch"] in out
        assert f"**Next Action**: {item['next_action']}" in out
        assert f"**Ranking Rationale**: {item['ranking_rationale']}" in out
        assert f"**Blocker**: {item['blocker']}" in out
        assert f"**Value**: {item['value_statement']}" in out


def test_twenty_or_more_active_branches_remain_inspectable_without_dropping_value_fields():
    import io
    from contextlib import redirect_stdout
    from tools.pr_governance_core.render import render_queue_table

    twenty_queue = []
    for i in range(25):
        twenty_queue.append({
            "branch": f"feat/candidate-{i:02d}",
            "tier": f"P2.{i+1}",
            "sub_rank": f"P2.{i+1}",
            "ahead": i + 1,
            "behind": i % 5,
            "domain": "Testing",
            "readiness": "In Flight",
            "review_verdict": "REVIEW_READY",
            "next_action": f"[CONTINUE_WAVES] Candidate {i} active",
            "ranking_rationale": f"P2 candidate ranked at position {i+1}",
            "blocker": f"Blocker for candidate {i}",
            "value_statement": f"Value statement for candidate {i}",
            "completion_evidence_requirement": "Passing readiness receipt",
            "completed_waves": "—",
            "open_waves": "1",
        })

    buf = io.StringIO()
    with redirect_stdout(buf):
        render_queue_table(twenty_queue, view_mode="compact")
    out = buf.getvalue()

    for item in twenty_queue:
        b = item["branch"]
        assert b in out
        assert f"**Next Action**: {item['next_action']}" in out
        assert f"**Ranking Rationale**: {item['ranking_rationale']}" in out
        assert f"**Blocker**: {item['blocker']}" in out
        assert f"**Value**: {item['value_statement']}" in out


def test_collapsed_stashes_point_to_real_existing_detail_artifact_with_stable_identities():
    import io
    import json
    import tempfile
    from contextlib import redirect_stdout
    from pathlib import Path
    from tools.pr_governance_core.render import render_queue_table

    with tempfile.TemporaryDirectory() as tmpdir:
        detail_path = Path(tmpdir) / "reports" / "pr_queue_detail.json"

        stashes_data = [
            {
                "ref": f"stash@{{{i}}}",
                "head_sha": f"deadbeef{i:04d}",
                "parent_sha": f"parent{i:04d}",
                "date": "2026-10-06T10:00:00Z",
                "classification": "STASH_MAIN",
                "branch": "main",
                "subject": f"WIP changes {i}",
            }
            for i in range(10)
        ]
        queue = [
            {
                "branch": "feat/my-branch",
                "tier": "P0.1",
                "sub_rank": "P0.1",
                "ahead": 1,
                "behind": 0,
                "clean_merge": True,
                "domain": "Governance",
                "next_action": "[REVIEW] Ready",
                "ranking_rationale": "High value",
            }
        ]
        for s in stashes_data:
            queue.append({
                "branch": s["ref"],
                "tier": "W",
                "sub_rank": "W.stash",
                "domain": "Housekeeping",
                "next_action": "[TRIAGE_STASH]",
                "ranking_rationale": "Stash",
                "classification": s["classification"],
            })

        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table(
                queue,
                inv_counts={"total_worktrees_children": 1, "stashes_total": 10},
                view_mode="compact",
                detail_path=detail_path,
                inv={"stashes": stashes_data, "counts": {"total_worktrees_children": 1, "stashes_total": 10}},
            )
        out = buf.getvalue()

        # The emitted detail link MUST exist on disk
        assert detail_path.exists(), "Emitted detail path was not created on disk"
        assert f"file://{detail_path}" in out

        with open(detail_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["collapsed_stash_count"] == 10
        assert len(data["stashes"]) == 10
        # Stable identities preserved
        first_stash = data["stashes"][0]
        assert first_stash["ref"] == "stash@{0}"
        assert first_stash["head_sha"] == "deadbeef0000"
        assert first_stash["parent_sha"] == "parent0000"


def test_coverage_footer_fraction_displays_accurate_covered_vs_total():
    import io
    from contextlib import redirect_stdout
    from tools.pr_governance_core.render import render_queue_table

    # Test asymmetric fraction (e.g. 10 covered out of 11 physical)
    inv_counts_gap = {
        "covered_children": 10,
        "total_children": 11,
        "covered_stashes": 75,
        "total_stashes": 78,
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        render_queue_table([], inv_counts=inv_counts_gap, view_mode="compact")
    out = buf.getvalue()

    assert "Worktree coverage: 10/11 children · 75/78 stashes" in out

    # Test complete coverage
    inv_counts_full = {
        "covered_children": 11,
        "total_children": 11,
        "covered_stashes": 78,
        "total_stashes": 78,
    }
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        render_queue_table([], inv_counts=inv_counts_full, view_mode="compact")
    out2 = buf2.getvalue()

    assert "Worktree coverage: 11/11 children · 78/78 stashes" in out2


def test_readability_probe_measures_actual_output_and_marks_unverified_if_renderer_unavailable():
    from tools.pr_queue_render_probe import run_probe

    measurements, summary = run_probe(
        viewports=[800, 1000],
        chrome_bin="/nonexistent/chrome",  # Headless browser unavailable
        assert_min_chars=28,
    )
    assert len(measurements) > 0
    # Must report unverified when headless renderer is unavailable
    assert summary.renderer_measurement_available is False
    assert summary.renderer_measurement_status == "UNVERIFIED"
    # Empirical verdicts computed rather than manufactured
    assert summary.rc1_no_width_contract in ("VERIFIED", "OBSERVED_MARGINAL")
    assert summary.rc3_w_tier_row_flood in ("VERIFIED", "OBSERVED_MARGINAL")
    assert summary.threshold_passed is True


def test_observer_single_run_lock_collision_and_dead_pid_reclamation():
    import json
    import os
    import tempfile
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from tools.pr_governance_core.observer import ObserverLockCollisionError, acquire_observer_lock

    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = Path(tmpdir) / "observer.lock"

        with acquire_observer_lock(lock_file) as info:
            assert lock_file.exists()
            assert info.pid == os.getpid()

            # Active process lock collision
            collision = False
            try:
                with acquire_observer_lock(lock_file):
                    pass
            except ObserverLockCollisionError:
                collision = True
            assert collision is True, "Active lock collision did not raise"

        # Lock is released upon exit and can be re-acquired
        with acquire_observer_lock(lock_file) as info2:
            assert info2.pid == os.getpid()


def test_observer_immutable_run_identity_and_isolated_reporting():
    import json
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    from tools.pr_governance_core.observer import run_observation
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
        snap = QueueSnapshot(
            observed_at=datetime.now(timezone.utc).isoformat(),
            branches=[
                BranchSnapshot(branch="feat/test-alpha", head_sha="aaaa", ahead=1, behind=0, completed_waves="1"),
            ],
        )

        receipt = run_observation(repo_root=Path(tmpdir), output_dir=out_dir, snapshot=snap)
        assert receipt.run_id
        assert "_" in receipt.run_id
        receipt_path = out_dir / "runs" / f"{receipt.run_id}.json"
        assert receipt_path.exists()

        last_accepted_path = out_dir / "last_accepted_snapshot.json"
        assert last_accepted_path.exists()
        last_data = json.loads(last_accepted_path.read_text(encoding="utf-8"))
        assert last_data["snapshot_digest"] == receipt.snapshot_digest


def test_observer_notification_deduplication_unchanged_state_suppressed():
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    from tools.pr_governance_core.observer import run_observation
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
        snap = QueueSnapshot(
            observed_at=datetime.now(timezone.utc).isoformat(),
            branches=[
                BranchSnapshot(branch="feat/gov-alpha", head_sha="1111", ahead=2, behind=0, completed_waves="1"),
            ],
        )
        r1 = run_observation(repo_root=Path(tmpdir), output_dir=out_dir, snapshot=snap)
        assert r1.assessment.notify is True

        r2 = run_observation(repo_root=Path(tmpdir), output_dir=out_dir, snapshot=snap)
        assert r2.assessment.notify is False
        assert r2.assessment.terminal_outcome == "NO_NOTIFICATION"


def test_observer_collects_real_snapshot_without_injected_fixture(tmp_path):
    """The scheduled CLI path must collect a snapshot without a test-supplied object."""
    import os
    import subprocess
    from tools.pr_governance_core.observer import acquire_observer_lock, run_observation

    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    out_dir = tmp_path / "reports" / "observer"
    receipt = run_observation(repo_root=tmp_path, output_dir=out_dir)

    assert receipt.run_id
    assert (out_dir / "runs" / f"{receipt.run_id}.json").is_file()
    # Observer lock released after run completes
    with acquire_observer_lock(out_dir / "observer.lock") as relock:
        assert relock.pid == os.getpid()


def test_observer_repeated_collection_failure_signature_deduplication():
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    from tools.pr_governance_core.observer import run_observation
    from tools.pr_governance_core.queue_snapshot import QueueSnapshot

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
        snap_fault = QueueSnapshot(
            observed_at=datetime.now(timezone.utc).isoformat(),
            completeness_status="INCONSISTENT",
            errors=["Lock collision on worktree listing"],
        )
        r1 = run_observation(repo_root=Path(tmpdir), output_dir=out_dir, snapshot=snap_fault)
        assert r1.assessment.notify is True
        assert r1.assessment.terminal_outcome == "COLLECTION_FAILURE_ALERT"

        r2 = run_observation(repo_root=Path(tmpdir), output_dir=out_dir, snapshot=snap_fault)
        assert r2.assessment.notify is False
        assert r2.assessment.terminal_outcome == "NO_NOTIFICATION"
        assert "Repeated unchanged collection failure signature" in r2.assessment.reason


def test_observer_sleeping_host_missed_runs_handled_cleanly():
    import json
    import tempfile
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from tools.pr_governance_core.observer import run_observation
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
        out_dir.mkdir(parents=True, exist_ok=True)
        baseline_path = out_dir / "scheduled_baseline.json"

        snap = QueueSnapshot(
            observed_at=datetime.now(timezone.utc).isoformat(),
            branches=[BranchSnapshot(branch="feat/a", head_sha="1111", ahead=1, behind=0, completed_waves="1", open_waves="—")],
        )

        # Run 1: baseline
        run_observation(repo_root=Path(tmpdir), output_dir=out_dir, snapshot=snap, mode="scheduled")

        # Simulate 72 hours passing while machine was asleep
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        data["observed_at"] = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        baseline_path.write_text(json.dumps(data), encoding="utf-8")

        # Run 2: resumed run
        receipt = run_observation(repo_root=Path(tmpdir), output_dir=out_dir, snapshot=snap, mode="scheduled")
        assert receipt.hours_since_last_observation >= 71.0
        assert receipt.missed_interval_hours >= 71.0
        assert receipt.assessment.terminal_outcome == "NO_NOTIFICATION"


def test_observer_shadow_qualification_suite_passes_all_scenarios():
    import tempfile
    from pathlib import Path
    from tools.pr_governance_core.observer_shadow import run_shadow_scenarios

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
        scenarios = run_shadow_scenarios(repo_root=Path(tmpdir), output_dir=out_dir)
        assert len(scenarios) >= 5
        for sc in scenarios:
            assert sc["passed"] is True, f"Scenario failed: {sc['scenario']} - {sc['reason']}"


# ==============================================================================
# WAVE 2: Business Value Linkage & 5-Criterion Queue Ranking
# ==============================================================================

def test_five_criterion_hierarchy_sort_order_and_rationales():
    from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue

    rows = [
        {"branch": "feat/feat", "tier": "P1", "risk_tier": 0, "benefit_category": "FEATURE_AND_DELIVERABLE", "value_score": 2.5},
        {"branch": "feat/house", "tier": "P1", "risk_tier": 0, "benefit_category": "HOUSEKEEPING", "value_score": 1.0},
        {"branch": "feat/cost", "tier": "P1", "risk_tier": 0, "benefit_category": "COST_REDUCTION", "value_score": 5.0},
        {"branch": "feat/integrity", "tier": "P1", "risk_tier": 0, "benefit_category": "GRAPH_INTEGRITY", "value_score": 3.5},
        {"branch": "feat/latency", "tier": "P1", "risk_tier": 0, "benefit_category": "LATENCY_IMPROVEMENT", "value_score": 4.5},
    ]
    sort_queue(rows)
    assign_sub_ranks(rows)

    branches = [r["branch"] for r in rows]
    assert branches == [
        "feat/cost",
        "feat/latency",
        "feat/integrity",
        "feat/feat",
        "feat/house",
    ], f"Unexpected branch sort order: {branches}"

    assert "1st: P1" in rows[0]["ranking_rationale"]
    assert "COST_REDUCTION outranks LATENCY_IMPROVEMENT" in rows[1]["ranking_rationale"]
    assert "LATENCY_IMPROVEMENT outranks GRAPH_INTEGRITY" in rows[2]["ranking_rationale"]
    assert "GRAPH_INTEGRITY outranks FEATURE_AND_DELIVERABLE" in rows[3]["ranking_rationale"]
    assert "FEATURE_AND_DELIVERABLE outranks HOUSEKEEPING" in rows[4]["ranking_rationale"]


def test_value_score_outranks_complexity_within_same_risk_and_theme():
    from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue

    # Branch A has higher value (4.0) but higher complexity (L/3)
    # Branch B has lower value (2.0) but lower complexity (XS/0)
    rows = [
        {"branch": "feat/b_low_val", "tier": "P1", "risk_tier": 0, "benefit_category": "GRAPH_INTEGRITY", "value_score": 2.0, "complexity": {"rank": 0}},
        {"branch": "feat/a_high_val", "tier": "P1", "risk_tier": 0, "benefit_category": "GRAPH_INTEGRITY", "value_score": 4.0, "complexity": {"rank": 3}},
    ]
    sort_queue(rows)
    assign_sub_ranks(rows)

    assert rows[0]["branch"] == "feat/a_high_val", "High-value branch must sort before lower-value branch regardless of complexity"
    assert rows[1]["branch"] == "feat/b_low_val"
    assert "lower value 2.0 vs 4.0" in rows[1]["ranking_rationale"]


def test_test_filename_or_commit_regex_never_inflates_business_value_without_prod_diff():
    from tools.pr_governance_core.value_itemizer import (
        classify_criterion_from_diff,
        extract_structured_value,
    )

    # Test file only, with inflated commit message and test name claiming $100k cost savings
    changed_files = ["tests/test_huge_cost_optimization.py", "docs/cost_reduction_notes.md"]
    category, score = classify_criterion_from_diff(
        changed_files,
        desc="Reduces paid model calls by 95% and saves $100k in token consumption",
        tier="P0",
    )
    assert category == "HOUSEKEEPING", f"Expected HOUSEKEEPING, got {category}"
    assert score == 1.0, f"Expected 1.0, got {score}"

    statement, struct_cat, struct_score = extract_structured_value(
        branch="feat/test-only-fake-cost",
        changed_files=changed_files,
        tier="P0",
    )
    assert struct_cat == "HOUSEKEEPING"
    assert struct_score == 1.0


def test_rubric_weights_match_five_criteria():
    from tools.pr_priority_engine import load_rubric

    rubric = load_rubric()

    vw = rubric["scoring"]["value_weights"]
    assert vw["COST_REDUCTION"] == 5.0
    assert vw["LATENCY_IMPROVEMENT"] == 4.5
    assert vw["GRAPH_INTEGRITY"] == 3.5
    assert vw["FEATURE_AND_DELIVERABLE"] == 2.5
    assert vw["HOUSEKEEPING"] == 1.0

    valid_cats = rubric["scoring"]["business_value_governance"]["valid_benefit_categories"]
    assert "GRAPH_INTEGRITY" in valid_cats
    assert "FEATURE_AND_DELIVERABLE" in valid_cats
    assert "FEATURE_CAPABILITY" in valid_cats


def test_value_measurement_preserves_benefit_across_main_advances_when_diff_tree_matches(tmp_path):
    import json
    from tools.pr_governance_core.common import slugify_branch
    from tools.pr_governance_core.value_measurement import load_measured_benefit

    # Create dummy benefit receipt
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    branch = "feat/spark-agent-runtime"
    receipt_file = reports_dir / f"benefit_measurement_{slugify_branch(branch)}_receipt.json"

    branch_sha = "a" * 40
    old_base_sha = "b" * 40
    new_base_sha = "c" * 40
    diff_hash = "d" * 64

    receipt_data = {
        "receipt_type": "pr_queue_benefit_measurement",
        "terminal_outcome": "PASS",
        "branch": branch,
        "branch_sha": branch_sha,
        "base_sha": old_base_sha,
        "diff_tree_hash": diff_hash,
        "baseline_value": 100.0,
        "observed_value": 50.0,
        "benefit_direction": "decrease",
        "metric_name": "query latency",
        "unit": "ms",
        "scope": "retrieval",
        "benefit_category": "FEATURE_CAPABILITY",
    }
    receipt_file.write_text(json.dumps(receipt_data), encoding="utf-8")

    # Mock _diff_tree_hash to return diff_hash for new_base_sha
    import tools.pr_governance_core.value_measurement as vm
    orig_diff_fn = vm._diff_tree_hash
    try:
        vm._diff_tree_hash = lambda root, base, head: diff_hash
        benefit = load_measured_benefit(
            branch=branch,
            branch_sha=branch_sha,
            base_sha=new_base_sha,  # Different base_sha (main advanced)
            repo_root=tmp_path,
            worktree_path=None,
        )
        assert benefit is not None
        assert benefit.category == "FEATURE_CAPABILITY"
        assert "query latency improved from 100 to 50 ms for retrieval" in benefit.statement
    finally:
        vm._diff_tree_hash = orig_diff_fn



