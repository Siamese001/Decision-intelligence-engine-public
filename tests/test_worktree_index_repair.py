"""Unit tests for governed worktree index repair engine."""
import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.pr_governance_core.index_repair import (
    RepairResult,
    cmd_repair_index,
    repair_worktree_index,
    resolve_target_worktree,
)


@pytest.fixture
def mock_git_worktree_repo(tmp_path: Path):
    """Initializes a real miniature git repository to test index repair operations."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, capture_output=True, check=True)

    # Commit initial files
    (repo / "file_a.txt").write_text("content A")
    (repo / "tools").mkdir()
    (repo / "tools" / "runner.py").write_text("print('running')")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, capture_output=True, check=True)

    # Create worktree
    wt_dir = tmp_path / "worktrees" / "feat_mock_test"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", str(wt_dir), "-b", "feat/mock-test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    return repo, wt_dir


def test_repair_result_to_dict():
    res = RepairResult(
        success=True,
        worktree_slug="feat_mock",
        worktree_path="/tmp/mock",
        lock_reclaimed=True,
        daemons_reaped=[1234],
        unstaged_deletions=0,
        message="Clean",
    )
    d = res.to_dict()
    assert d["success"] is True
    assert d["unstaged_deletions"] == 0
    assert d["daemons_reaped"] == [1234]


def test_resolve_target_worktree_existing_path(mock_git_worktree_repo):
    _, wt_dir = mock_git_worktree_repo
    resolved = resolve_target_worktree(str(wt_dir))
    assert resolved == wt_dir.resolve()


def test_repair_worktree_index_nonexistent(tmp_path: Path):
    nonexistent = tmp_path / "missing_worktree"
    res = repair_worktree_index(nonexistent)
    assert res.success is False
    assert "does not exist" in res.message


def test_repair_worktree_index_recovers_missing_files(mock_git_worktree_repo):
    repo, wt_dir = mock_git_worktree_repo
    runner_file = wt_dir / "tools" / "runner.py"
    assert runner_file.exists()

    # Simulate interrupted checkout: delete runner.py and file_a.txt
    runner_file.unlink()
    (wt_dir / "file_a.txt").unlink()
    assert not runner_file.exists()
    assert not (wt_dir / "file_a.txt").exists()

    # Run repair
    with patch("tools.pr_governance_core.index_repair.reclaim_stale_worktree_lock", return_value=True):
        with patch("tools.pr_governance_core.index_repair.reap_worktree_fsmonitor", return_value=[]):
            res = repair_worktree_index(wt_dir, root=repo)

    assert res.success is True
    assert res.unstaged_deletions == 0
    # Files must be restored to disk!
    assert runner_file.exists()
    assert (wt_dir / "file_a.txt").exists()
    assert runner_file.read_text() == "print('running')"


def test_repair_worktree_index_preserves_untracked_files(mock_git_worktree_repo):
    repo, wt_dir = mock_git_worktree_repo
    untracked = wt_dir / "my_custom_note.txt"
    untracked.write_text("important scratch notes")

    with patch("tools.pr_governance_core.index_repair.reclaim_stale_worktree_lock", return_value=False):
        with patch("tools.pr_governance_core.index_repair.reap_worktree_fsmonitor", return_value=[]):
            res = repair_worktree_index(wt_dir, root=repo)

    assert res.success is True
    assert untracked.exists()
    assert untracked.read_text() == "important scratch notes"


def test_repair_worktree_index_preserves_local_modifications(mock_git_worktree_repo):
    repo, wt_dir = mock_git_worktree_repo
    file_a = wt_dir / "file_a.txt"
    assert file_a.exists()
    # User made local edits to file_a
    file_a.write_text("locally modified content by developer")

    with patch("tools.pr_governance_core.index_repair.reclaim_stale_worktree_lock", return_value=False):
        with patch("tools.pr_governance_core.index_repair.reap_worktree_fsmonitor", return_value=[]):
            res = repair_worktree_index(wt_dir, root=repo)

    assert res.success is True
    # The developer's local modifications MUST NOT be destroyed
    assert file_a.read_text() == "locally modified content by developer"


def test_cmd_repair_index_cli_handler(mock_git_worktree_repo):
    repo, wt_dir = mock_git_worktree_repo
    args = argparse.Namespace(worktree=str(wt_dir), force_lock=True)

    with patch("tools.pr_governance_core.index_repair.ROOT", repo):
        code = cmd_repair_index(args)

    assert code == 0
