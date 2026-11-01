"""Unit tests for governed worktree index.lock inspection and recovery."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.pr_governance_core.merge_lock import LockContentionError
from tools.pr_governance_core.worktree_lock import (
    WorktreeLockInfo,
    get_file_holder_pid,
    inspect_worktree_index_locks,
    reclaim_stale_worktree_lock,
)


@pytest.fixture
def mock_git_environment(tmp_path: Path):
    """Sets up a mock .git directory with worktrees metadata structure."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True)
    worktrees_dir = git_dir / "worktrees"
    worktrees_dir.mkdir()
    return tmp_path, git_dir, worktrees_dir


def test_worktree_lock_info_to_dict():
    info = WorktreeLockInfo(
        worktree_slug="test_slug",
        lock_file_path="/tmp/test/index.lock",
        holding_pid=12345,
        is_holding_pid_alive=False,
        age_seconds=120.5,
        is_stale=True,
        size_bytes=0,
    )
    d = info.to_dict()
    assert d["worktree_slug"] == "test_slug"
    assert d["holding_pid"] == 12345
    assert d["is_stale"] is True
    assert d["age_seconds"] == 120.5


def test_inspect_worktree_index_locks_empty(mock_git_environment):
    repo_root, _, _ = mock_git_environment
    locks = inspect_worktree_index_locks(repo_root=repo_root)
    assert locks == []


def test_inspect_worktree_index_locks_detects_locks(mock_git_environment):
    repo_root, _, worktrees_dir = mock_git_environment
    wt_meta = worktrees_dir / "wt_session_alpha"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("dummy")

    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=None):
        locks = inspect_worktree_index_locks(repo_root=repo_root, stale_threshold_sec=0.0)

    assert len(locks) == 1
    assert locks[0].worktree_slug == "wt_session_alpha"
    assert locks[0].size_bytes == 5
    assert locks[0].is_stale is True


def test_reclaim_stale_lock_when_no_lock_present(mock_git_environment):
    repo_root, _, _ = mock_git_environment
    res = reclaim_stale_worktree_lock("nonexistent", repo_root=repo_root)
    assert res is True


def test_reclaim_stale_lock_held_by_dead_pid(mock_git_environment):
    repo_root, _, worktrees_dir = mock_git_environment
    wt_meta = worktrees_dir / "wt_dead_process"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("")

    # Dead PID 999999
    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=999999):
        with patch("tools.pr_governance_core.worktree_lock._is_pid_alive", return_value=False):
            res = reclaim_stale_worktree_lock("wt_dead_process", repo_root=repo_root)

    assert res is True
    assert not lock_file.exists()


def test_reclaim_stale_lock_held_by_live_pid_raises(mock_git_environment):
    repo_root, _, worktrees_dir = mock_git_environment
    wt_meta = worktrees_dir / "wt_active_process"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("")

    current_pid = os.getpid()
    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=current_pid):
        with patch("tools.pr_governance_core.worktree_lock._is_pid_alive", return_value=True):
            with pytest.raises(LockContentionError, match="held by active PID"):
                reclaim_stale_worktree_lock("wt_active_process", repo_root=repo_root, force=False)

    assert lock_file.exists()


def test_reclaim_stale_lock_held_by_live_pid_with_force(mock_git_environment):
    repo_root, _, worktrees_dir = mock_git_environment
    wt_meta = worktrees_dir / "wt_forced_reclaim"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("")

    current_pid = os.getpid()
    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=current_pid):
        with patch("tools.pr_governance_core.worktree_lock._is_pid_alive", return_value=True):
            res = reclaim_stale_worktree_lock("wt_forced_reclaim", repo_root=repo_root, force=True)

    assert res is True
    assert not lock_file.exists()


def test_reclaim_lock_unknown_holder_recent_raises(mock_git_environment):
    repo_root, _, worktrees_dir = mock_git_environment
    wt_meta = worktrees_dir / "wt_recent_unknown"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("")

    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=None):
        with pytest.raises(LockContentionError, match="has no identified holder and was recently modified"):
            reclaim_stale_worktree_lock("wt_recent_unknown", repo_root=repo_root, force=False, stale_threshold_sec=60.0)

    assert lock_file.exists()


def test_reclaim_lock_unknown_holder_stale_unlinks(mock_git_environment):
    repo_root, _, worktrees_dir = mock_git_environment
    wt_meta = worktrees_dir / "wt_stale_unknown"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("")

    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=None):
        res = reclaim_stale_worktree_lock("wt_stale_unknown", repo_root=repo_root, force=False, stale_threshold_sec=0.0)

    assert res is True
    assert not lock_file.exists()

