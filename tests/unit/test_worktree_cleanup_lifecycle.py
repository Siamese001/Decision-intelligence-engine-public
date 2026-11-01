"""
tests/unit/test_worktree_cleanup_lifecycle.py

Tests for post-mutation worktree lifecycle cleanup and teardown.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    teardown_ingest_worktree,
    _resolve_primary_repo_root,
)
from tools.pr_governance_core.worktree import ROOT


def test_teardown_ingest_worktree_safeguards():
    repo_root = _resolve_primary_repo_root(ROOT)
    # Refuses to delete the primary repository root
    assert not teardown_ingest_worktree(repo_root, "main", repo_root=repo_root)

    # Refuses to delete paths outside .worktrees
    assert not teardown_ingest_worktree(repo_root / "documents", "main", repo_root=repo_root)

    # Refuses empty arguments
    assert not teardown_ingest_worktree(None, None, repo_root=repo_root)


def test_teardown_ingest_worktree_execution(tmp_path):
    repo_root = _resolve_primary_repo_root(ROOT)
    test_branch = "ingest/test-worktree-cleanup-dummy"
    test_wt_dir = repo_root / ".worktrees" / "test_dummy_cleanup_wt"

    # Pre-clean
    subprocess.run(["git", "worktree", "remove", "--force", str(test_wt_dir)], cwd=str(repo_root), capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=str(repo_root), capture_output=True)
    subprocess.run(["git", "branch", "-D", test_branch], cwd=str(repo_root), capture_output=True)

    try:
        add_res = subprocess.run(
            ["git", "worktree", "add", "-b", test_branch, str(test_wt_dir), "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        assert add_res.returncode == 0, f"Failed to create test worktree: {add_res.stderr}"

        # Add uncommitted dummy file to simulate pipeline output
        out_dir = test_wt_dir / "output" / "market_intelligence"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "test_artifact.json").write_text('{"status": "COMPLETE"}')

        # Execute teardown: auto-commits deliverables, removes worktree dir, preserves unmerged branch
        ok = teardown_ingest_worktree(test_wt_dir, test_branch, repo_root=repo_root)
        assert ok, "teardown_ingest_worktree failed"
        assert not test_wt_dir.exists(), "Worktree directory still exists"

        # Verify unmerged branch is preserved (dehydrated) so deliverables are never lost
        branch_check = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{test_branch}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        assert branch_check.returncode == 0, "Unmerged branch with deliverables must be preserved in PR queue"
        sha = branch_check.stdout.strip()
        archive_check = subprocess.run(
            ["git", "rev-parse", f"refs/archive/{test_branch}/{sha}"],
            cwd=str(repo_root),
            capture_output=True,
        )
        assert archive_check.returncode == 0, "Immutable archive ref must be written"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(test_wt_dir)], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "branch", "-D", test_branch], cwd=str(repo_root), capture_output=True)


def test_cmd_finish_with_ingest_flag():
    import argparse
    from tools.pr_governance_core.worktree import cmd_finish

    repo_root = _resolve_primary_repo_root(ROOT)
    test_branch = "ingest/test-worktree-cleanup-cmd-finish"
    test_wt_dir = repo_root / ".worktrees" / "test_dummy_cmd_finish_wt"

    # Pre-clean
    subprocess.run(["git", "worktree", "remove", "--force", str(test_wt_dir)], cwd=str(repo_root), capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=str(repo_root), capture_output=True)
    subprocess.run(["git", "branch", "-D", test_branch], cwd=str(repo_root), capture_output=True)

    try:
        add_res = subprocess.run(
            ["git", "worktree", "add", "-b", test_branch, str(test_wt_dir), "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        assert add_res.returncode == 0, f"Failed to create test worktree: {add_res.stderr}"

        out_dir = test_wt_dir / "output" / "doc_test"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "receipt.json").write_text('{"status": "COMPLETE"}')

        args = argparse.Namespace(branch=test_branch, force=False, ingest=True, delete_branch=True)
        rc = cmd_finish(args)
        assert rc == 1, "cmd_finish must block fail-closed when uncommitted deliverable files exist without --force"
        assert test_wt_dir.exists(), "Worktree directory must remain intact when uncommitted deliverables block teardown"

        force_args = argparse.Namespace(branch=test_branch, force=True, ingest=True, delete_branch=True)
        rc_force = cmd_finish(force_args)
        assert rc_force == 0, f"cmd_finish --force returned {rc_force}"
        assert not test_wt_dir.exists(), "Worktree directory still exists after cmd_finish --force"

        branch_check = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{test_branch}"],
            cwd=str(repo_root),
            capture_output=True,
        )
        assert branch_check.returncode != 0, "Test branch was not deleted by cmd_finish"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(test_wt_dir)], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "branch", "-D", test_branch], cwd=str(repo_root), capture_output=True)
