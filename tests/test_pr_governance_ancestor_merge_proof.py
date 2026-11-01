"""
Tests for PR governance ancestor and merge proof checks.

Verifies:
1. Freshly started worktrees at behind == 0 are ZERO_COMMIT_ANCESTOR (is_merged == False).
2. Zero-commit worktrees when main advances (ahead == 0, behind > 0) are ZERO_COMMIT_ANCESTOR (is_merged == False, can_auto_clean == False).
3. Genuinely merged feature branches (second-parent merge commit on main or merge receipt) are GENUINELY_MERGED (is_merged == True).
4. _is_branch_merged() rejects zero-commit ancestors and accepts genuinely merged branches.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
import tempfile
import pytest

from tools.pr_governance_core.merge_proof import (
    BranchMergeState,
    classify_branch_merge_state,
    is_second_parent_of_merge_on_main,
)
from tools.pr_governance_core.gc import inspect_worktrees_for_gc
from tools.pr_governance_core.worktree import _is_branch_merged


@pytest.fixture
def test_git_repo(tmp_path: Path):
    """Provisions a hermetic git repository for merge proof testing."""
    env = os.environ.copy()
    # Unset git isolation vars if any
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        env.pop(var, None)

    def run(cmd: list[str], cwd: Path = tmp_path) -> str:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    run(["git", "init", "-b", "main"])
    run(["git", "config", "user.name", "Test Tester"])
    run(["git", "config", "user.email", "tester@example.com"])
    run(["git", "config", "commit.gpgsign", "false"])

    # Initial commit on main
    (tmp_path / "README.md").write_text("# Test Repo\n")
    run(["git", "add", "README.md"])
    run(["git", "commit", "-m", "chore: initial commit"])

    return tmp_path, run


def test_classify_fresh_branch_at_main_head(test_git_repo):
    repo, run = test_git_repo
    main_head = run(["git", "rev-parse", "main"])

    # Create branch feat/fresh without new commits
    run(["git", "branch", "feat/fresh", "main"])
    fresh_head = run(["git", "rev-parse", "feat/fresh"])

    state = classify_branch_merge_state("feat/fresh", fresh_head, main_head, root=repo)
    assert state == BranchMergeState.ZERO_COMMIT_ANCESTOR
    assert not is_second_parent_of_merge_on_main(fresh_head, branch_name="feat/fresh", root=repo)
    assert not _is_branch_merged("feat/fresh", root=repo)


def test_classify_zero_commit_ancestor_when_main_advances(test_git_repo):
    repo, run = test_git_repo

    # Create branch feat/stale off initial commit
    run(["git", "branch", "feat/stale", "main"])
    stale_head = run(["git", "rev-parse", "feat/stale"])

    # Advance main by 3 commits
    for i in range(1, 4):
        (repo / f"file_{i}.txt").write_text(f"content {i}\n")
        run(["git", "add", f"file_{i}.txt"])
        run(["git", "commit", "-m", f"feat: add file {i}"])

    main_head = run(["git", "rev-parse", "main"])

    # Verify feat/stale is 0 ahead, 3 behind
    diff = run(["git", "rev-list", "--left-right", "--count", "main...feat/stale"]).split()
    behind, ahead = int(diff[0]), int(diff[1])
    assert ahead == 0
    assert behind == 3

    # Ensure it classifies as ZERO_COMMIT_ANCESTOR, NOT GENUINELY_MERGED!
    state = classify_branch_merge_state("feat/stale", stale_head, main_head, root=repo)
    assert state == BranchMergeState.ZERO_COMMIT_ANCESTOR
    assert not _is_branch_merged("feat/stale", root=repo)


def test_classify_genuinely_merged_branch(test_git_repo):
    repo, run = test_git_repo

    # Create feature branch with a real commit
    run(["git", "checkout", "-b", "feat/real-feature"])
    (repo / "feature.py").write_text("print('hello')\n")
    run(["git", "add", "feature.py"])
    run(["git", "commit", "-m", "feat: implement real feature"])
    feature_head = run(["git", "rev-parse", "feat/real-feature"])

    # Switch back to main and merge with --no-ff
    run(["git", "checkout", "main"])
    run(["git", "merge", "--no-ff", "feat/real-feature", "-m", "Merge branch 'feat/real-feature' into main"])
    main_head = run(["git", "rev-parse", "main"])

    # Verify merge proof detects it as genuinely merged
    state = classify_branch_merge_state("feat/real-feature", feature_head, main_head, root=repo)
    assert state == BranchMergeState.GENUINELY_MERGED
    assert is_second_parent_of_merge_on_main(feature_head, branch_name="feat/real-feature", root=repo)
    assert _is_branch_merged("feat/real-feature", root=repo)


def test_classify_sealed_merge_receipt(test_git_repo):
    repo, run = test_git_repo

    # Create branch
    run(["git", "branch", "feat/with-receipt", "main"])
    branch_head = run(["git", "rev-parse", "feat/with-receipt"])
    main_head = run(["git", "rev-parse", "main"])

    # Create dummy merge receipt
    reports_dir = repo / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    receipt_file = reports_dir / "pr_merge_feat_with_receipt_receipt.json"
    receipt_file.write_text('{"terminal_outcome": "PASS"}\n')

    state = classify_branch_merge_state("feat/with-receipt", branch_head, main_head, root=repo)
    assert state == BranchMergeState.GENUINELY_MERGED
    assert _is_branch_merged("feat/with-receipt", root=repo)


def test_gc_inspection_distinguishes_zero_commit_ancestor_worktree(test_git_repo):
    repo, run = test_git_repo

    # Create worktree for zero-commit branch
    wt_dir = repo / ".worktrees" / "feat_zero_commit"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "-b", "feat/zero-commit", str(wt_dir), "main"])

    # Advance main
    (repo / "change.txt").write_text("main update\n")
    run(["git", "add", "change.txt"])
    run(["git", "commit", "-m", "feat: advance main"])

    # Inspect worktrees for GC
    candidates = inspect_worktrees_for_gc(root=repo)
    zero_commit_candidate = next((c for c in candidates if c["branch"] == "feat/zero-commit"), None)

    assert zero_commit_candidate is not None
    assert zero_commit_candidate["is_merged"] is False
    assert zero_commit_candidate["is_zero_commit"] is True
    assert zero_commit_candidate["can_auto_clean"] is False


def test_supersession_requires_merge_proof_for_zero_commit_branch(test_git_repo):
    from tools.pr_topology_core.supersession import _evaluate_ancestor_supersession

    repo, run = test_git_repo

    # Create zero commit branch
    run(["git", "branch", "feat/unmerged-zero-commit", "main"])
    branch_sha = run(["git", "rev-parse", "feat/unmerged-zero-commit"])

    # Advance main
    (repo / "new_file.txt").write_text("advance\n")
    run(["git", "add", "new_file.txt"])
    run(["git", "commit", "-m", "feat: advance main"])
    main_sha = run(["git", "rev-parse", "main"])

    target, evidence = _evaluate_ancestor_supersession("feat/unmerged-zero-commit", branch_sha, main_sha, repo, "main")
    # Must NOT be superseded without merge proof
    assert target is None
    assert evidence == []


def test_recreated_branch_after_prior_merge_is_not_falsely_merged(test_git_repo):
    repo, run = test_git_repo

    # 1. Create and merge feat/branch-v1
    run(["git", "checkout", "-b", "feat/same-name"])
    (repo / "v1.txt").write_text("v1\n")
    run(["git", "add", "v1.txt"])
    run(["git", "commit", "-m", "feat: v1 commit"])
    run(["git", "checkout", "main"])
    run(["git", "merge", "--no-ff", "feat/same-name", "-m", "Merge branch 'feat/same-name' into main"])

    # Delete branch ref
    run(["git", "branch", "-D", "feat/same-name"])

    # 2. Advance main further
    (repo / "main_next.txt").write_text("next\n")
    run(["git", "add", "main_next.txt"])
    run(["git", "commit", "-m", "feat: main advance after merge"])
    new_main = run(["git", "rev-parse", "main"])

    # 3. Recreate feat/same-name at new main with 0 commits
    run(["git", "branch", "feat/same-name", "main"])
    recreated_sha = run(["git", "rev-parse", "feat/same-name"])

    # It must classify as ZERO_COMMIT_ANCESTOR (it has 0 commits ahead of main, and its SHA is a newer commit on main, NOT the merged P2!)
    state = classify_branch_merge_state("feat/same-name", recreated_sha, new_main, root=repo)
    assert state == BranchMergeState.ZERO_COMMIT_ANCESTOR
    assert not _is_branch_merged("feat/same-name", root=repo)


def test_untracked_worktree_files_not_falsely_marked_absorbed(test_git_repo):
    from tools.pr_topology_core.supersession import evaluate_dirty_file_coverage

    repo, run = test_git_repo
    wt_dir = repo / ".worktrees" / "feat_untracked_work"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "-b", "feat/untracked-work", str(wt_dir), "main"])

    # Create brand-new untracked file that does NOT exist in main
    new_doc = wt_dir / "documents" / "weekly_update.md"
    new_doc.parent.mkdir(parents=True, exist_ok=True)
    new_doc.write_text("# Weekly Update\nUnique new content.\n", encoding="utf-8")

    is_cov, evidence, residual = evaluate_dirty_file_coverage(wt_dir, "main", root=repo, ignore_review_artifacts=True)
    assert is_cov is False
    assert "documents/weekly_update.md" in residual


def test_evaluate_ahead_zero_branch_distinguishes_merged_vs_zero_commit_stub(test_git_repo):
    from unittest.mock import MagicMock, patch
    from tools.pr_topology_core.disposition import _evaluate_ahead_zero_branch
    from tools.pr_topology_core.matrix import BranchDiff

    repo, run = test_git_repo

    # 1. Create an unmerged zero-commit branch stub
    run(["git", "branch", "feat/zero-stub", "main"])

    # 2. Create and genuinely merge a feature branch
    run(["git", "checkout", "-b", "feat/genuinely-merged"])
    (repo / "merged_feat.py").write_text("x = 1\n", encoding="utf-8")
    run(["git", "add", "merged_feat.py"])
    run(["git", "commit", "-m", "feat: merged feature"])
    run(["git", "checkout", "main"])
    run(["git", "merge", "--no-ff", "feat/genuinely-merged", "-m", "Merge branch 'feat/genuinely-merged' into main"])

    main_sha = run(["git", "rev-parse", "main"])
    main_tree = run(["git", "rev-parse", "main^{tree}"])

    with patch("tools.pr_topology_core.birth.inspect_branch_birth") as mock_birth:
        mock_birth.return_value = MagicMock(is_inception_active=False, age_desc="14 days old")

        stub_disp = _evaluate_ahead_zero_branch(
            branch="feat/zero-stub",
            diff=BranchDiff(branch="feat/zero-stub", ahead=0, behind=2),
            wt={"dirty": False, "stash_count": 0},
            waves_info=None,
            root=repo,
            main_sha=main_sha,
            main_tree=main_tree,
        )
        assert stub_disp.action == "PRUNE"
        assert "No unique commits vs main, abandoned zero-commit branch" in stub_disp.reason
        assert "Absorbed into main" not in stub_disp.reason

        merged_disp = _evaluate_ahead_zero_branch(
            branch="feat/genuinely-merged",
            diff=BranchDiff(branch="feat/genuinely-merged", ahead=0, behind=0),
            wt={"dirty": False, "stash_count": 0},
            waves_info=None,
            root=repo,
            main_sha=main_sha,
            main_tree=main_tree,
        )
        assert merged_disp.action == "PRUNE"
        assert "Merged into main@" in merged_disp.reason

