"""Unit tests for tools/audit_worktree_stale_locks.py."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.audit_worktree_stale_locks import (
    audit_worktree_stale_locks,
    auto_fix_defects,
    format_audit_report,
    main,
)


@pytest.fixture
def mock_repo(tmp_path: Path):
    """Sets up a mock repo structure with .git/worktrees and .worktrees."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True)
    worktrees_meta = git_dir / "worktrees"
    worktrees_meta.mkdir()
    worktrees_code = tmp_path / ".worktrees"
    worktrees_code.mkdir()
    return tmp_path, worktrees_meta, worktrees_code


def test_audit_clean_worktrees(mock_repo):
    repo_root, _, _ = mock_repo
    res = audit_worktree_stale_locks(repo_root=repo_root)
    assert res["is_clean"] is True
    assert res["stale_locks"] == []
    assert res["orphaned_sockets"] == []
    assert res["stale_provisioning_lock"] is False

    report = format_audit_report(res)
    assert "PASS" in report


def test_audit_detects_stale_lock(mock_repo):
    repo_root, worktrees_meta, _ = mock_repo
    wt_meta = worktrees_meta / "wt_alpha"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("lock_content")

    # Set mtime back by 1000s
    old_time = time.time() - 1000.0
    os.utime(lock_file, (old_time, old_time))

    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=None):
        res = audit_worktree_stale_locks(repo_root=repo_root, stale_age_seconds=600.0)
        assert res["is_clean"] is False
        assert len(res["stale_locks"]) == 1
        assert res["stale_locks"][0]["worktree_slug"] == "wt_alpha"

        report = format_audit_report(res)
        assert "DEFECTS DETECTED" in report
        assert "wt_alpha" in report


def test_audit_detects_orphaned_ipc_socket(mock_repo):
    repo_root, worktrees_meta, _ = mock_repo
    wt_meta = worktrees_meta / "wt_beta"
    wt_meta.mkdir()
    socket_file = wt_meta / "fsmonitor--daemon.ipc"
    socket_file.write_text("ipc_socket")

    res = audit_worktree_stale_locks(repo_root=repo_root)
    assert res["is_clean"] is False
    assert len(res["orphaned_sockets"]) == 1
    assert "fsmonitor--daemon.ipc" in res["orphaned_sockets"][0]

    report = format_audit_report(res)
    assert "Orphaned Socket" in report


def test_audit_detects_stale_provisioning_lock(mock_repo):
    repo_root, _, worktrees_code = mock_repo
    prov_lock = worktrees_code / ".provisioning.lock"
    prov_lock.write_text("lock")

    old_time = time.time() - 1000.0
    os.utime(prov_lock, (old_time, old_time))

    res = audit_worktree_stale_locks(repo_root=repo_root, stale_age_seconds=600.0)
    assert res["is_clean"] is False
    assert res["stale_provisioning_lock"] is True


def test_auto_fix_defects(mock_repo):
    repo_root, worktrees_meta, worktrees_code = mock_repo
    # Setup stale lock
    wt_meta = worktrees_meta / "wt_gamma"
    wt_meta.mkdir()
    lock_file = wt_meta / "index.lock"
    lock_file.write_text("lock")
    old_time = time.time() - 1000.0
    os.utime(lock_file, (old_time, old_time))

    # Setup socket
    sock_file = wt_meta / "fsmonitor--daemon.ipc"
    sock_file.write_text("sock")

    # Setup stale prov lock
    prov_lock = worktrees_code / ".provisioning.lock"
    prov_lock.write_text("prov")
    os.utime(prov_lock, (old_time, old_time))

    with patch("tools.pr_governance_core.worktree_lock.get_file_holder_pid", return_value=None):
        res = audit_worktree_stale_locks(repo_root=repo_root, stale_age_seconds=600.0)
        assert res["is_clean"] is False

        fixed = auto_fix_defects(res, repo_root=repo_root)
        assert fixed >= 3
        assert not lock_file.exists()
        assert not sock_file.exists()
        assert not prov_lock.exists()

        res_after = audit_worktree_stale_locks(repo_root=repo_root, stale_age_seconds=600.0)
        assert res_after["is_clean"] is True


def test_main_cli_check_clean(mock_repo):
    repo_root, _, _ = mock_repo
    code = main(["--check", "--repo-root", str(repo_root)])
    assert code == 0


def test_main_cli_check_failure(mock_repo):
    repo_root, worktrees_meta, _ = mock_repo
    wt_meta = worktrees_meta / "wt_delta"
    wt_meta.mkdir()
    sock_file = wt_meta / "fsmonitor--daemon.ipc"
    sock_file.write_text("sock")

    code = main(["--check", "--repo-root", str(repo_root)])
    assert code == 1


def test_main_cli_json_output(mock_repo, capsys):
    repo_root, _, _ = mock_repo
    code = main(["--json", "--repo-root", str(repo_root)])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["is_clean"] is True
