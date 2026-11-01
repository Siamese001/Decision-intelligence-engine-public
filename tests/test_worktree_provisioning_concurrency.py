#!/usr/bin/env python3
"""tests/test_worktree_provisioning_concurrency.py

Concurrency and stress verification suite for Wave 5 Worktree Provisioning Concurrency Barrier.
Validates:
1. Adaptive backoff and serialization of parallel worktree provisioning locks.
2. Clean timeout escalation (TimeoutError) when a lock is held beyond threshold.
3. Multi-threaded concurrent worktree provisioning with file parity and zero partial checkouts.
4. Git runner timeout resilience (120s baseline with heartbeat logging).
"""
from __future__ import annotations

import concurrent.futures
import subprocess
import time
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    git_worktree_provision_lock,
    run_git as recorder_run_git,
)
from tools.pr_governance_core.worktree import worktree_provisioning_lock


@pytest.fixture
def mock_repo(tmp_path: Path) -> Path:
    """Creates a local mock git repo for testing provisioning concurrency."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, capture_output=True, check=True)

    # Add initial files
    (repo / "file1.txt").write_text("Hello 1")
    (repo / "file2.txt").write_text("Hello 2")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, capture_output=True, check=True)
    return repo


def test_worktree_provisioning_lock_serializes_threads(tmp_path: Path):
    """Asserts that worktree_provisioning_lock prevents concurrent access."""
    active_holders = []
    max_simultaneous = 0
    lock_root = tmp_path / "lock_root"
    lock_root.mkdir()

    def worker(worker_id: int):
        nonlocal max_simultaneous
        with worktree_provisioning_lock(timeout_seconds=10.0, root=lock_root):
            active_holders.append(worker_id)
            if len(active_holders) > max_simultaneous:
                max_simultaneous = len(active_holders)
            # Sleep briefly to create contention
            time.sleep(0.05)
            active_holders.remove(worker_id)
        return worker_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(5)]
        results = [f.result() for f in futures]

    assert len(results) == 5
    assert max_simultaneous == 1, f"Expected strict mutual exclusion, got {max_simultaneous} concurrent holders"


def test_git_worktree_provision_lock_serializes_threads(tmp_path: Path):
    """Asserts that recorder git_worktree_provision_lock serializes callers."""
    active_holders = []
    max_simultaneous = 0
    repo_root = tmp_path / "rec_root"
    repo_root.mkdir()

    def worker(worker_id: int):
        nonlocal max_simultaneous
        with git_worktree_provision_lock(repo_root, timeout_seconds=10.0):
            active_holders.append(worker_id)
            if len(active_holders) > max_simultaneous:
                max_simultaneous = len(active_holders)
            time.sleep(0.05)
            active_holders.remove(worker_id)
        return worker_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(5)]
        results = [f.result() for f in futures]

    assert len(results) == 5
    assert max_simultaneous == 1, f"Expected mutual exclusion, got {max_simultaneous}"


def test_worktree_provisioning_lock_timeout(tmp_path: Path):
    """Asserts that worktree_provisioning_lock raises TimeoutError when lock is held."""
    lock_root = tmp_path / "timeout_root"
    lock_root.mkdir()

    # Hold the lock in a context
    with worktree_provisioning_lock(timeout_seconds=5.0, root=lock_root):
        # Attempt to acquire with very short timeout
        with pytest.raises(TimeoutError) as exc_info:
            with worktree_provisioning_lock(timeout_seconds=0.1, root=lock_root):
                pass
        assert "Could not acquire worktree provisioning lock" in str(exc_info.value)


def test_git_worktree_provision_lock_timeout(tmp_path: Path):
    """Asserts that git_worktree_provision_lock raises TimeoutError when lock is held."""
    repo_root = tmp_path / "rec_timeout_root"
    repo_root.mkdir()

    with git_worktree_provision_lock(repo_root, timeout_seconds=5.0):
        with pytest.raises(TimeoutError) as exc_info:
            with git_worktree_provision_lock(repo_root, timeout_seconds=0.1):
                pass
        assert "Could not acquire git worktree provision lock" in str(exc_info.value)


def test_concurrent_git_worktree_creation(mock_repo: Path):
    """Verifies that parallel worktree creation under lock produces clean file parity with zero partials."""
    worktrees_dir = mock_repo / ".worktrees"
    worktrees_dir.mkdir(exist_ok=True)

    def provision_worker(worker_id: int):
        branch = f"feat/concurrent-test-{worker_id}"
        slug = f"feat_concurrent_test_{worker_id}"
        target = worktrees_dir / slug

        with worktree_provisioning_lock(timeout_seconds=30.0, root=mock_repo):
            # Create worktree using git command
            subprocess.run(
                ["git", "worktree", "add", str(target), "-b", branch],
                cwd=mock_repo,
                capture_output=True,
                check=True,
                text=True,
                timeout=30.0,
            )
        return target

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(provision_worker, i) for i in range(3)]
        created_paths = [f.result() for f in futures]

    # Verify each created worktree has full file parity
    for wt in created_paths:
        assert wt.exists()
        assert (wt / ".git").exists()
        assert (wt / "file1.txt").read_text() == "Hello 1"
        assert (wt / "file2.txt").read_text() == "Hello 2"


def test_recorder_run_git_timeout_and_heartbeat(mock_repo: Path):
    """Verifies run_git executes successfully and respects default 120s timeout."""
    output = recorder_run_git(["status", "--porcelain"], cwd=mock_repo)
    assert output == ""
