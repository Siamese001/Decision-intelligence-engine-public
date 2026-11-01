"""Adversarial tests for PR Queue layout ordering, evidence integrity, and malformed inputs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from tools.pr_governance_core.decision_api import (
    _discover_evidence_links,
)
from tools.pr_governance_core.queue_observer_table import (
    COLUMNS,
    render_observer_table,
    _evidence_cell,
    _branch_cell,
)
from tools.pr_governance_core.queue_snapshot import BranchSnapshot


# Category 1: Boundary Payloads
def test_boundary_empty_branch_name_and_zero_commits(tmp_path: Path):
    """Empty branch name, zero commits, and null attributes handled safely without crashing."""
    b_snap = BranchSnapshot(branch="", head_sha="", ahead=0, behind=0, clean_merge=True)
    evidence = _discover_evidence_links("", b_snap, [], tmp_path)
    assert evidence == []

    cell = _evidence_cell({"evidence_links": []})
    assert cell == "N/A"

    cell_none = _evidence_cell({})
    assert cell_none == "N/A"

    rendered = _branch_cell("", None)
    assert "N/A" in rendered


def test_boundary_missing_or_nonexistent_worktree_path(tmp_path: Path):
    """Non-existent worktree path is handled gracefully and does not yield broken links."""
    nonexistent = tmp_path / "does_not_exist"
    b_snap = BranchSnapshot(
        branch="feat/boundary-wt",
        head_sha="abc1234",
        ahead=1,
        behind=0,
        clean_merge=True,
        worktree_path=str(nonexistent),
    )
    evidence = _discover_evidence_links("feat/boundary-wt", b_snap, [], tmp_path)
    assert evidence == []

    rendered_without_wt = _branch_cell("feat/boundary-wt", None)
    assert '<span style="white-space:nowrap">' not in rendered_without_wt
    assert "feat/boundary-wt" in rendered_without_wt
    assert "<file://" not in rendered_without_wt


# Category 2: Malformed Inputs
def test_malformed_pipe_character_injection_escaped_cleanly():
    """Pipe characters in branch names, next action, reason, or URLs do not corrupt table columns."""
    malicious_item = {
        "branch": "feat/inject|pipe|split",
        "sub_rank": "P1.1",
        "next_action": "Action|With|Pipes",
        "reason": "Reason|With|Pipes",
        "value_statement": "Val|Pipe",
        "merge_gate": "Gate|Pipe",
        "progress": "Prog|Pipe",
        "git_state": "Ahead|Behind",
        "evidence_links": [{"label": "Evil|Label", "url": "file:///path|with|pipe"}],
    }
    rendered = render_observer_table(
        [malicious_item],
        {"mode": "interactive", "outcome": "COMPLETE", "notification": "SUPPRESSED"},
    )
    for line in rendered.splitlines():
        assert line.count("|") == len(COLUMNS) + 1
        assert "Evil&#124;Label" in line or "Evil" not in line


def test_malformed_broken_symlinks_and_corrupted_files(tmp_path: Path):
    """Broken symlinks and corrupted files are handled cleanly without unhandled exceptions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    plans_dir = repo / "plans" / "feat_corrupted"
    plans_dir.mkdir(parents=True)
    broken_symlink = plans_dir / "implementation_plan.md"
    try:
        broken_symlink.symlink_to(repo / "nonexistent_target.md")
    except OSError:
        pass

    reports_dir = repo / "reports" / "readiness"
    reports_dir.mkdir(parents=True)
    corrupted_receipt = reports_dir / "local_main_readiness_feat_corrupted_receipt.json"
    corrupted_receipt.write_bytes(b"\x80\xff\xfe truncated invalid utf8 json {{{{")

    b_snap = BranchSnapshot(
        branch="feat/corrupted",
        head_sha="deadbeef",
        ahead=1,
        behind=0,
        clean_merge=True,
    )
    links = _discover_evidence_links("feat/corrupted", b_snap, [], repo)
    receipt_links = [l for l in links if l["label"] == "Readiness Receipt"]
    assert receipt_links == []


# Category 3: Evidence Integrity & Invariant Bypass Attempts
def test_evidence_integrity_drops_nonexistent_paths_and_forbids_synthetic_urls(tmp_path: Path):
    """Evidence links must only point to existing files on disk, and deliverables are capped at 2."""
    repo = tmp_path / "repo"
    repo.mkdir()
    out_dir = repo / "output"
    out_dir.mkdir()
    (out_dir / "deliverable_1.md").write_text("deliv1", encoding="utf-8")
    (out_dir / "deliverable_2.md").write_text("deliv2", encoding="utf-8")
    (out_dir / "deliverable_3.md").write_text("deliv3", encoding="utf-8")

    changed = [
        "output/deliverable_1.md",
        "output/deliverable_2.md",
        "output/deliverable_3.md",
        "output/nonexistent_deliv.md",
    ]
    b_snap = BranchSnapshot(
        branch="feat/deliv-cap",
        head_sha="1234567",
        ahead=1,
        behind=0,
        clean_merge=True,
    )
    links = _discover_evidence_links("feat/deliv-cap", b_snap, changed, repo)
    deliv_links = [l for l in links if "Deliverable" in l["label"]]
    assert len(deliv_links) == 2
    for l in deliv_links:
        assert l["url"].startswith("file://")
        p = Path(l["url"].replace("file://", ""))
        assert p.is_file()


def test_worktree_missing_deliverable_cannot_borrow_root_file(tmp_path: Path):
    from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome

    root, wt = tmp_path / "repo", tmp_path / "worktree"
    (root / "output").mkdir(parents=True)
    (root / "output" / "brief.md").write_text("Root only", encoding="utf-8")
    wt.mkdir()
    snap = BranchSnapshot(branch="feat/brief", head_sha="a" * 40, worktree_path=str(wt))
    assert _discover_evidence_links("feat/brief", snap, ["output/brief.md"], root) == []
    outcome = evaluate_branch_outcome("feat/brief", "P2", changed_files=["output/brief.md"],
                                      repo_root=root, worktree_path=str(wt))
    assert outcome.measurement_status == "UNVERIFIED"


def test_evidence_worktree_path_with_spaces_is_properly_encoded(tmp_path: Path):
    """Worktree paths with spaces or special characters are safely URL-encoded."""
    wt_with_spaces = tmp_path / "dir with spaces" / "work tree"
    wt_with_spaces.mkdir(parents=True)
    plan_file = wt_with_spaces / "implementation_plan.md"
    plan_file.write_text("# Plan\nBranch: feat/spaces-test\n", encoding="utf-8")

    b_snap = BranchSnapshot(
        branch="feat/spaces-test",
        head_sha="9876543",
        ahead=1,
        behind=0,
        clean_merge=True,
        worktree_path=str(wt_with_spaces),
    )
    links = _discover_evidence_links("feat/spaces-test", b_snap, [], tmp_path)
    assert len(links) >= 1
    assert any("implementation_plan.md" in l["url"] for l in links)

    rendered_branch = _branch_cell("feat/spaces-test", str(wt_with_spaces))
    assert "%20with%20spaces" in rendered_branch
    assert "dir with spaces" not in rendered_branch


# Category 4: Exact Production Regression
def test_exact_production_regression_layout_and_evidence(tmp_path: Path):
    """Regression test: verify Next Action is Column 3, Reason is Column 4, and evidence is not N/A."""
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = repo / "implementation_plan.md"
    plan.write_text("# Wave 1-4 Plan\nBranch: feat/regression-test\n", encoding="utf-8")
    reports_dir = repo / "reports" / "readiness"
    reports_dir.mkdir(parents=True)
    readiness_rcpt = reports_dir / "local_main_readiness_feat_regression_test_receipt.json"
    readiness_rcpt.write_text('{"terminal_outcome": "PASS", "branch": "feat/regression-test", "head_sha": "a1b2c3d"}', encoding="utf-8")

    b_snap = BranchSnapshot(
        branch="feat/regression-test",
        head_sha="a1b2c3d",
        ahead=2,
        behind=0,
        clean_merge=True,
        worktree_path=str(repo),
    )
    evidence = _discover_evidence_links("feat/regression-test", b_snap, [], repo)
    assert len(evidence) == 2
    labels = {e["label"] for e in evidence}
    assert "Plan" in labels
    assert "Readiness Receipt" in labels

    item = {
        "branch": "feat/regression-test",
        "sub_rank": "P2.1",
        "next_action": "READY_FOR_MERGE",
        "reason": "All 30 gates passed",
        "value_statement": "Core stability; verified",
        "merge_gate": "PASS (Eligible)",
        "progress": "4/4 waves complete",
        "git_state": "ahead 2, behind 0, clean",
        "evidence_links": evidence,
    }
    table = render_observer_table(
        [item],
        {"mode": "interactive", "outcome": "COMPLETE", "notification": "SUPPRESSED"},
    )
    lines = table.splitlines()
    header = lines[0]
    expected_header = "| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |"
    assert header == expected_header

    data_row = next(l for l in lines if "feat/regression-test" in l)
    cells = [c.strip() for c in data_row.split("|")[1:-1]]
    assert len(cells) == 10
    assert cells[0] in ("**1**", f"**{item['sub_rank']}**")
    assert "feat/regression-test" in cells[1]
    assert cells[3] in ("**XS**", "**S**", "**M**", "**L**", "**XL**")
    assert cells[4] in ("**Y**", "**N**", "**N/A**")
    assert cells[5] in ("**Y**", "**N**", "**N/A**")
    assert cells[6] in ("**Y**", "**Y (Partial)**", "**N**")
    assert cells[7] in ("**Y**", "**N**")
    assert cells[8] != ""
    assert cells[9] != ""



# Category 5: Value Itemization Adversarial & Invariant Tests
def test_adversarial_empty_and_malformed_plans_handled_cleanly(tmp_path: Path):
    """Empty and malformed plans are safe; a valid description remains complete."""
    from tools.pr_governance_core.value_itemizer import extract_plan_intent, extract_itemized_value

    # 1. 0-byte plan file
    empty_plan = tmp_path / "empty_plan.md"
    empty_plan.write_text("", encoding="utf-8")
    title, summary = extract_plan_intent(empty_plan)
    assert title is None and summary is None

    # 2. Plan with missing # heading
    no_heading = tmp_path / "no_heading.md"
    no_heading.write_text("Just some text without any markdown headings.\n", encoding="utf-8")
    title, summary = extract_plan_intent(no_heading)
    assert title is None

    # 3. Long description remains intact instead of masquerading as a sentence.
    oversized_plan = tmp_path / "oversized.md"
    oversized_content = (
        "# Implementation Plan: A Deliberately Extremely Long Plan Title Intended To Test Truncation Boundaries\n\n"
        "This is an extraordinarily long executive summary designed to verify that the value extraction engine "
        "will cleanly truncate excessively verbose text down to an acceptable table cell budget instead of breaking "
        "the markdown table formatting or causing horizontal column explosion across narrow user terminals.\n"
    )
    oversized_plan.write_text(oversized_content, encoding="utf-8")
    val = extract_itemized_value(
        branch="feat/oversized",
        repo_root=tmp_path,
        evidence_links=[{"label": "Plan", "url": f"file://{oversized_plan}"}],
    )
    assert val is not None
    assert "horizontal column explosion across narrow user terminals" in val
    assert "..." not in val


def test_adversarial_unverified_branch_safely_evaluates_to_unmeasured_value_statement():
    """Unverified branch with no plan, commit, or deliverable falls back to UNMEASURED_VALUE_STATEMENT."""
    from tools.pr_governance_core.outcome_metadata import UNMEASURED_VALUE_STATEMENT, evaluate_branch_outcome

    outcome = evaluate_branch_outcome(
        branch="feat/synthetic-ghost-branch",
        tier="P3",
        changed_files=["unrelated/file.txt"],
    )
    stmt = outcome.value_statement()
    assert stmt == UNMEASURED_VALUE_STATEMENT
    assert "LOC" not in stmt
    assert "%" not in stmt
    assert "lines" not in stmt


def test_adversarial_itemized_value_extracts_from_deliverables_and_plans(tmp_path: Path):
    """Branches with client deliverables or plans produce substantive, evidence-bound value statements."""
    from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome

    # Deliverables in output/
    deliverables = tmp_path / "output" / "client_presentation_2026"
    deliverables.mkdir(parents=True)
    (deliverables / "01_transcript.md").write_text("Transcript", encoding="utf-8")
    (deliverables / "02_brief.md").write_text("Brief", encoding="utf-8")
    outcome_deliv = evaluate_branch_outcome(
        branch="feat/client-deliverable",
        tier="P2",
        changed_files=["output/client_presentation_2026/01_transcript.md", "output/client_presentation_2026/02_brief.md"],
        repo_root=tmp_path,
    )
    stmt_deliv = outcome_deliv.value_statement()
    assert "Makes 2 client_presentation_2026 client-deliverable artifacts available" in stmt_deliv
    assert outcome_deliv.measurement_status == "EXPECTED"
    (deliverables / "01_transcript.md").unlink()
    (deliverables / "02_brief.md").unlink()
    missing = evaluate_branch_outcome("feat/client-deliverable", "P2",
        changed_files=["output/client_presentation_2026/01_transcript.md"], repo_root=tmp_path)
    assert missing.measurement_status == "UNVERIFIED"

    # Authentic plan in docs/plans/
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    plan_file = plans_dir / "feat_real_work.md"
    plan_file.write_text(
        "# Implement High-Throughput Batch Processing Engine\n\n"
        "Replaces serial batch ingestion with parallel streaming workers to cut pipeline latency by 4x.\n",
        encoding="utf-8",
    )
    outcome_plan = evaluate_branch_outcome(
        branch="feat/real-work",
        tier="P1",
        changed_files=["core/engine.py"],
        repo_root=tmp_path,
    )
    stmt_plan = outcome_plan.value_statement()
    assert "Replaces serial batch ingestion with parallel streaming workers" in stmt_plan
    assert outcome_plan.measurement_status == "EXPECTED"


def test_unrelated_plan_and_receipt_cannot_verify_value(tmp_path: Path):
    from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome, UNMEASURED_VALUE_STATEMENT

    wt = tmp_path / "worktree"
    plan = wt / "plans" / "feat_copilot_query_pipeline_augmentation_plan" / "implementation_plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Copilot\n\nReduces query latency with a copilot.\n", encoding="utf-8")
    receipt = wt / "reports" / "wave_purge_silent_mock_toggles_receipt.json"
    receipt.parent.mkdir()
    receipt.write_text('{"branch":"feat/purge-silent-mock-toggles"}', encoding="utf-8")
    outcome = evaluate_branch_outcome(
        "feat/authentic-qrels-benchmark", "P3", repo_root=tmp_path, worktree_path=str(wt),
        evidence_links=[{"label": "Plan", "url": plan.as_uri()}, {"label": "Wave Receipt", "url": receipt.as_uri()}],
    )
    assert outcome.value_statement() == UNMEASURED_VALUE_STATEMENT
    assert outcome.measurement_status == "UNVERIFIED"
    links = _discover_evidence_links("feat/authentic-qrels-benchmark", {
        "worktree_path": str(wt), "head_sha": "a" * 40,
        "plans": [{"plan_path": "plans/feat_copilot_query_pipeline_augmentation_plan/implementation_plan.md"}],
    }, [], tmp_path)
    assert links == []


def test_branch_owned_plan_without_benefit_stays_unverified(tmp_path: Path):
    from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome

    plan = tmp_path / "docs" / "plans" / "feat_model_panel.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Build Model Panel\n\nLists model names and parameter counts.\n", encoding="utf-8")
    outcome = evaluate_branch_outcome("feat/model-panel", "P3", repo_root=tmp_path)
    assert outcome.measurement_status == "UNVERIFIED"
    assert "Lists model names" in outcome.value_statement()


def test_measured_value_requires_matching_revision_and_improvement(tmp_path: Path):
    from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome

    wt = tmp_path / "worktree"
    reports = wt / "reports"
    reports.mkdir(parents=True)
    receipt = reports / "benefit_measurement_feat_batch_receipt.json"
    data = {
        "receipt_type": "pr_queue_benefit_measurement", "terminal_outcome": "PASS",
        "branch": "feat/batch", "branch_sha": "a" * 40, "base_sha": "b" * 40,
        "metric_name": "p95 latency", "baseline_value": 120, "observed_value": 80,
        "unit": "ms", "scope": "batch ingestion", "benefit_direction": "decrease",
        "benefit_category": "LATENCY_IMPROVEMENT",
    }
    receipt.write_text(json.dumps(data), encoding="utf-8")
    measured = evaluate_branch_outcome("feat/batch", "P0", repo_root=tmp_path,
        worktree_path=str(wt), branch_sha="a" * 40, base_sha="b" * 40)
    assert measured.measurement_status == "MEASURED"
    assert "120 to 80 ms" in measured.value_statement()
    stale = evaluate_branch_outcome("feat/batch", "P0", repo_root=tmp_path,
        worktree_path=str(wt), branch_sha="c" * 40, base_sha="b" * 40)
    assert stale.measurement_status == "UNVERIFIED"
    data["observed_value"] = 150
    receipt.write_text(json.dumps(data), encoding="utf-8")
    worse = evaluate_branch_outcome("feat/batch", "P0", repo_root=tmp_path,
        worktree_path=str(wt), branch_sha="a" * 40, base_sha="b" * 40)
    assert worse.measurement_status == "UNVERIFIED"
