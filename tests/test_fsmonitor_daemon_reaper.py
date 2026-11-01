"""Unit tests for worktree background daemon & IPC socket reaper."""
import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tools.pr_governance_core.worktree_daemon_reaper import (
    _resolve_worktree_gitdir,
    find_fsmonitor_pids,
    reap_worktree_fsmonitor,
    stop_worktree_fsmonitor,
    unlink_lingering_sockets,
)


@pytest.fixture
def mock_worktree_layout(tmp_path: Path):
    """Sets up a mock repo with worktrees directory and gitdir metadata."""
    root = tmp_path / "repo"
    root.mkdir()
    git_dir = root / ".git"
    git_dir.mkdir()
    worktrees_meta = git_dir / "worktrees"
    worktrees_meta.mkdir()

    wt_dir = root / ".worktrees" / "feat_mock_feature"
    wt_dir.mkdir(parents=True)

    wt_gitdir = worktrees_meta / "feat_mock_feature"
    wt_gitdir.mkdir()
    (wt_gitdir / "gitdir").write_text(str(wt_dir.resolve()))
    (wt_gitdir / "commondir").write_text("../..")

    return root, wt_dir, wt_gitdir


def test_stop_worktree_fsmonitor_nonexistent(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist"
    assert stop_worktree_fsmonitor(nonexistent) is False


def test_stop_worktree_fsmonitor_graceful(mock_worktree_layout):
    _, wt_dir, _ = mock_worktree_layout
    with patch("tools.pr_governance_core.worktree_daemon_reaper.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        res = stop_worktree_fsmonitor(wt_dir)
        assert res is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "fsmonitor--daemon" in cmd
        assert "stop" in cmd


def test_resolve_worktree_gitdir(mock_worktree_layout):
    root, wt_dir, wt_gitdir = mock_worktree_layout
    resolved = _resolve_worktree_gitdir(wt_dir, root)
    assert resolved is not None
    assert resolved.resolve() == wt_gitdir.resolve()


def test_unlink_lingering_sockets(mock_worktree_layout):
    root, wt_dir, wt_gitdir = mock_worktree_layout
    ipc_file = wt_gitdir / "fsmonitor--daemon.ipc"
    ipc_file.write_text("")
    cookie_dir = wt_gitdir / "fsmonitor--daemon"
    cookie_dir.mkdir()
    (cookie_dir / "cookie_1").write_text("cookie")

    unlinked = unlink_lingering_sockets(wt_dir, root)
    assert "fsmonitor--daemon.ipc" in unlinked
    assert "fsmonitor--daemon" in unlinked
    assert not ipc_file.exists()
    assert not cookie_dir.exists()


def test_reap_worktree_fsmonitor_terminates_orphan_daemon(mock_worktree_layout):
    root, wt_dir, wt_gitdir = mock_worktree_layout
    ipc_file = wt_gitdir / "fsmonitor--daemon.ipc"
    ipc_file.write_text("")

    mock_daemon_pid = 777777
    with patch("tools.pr_governance_core.worktree_daemon_reaper.find_fsmonitor_pids", return_value=[mock_daemon_pid]):
        with patch("tools.pr_governance_core.worktree_daemon_reaper._is_pid_alive", side_effect=[True, False]):
            with patch("os.kill") as mock_kill:
                reaped = reap_worktree_fsmonitor(wt_dir, root=root)

    assert reaped == [mock_daemon_pid]
    mock_kill.assert_called_with(mock_daemon_pid, signal.SIGTERM)
    assert not ipc_file.exists()


def test_reap_worktree_fsmonitor_respects_immune_pids(mock_worktree_layout):
    root, wt_dir, _ = mock_worktree_layout
    current_pid = os.getpid()

    # find_fsmonitor_pids filters immune PIDs automatically
    with patch("tools.pr_governance_core.worktree_daemon_reaper.subprocess.run") as mock_run:
        mock_run.return_value.stdout = f"{current_pid}\n"
        with patch("tools.pr_governance_core.worktree_daemon_reaper.get_immune_pids", return_value={current_pid}):
            pids = find_fsmonitor_pids(wt_dir, root=root)

    assert current_pid not in pids


def test_remove_worktree_files_invokes_reaper(mock_worktree_layout):
    root, wt_dir, _ = mock_worktree_layout
    from tools.pr_governance_core.worktree import _remove_worktree_files

    with patch("tools.pr_governance_core.worktree_daemon_reaper.reap_worktree_fsmonitor") as mock_reap:
        with patch("tools.pr_governance_core.worktree.kill_ghost_processes"):
            with patch("tools.pr_governance_core.worktree.safely_unlink_worktree_dependencies"):
                with patch("tools.pr_governance_core.worktree.run_git"):
                    res = _remove_worktree_files(wt_dir, force=True, root=root)
                    assert res is True
                    mock_reap.assert_called_once_with(wt_dir, root=root)
