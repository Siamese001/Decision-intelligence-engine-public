"""Decision-to-table checks for concrete observer recommendations."""
from __future__ import annotations

import pytest

from tools.pr_governance_core.observer_actions import observer_next_action
from tools.pr_governance_core.queue_observer_table import render_observer_table


@pytest.mark.parametrize("facts,expected", [
    ({"dirty": True, "ahead": 1, "clean_merge": True, "open_waves": "1, 2", "readiness_receipt_pass": False}, "[COMMIT_FIRST]"),
    ({"dirty": False, "ahead": 1, "clean_merge": True, "open_waves": "1, 2", "readiness_receipt_pass": False}, "[CONTINUE_WAVES]"),
    ({"dirty": False, "ahead": 1, "clean_merge": False, "open_waves": "—"}, "[RESOLVE_CONFLICTS]"),
    ({"dirty": False, "ahead": None, "clean_merge": None, "open_waves": "—"}, "[INVESTIGATE]"),
    ({"dirty": False, "ahead": 1, "clean_merge": True, "open_waves": "—", "behind": 20}, "[REBASE]"),
    ({"dirty": False, "ahead": 1, "clean_merge": True, "open_waves": "—", "behind": 50, "diff_category": "DOCS_ONLY_UNSTARTED"}, "[CONFIRM_TOMBSTONE]"),
    ({"dirty": False, "ahead": 1, "clean_merge": True, "open_waves": "—", "behind": 50, "diff_category": "CODE_ACTIVE"}, "[REBASE]"),
    ({"dirty": False, "ahead": 1, "clean_merge": True, "open_waves": "—", "behind": 0, "diff_category": "EMPTY_STUB"}, "[CONFIRM_TOMBSTONE]"),
    ({"dirty": False, "ahead": 1, "clean_merge": True, "open_waves": "—", "behind": 0, "readiness_receipt_pass": False}, "[REVIEW]"),
    ({"dirty": False, "ahead": 1, "clean_merge": True, "open_waves": "—", "behind": 0, "readiness_receipt_pass": True}, "[MERGE]"),
    ({"tier": "D.1", "ahead": 0, "open_waves": "1, 2"}, "[CONFIRM_TOMBSTONE]"),
])
def test_first_action_from_live_facts(facts, expected):
    item = {"branch": "feat/example", "tier": "P3.1", **facts}
    assert observer_next_action(item).startswith(expected)


def test_dependency_issue_names_resolution_step():
    item = {"branch": "feat/child", "tier": "P2.1", "ahead": 2,
            "behind": 0, "clean_merge": True, "open_waves": "—"}
    assert observer_next_action(item, "Blocked by unmerged prerequisite 'feat/parent'").startswith("[RESOLVE_DEPENDENCY]")


def test_rendered_action_and_reason_keep_distinct_facts():
    item = {"branch": "feat/google-meetings-review", "tier": "P2.1", "sub_rank": "P2.1",
            "ahead": 1, "behind": 50, "clean_merge": True, "dirty": True,
            "open_waves": "1, 2, 3", "readiness_receipt_pass": False,
            "blocker": "Uncommitted changes; 50 behind; open waves 1, 2, 3; review unverified"}
    item["next_action"] = observer_next_action(item)
    row = render_observer_table([item], {"mode": "interactive"}).splitlines()[2]
    cells = [cell.strip() for cell in row.split("|")[1:-1]]
    assert len(cells) in (6, 10)
    assert cells[3] in ("**XS**", "**S**", "**M**", "**L**", "**XL**")
    action_cell = cells[-1]
    assert not action_cell.startswith("BLOCKED")



def test_decision_api_does_not_replace_first_step_with_blocked_status():
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

    snapshot = QueueSnapshot(branches=[BranchSnapshot(
        branch="feat/action-regression", head_sha="a" * 40, ahead=2, behind=50,
        clean_merge=True, is_dirty=True, open_waves="1, 2",
        readiness_receipt_pass=False,
    )])
    item = evaluate_queue_decisions(snapshot).branches[0]
    assert item["next_action"].startswith("[COMMIT_FIRST]")
    assert item["merge_eligibility"] != "ELIGIBLE"
    assert "review" in " ".join(item["eligibility_reasons"]).lower()
