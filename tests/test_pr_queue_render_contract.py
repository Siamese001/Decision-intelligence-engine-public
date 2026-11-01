"""Rendering contract checks for the six-column executive observer table."""
from __future__ import annotations

from tools.pr_governance_core.queue_observer_table import COLUMNS, render_observer_table
from tools.pr_queue_render_probe import generate_observer_table_html


def test_branch_column_wraps_and_hyperlinks_directory():
    branch = "feat/a-deliberately-long-branch-name-for-narrow-host-viewports"
    rendered = render_observer_table(
        [{"branch": branch, "sub_rank": "P0.1", "worktree_path": "/tmp/branch tree", "next_action": "Review", "value_statement": "Expected savings; magnitude unverified."}],
        {"mode": "interactive", "outcome": "COMPLETE", "notification": "SUPPRESSED"},
    )
    assert rendered.splitlines()[0].count("|") == len(COLUMNS) + 1
    # Ensure branch cell has min-width anchor and separator is expanded
    assert '<span style="display:inline-block;min-width:240px">' in rendered
    assert ":----------------------------------:|" in rendered.splitlines()[1]
    # Ensure no nowrap restriction on worktree cell
    assert "<span style=\"white-space:nowrap\">" not in rendered
    assert f"[{branch}](file:///tmp/branch tree)" in rendered
    assert len(rendered.splitlines()) == 3


def test_untrusted_cell_delimiter_cannot_inject_a_table_cell():
    rendered = render_observer_table(
        [{"branch": "feat/x", "sub_rank": "P3.1", "domain": "a|b", "next_action": "<script>|x"}],
        {"mode": "scheduled", "outcome": "COMPLETE", "notification": "SUPPRESSED"},
    )
    body = next(line for line in rendered.splitlines() if "feat/x" in line)
    assert "a&#124;b" in body
    assert body.count("|") == len(COLUMNS) + 1


def test_summary_and_recent_merges_share_the_single_table():
    rendered = render_observer_table([], {"mode": "scheduled", "outcome": "COMPLETE", "notification": "SUPPRESSED"}, recent_merges=[{"subject": "Merged fix"}])
    assert rendered.count("| Priority |") == 1
    assert "Blocked" in rendered or "Governance" in rendered


def test_browser_probe_uses_actual_observer_columns():
    html = generate_observer_table_html("feat/a-long-branch-name", 600)
    assert all(f"<th>{column}</th>" in html for column in COLUMNS)
    assert "overflow-x:auto" in html


def test_twenty_four_rows_keep_full_branch_names_and_all_six_cells():
    rows = [{"branch": f"feat/branch-{index}-long-identity", "sub_rank": f"P3.{index}", "next_action": "Review"} for index in range(1, 25)]
    rendered = render_observer_table(rows, {"mode": "interactive", "outcome": "COMPLETE"})
    body = [line for line in rendered.splitlines() if "feat/branch-" in line]
    assert len(body) == 24
    assert all(line.count("|") == len(COLUMNS) + 1 for line in body)
    assert "feat/branch-24-long-identity" in rendered


def test_complexity_column_renders_t_shirt_sizes():
    items = [
        {"branch": "feat/small", "sub_rank": "P0.1", "changed_files": 1, "ahead": 1},
        {"branch": "feat/med", "sub_rank": "P3.1", "changed_files": 12, "ahead": 5},
        {"branch": "feat/large", "sub_rank": "P2.1", "changed_files": 40, "ahead": 20},
    ]
    rendered = render_observer_table(items, {"mode": "interactive"})
    lines = [l for l in rendered.splitlines() if l.startswith("| **")]
    assert len(lines) == 3
    for line in lines:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        assert len(cells) == len(COLUMNS)
        assert cells[3] in ("**XS**", "**S**", "**M**", "**L**", "**XL**")


def test_recommended_disposition_and_immediate_action_alignment():
    items = [
        {"branch": "feat/clean", "sub_rank": "P1.1", "ahead": 1, "behind": 0, "clean_merge": True, "readiness_receipt_pass": True},
        {"branch": "feat/blocked", "sub_rank": "P2.1", "dirty": True, "ahead": 1},
        {"branch": "feat/diverged", "sub_rank": "P3.1", "behind": 12, "clean_merge": True},
    ]
    rendered = render_observer_table(items, {"mode": "interactive"})
    lines = [l for l in rendered.splitlines() if l.startswith("| **")]
    assert len(lines) == 3
    for line in lines:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        # Column 4: Recommended Disposition, Column 5: Immediate Blocker / Action
        assert cells[4] != ""
        assert cells[5] != ""
        assert not cells[5].startswith("BLOCKED")


def test_single_row_stash_collapse_invariant():
    items = [
        {"branch": "feat/active-1", "tier": "P1"},
        {"branch": "stash@{0}: WIP on feat/x", "tier": "W"},
        {"branch": "stash@{1}: WIP on feat/y", "tier": "W"},
        {"branch": "stub_session_123", "tier": "W"},
    ]
    rendered = render_observer_table(items, {"mode": "interactive"})
    lines = rendered.splitlines()
    data_lines = [l for l in lines if l.startswith("| **")]
    assert len(data_lines) == 1
    stash_lines = [l for l in lines if "collapsed" in l]
    assert len(stash_lines) == 1
    assert "2 git stash(es)" in stash_lines[0]
    assert "1 session stub(s)" in stash_lines[0]
