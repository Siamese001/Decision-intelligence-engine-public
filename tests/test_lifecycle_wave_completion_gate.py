"""Unit tests for Wave Completion Gate in PR Review and Merge Lifecycle."""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.pr_governance_core.lifecycle import (
    _check_merge_prerequisites,
    cmd_merge,
    cmd_review,
    verify_branch_wave_completion,
)


@pytest.fixture
def mock_repo(tmp_path: Path):
    """Sets up a temporary repo structure with plans directory."""
    plans_dir = tmp_path / "plans" / "feat_test_branch"
    plans_dir.mkdir(parents=True)
    return tmp_path, plans_dir


def test_verify_branch_wave_completion_open_waves(mock_repo):
    repo_root, plans_dir = mock_repo
    plan_file = plans_dir / "implementation_plan.md"
    plan_file.write_text(
        """# Sample Feature Plan
## Wave 1: First Step
### Objective & Scope
Do step 1.
## Wave 2: Second Step
### Objective & Scope
Do step 2.
""",
        encoding="utf-8",
    )
    is_complete, open_w, comp_w = verify_branch_wave_completion(
        "feat/test-branch",
        wt_path=repo_root,
        changed_files=["plans/feat_test_branch/implementation_plan.md"],
        repo_root=repo_root,
    )
    assert is_complete is False
    assert "1" in open_w
    assert "2" in open_w


def test_verify_branch_wave_completion_all_done(mock_repo):
    repo_root, plans_dir = mock_repo
    plan_file = plans_dir / "implementation_plan.md"
    plan_file.write_text(
        """# Sample Feature Plan
| Wave | Description | Status |
| :--- | :--- | :--- |
| Wave 1 | First Step | ✅ COMPLETE |
| Wave 2 | Second Step | ✅ COMPLETE |
""",
        encoding="utf-8",
    )
    is_complete, open_w, comp_w = verify_branch_wave_completion(
        "feat/test-branch",
        wt_path=repo_root,
        changed_files=["plans/feat_test_branch/implementation_plan.md"],
        repo_root=repo_root,
    )
    assert is_complete is True
    assert open_w == "—"
    assert "1" in comp_w
    assert "2" in comp_w


def test_verify_branch_wave_completion_no_plan(tmp_path: Path):
    is_complete, open_w, comp_w = verify_branch_wave_completion(
        "feat/no-plan-branch",
        wt_path=tmp_path,
        changed_files=["tools/some_tool.py"],
        repo_root=tmp_path,
    )
    assert is_complete is True
    assert open_w == "?"
    assert comp_w == "?"


def test_cmd_review_blocks_on_open_waves(capsys):
    args = argparse.Namespace(
        branch="feat/test-feature",
        remote=False,
        fetch=False,
        skip_readiness_run=True,
        force_readiness=False,
        fail_fast=False,
        recheck_failed_only=False,
        full=False,
        llm_summary=False,
        github=False,
        retry=0,
        allow_open_waves=False,
    )
    with patch("tools.pr_governance_core.lifecycle.get_feature_branches", return_value=["feat/test-feature"]), \
         patch("tools.pr_governance_core.lifecycle.inspect_branch_status", return_value={"ahead": 2, "behind": 0, "clean_merge": True, "readiness_receipt_pass": True}), \
         patch("tools.pr_governance_core.lifecycle.run_git", return_value=""), \
         patch("tools.pr_governance_core.lifecycle._resolve_review_worktree", return_value=Path("/tmp")), \
         patch("tools.pr_governance_core.lifecycle.check_diff_syntax", return_value=(True, None)), \
         patch("tools.pr_governance_core.lifecycle._execute_or_skip_readiness", return_value=True), \
         patch("tools.pr_governance_core.lifecycle.verify_branch_wave_completion", return_value=(False, "2, 3", "1")):
        rc = cmd_review(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "INCOMPLETE IMPLEMENTATION PLAN WAVES" in captured.out
        assert "2, 3" in captured.out


def test_cmd_review_allows_override_with_flag(capsys):
    args = argparse.Namespace(
        branch="feat/test-feature",
        remote=False,
        fetch=False,
        skip_readiness_run=True,
        force_readiness=False,
        fail_fast=False,
        recheck_failed_only=False,
        full=False,
        llm_summary=False,
        github=False,
        retry=0,
        allow_open_waves=True,
    )
    mock_rev_report = MagicMock()
    mock_rev_report.verdict.value = "PASS"
    mock_rev_report.verdict_reason = "Clean"
    mock_rev_report.recommended_action = "Merge"
    mock_rev_report.llm_intent_summary = None
    mock_rev_report.signals.governance_hazards = []
    mock_rev_report.signals.stale_paths = []
    mock_rev_report.semantic_review = None
    mock_rev_report.to_dict.return_value = {}
    mock_rev_report.evidence_digest = "abc"

    with patch("tools.pr_governance_core.lifecycle.get_feature_branches", return_value=["feat/test-feature"]), \
         patch("tools.pr_governance_core.lifecycle.inspect_branch_status", return_value={"ahead": 2, "behind": 0, "clean_merge": True, "readiness_receipt_pass": True}), \
         patch("tools.pr_governance_core.lifecycle.run_git", return_value=""), \
         patch("tools.pr_governance_core.lifecycle._resolve_review_worktree", return_value=Path("/tmp")), \
         patch("tools.pr_governance_core.lifecycle.check_diff_syntax", return_value=(True, None)), \
         patch("tools.pr_governance_core.lifecycle._execute_or_skip_readiness", return_value=True), \
         patch("tools.pr_governance_core.lifecycle.verify_branch_wave_completion", return_value=(False, "2, 3", "1")), \
         patch("tools.pr_governance_core.code_review.compute_code_review", return_value=mock_rev_report), \
         patch("tools.pr_governance_core.lifecycle._seal_pr_review_attestation"):
        rc = cmd_review(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "PR review PASS" in captured.out


def test_check_merge_prerequisites_blocks_on_open_waves(capsys):
    with patch("tools.pr_governance_core.lifecycle.run_git") as mock_git, \
         patch("tools.pr_governance_core.lifecycle.verify_branch_wave_completion", return_value=(False, "3, 4", "1, 2")):
        mock_git.side_effect = ["branch_sha", "main_sha"]
        rc = _check_merge_prerequisites("feat/test-branch", allow_open_waves=False)
        assert rc == 1
        captured = capsys.readouterr()
        assert "INCOMPLETE IMPLEMENTATION PLAN WAVES" in captured.out
        assert "3, 4" in captured.out


def test_check_merge_prerequisites_allows_override(capsys):
    mock_clearance = MagicMock()
    mock_clearance.cleared = True
    with patch("tools.pr_governance_core.lifecycle.run_git") as mock_git, \
         patch("tools.pr_governance_core.lifecycle.verify_branch_wave_completion", return_value=(False, "3, 4", "1, 2")), \
         patch("dual_pipeline.hitl_governance_core.decision_clearance.require_cleared", return_value=mock_clearance):
        mock_git.side_effect = ["branch_sha", "main_sha"]
        rc = _check_merge_prerequisites("feat/test-branch", allow_open_waves=True)
        assert rc == 0


def test_check_merge_prerequisites_passes_when_all_complete(capsys):
    mock_clearance = MagicMock()
    mock_clearance.cleared = True
    with patch("tools.pr_governance_core.lifecycle.run_git") as mock_git, \
         patch("tools.pr_governance_core.lifecycle.verify_branch_wave_completion", return_value=(True, "—", "1, 2, 3")), \
         patch("dual_pipeline.hitl_governance_core.decision_clearance.require_cleared", return_value=mock_clearance):
        mock_git.side_effect = ["branch_sha", "main_sha"]
        rc = _check_merge_prerequisites("feat/test-branch", allow_open_waves=False)
        assert rc == 0
