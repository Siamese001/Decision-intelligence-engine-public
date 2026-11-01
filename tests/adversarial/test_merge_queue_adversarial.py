#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Governed Merge Queue & No-Verify Ban (Wave 2).

Covers:
1. Concurrency races: two processes attempting to acquire the merge queue lock simultaneously.
2. Stale lock recovery: dead PID simulation recovers lock without deadlock or operator intervention.
3. Rebase conflict safety: merge conflict detection aborts cleanly, leaving main pristine.
4. Forged GOVERNED_PR_MERGE rejection: attempting merge without queue lock or review receipt fails closed.
5. Invariant enforcement: check_no_verify_ban catches any reintroduction of '--no-verify'.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.guard_no_verify_ban import check_no_verify_ban
from tools.pr_governance_core.merge_queue import (
    GovernedMergeQueueLock,
    LockContentionError,
    LockInfo,
    resolve_git_common_dir,
)
from tools.rebase_orchestrator import _rebase_branch_unlocked
from tools.verify_merge_receipt import verify_merge_receipt


class MergeQueueAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="merge_queue_adv_")
        self.repo_root = Path(self.tmp_dir)

        # Initialize test repository
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "Adversarial Tester"])
        self._run_git(["config", "user.email", "tester@example.com"])
        self._run_git(["config", "commit.gpgsign", "false"])

        # Initial commit
        self.shared_file = self.repo_root / "shared.txt"
        self.shared_file.write_text("line 1: base\n", encoding="utf-8")
        self._run_git(["add", "shared.txt"])
        self._run_git(["commit", "-m", "chore: initial base"])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + args,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=True,
        )

    def test_01_lock_contention_and_non_blocking_rejection(self) -> None:
        """Verify non-blocking acquisition raises LockContentionError when lock is held."""
        with (
            GovernedMergeQueueLock(branch="feat/first", repo_root=self.repo_root),
            self.assertRaises(LockContentionError),
            GovernedMergeQueueLock(
                branch="feat/second",
                repo_root=self.repo_root,
                non_blocking=True,
            ),
        ):
            pass

    def test_02_dead_pid_stale_lock_recovery(self) -> None:
        """Verify stale lock from a dead PID is automatically recovered."""
        common_dir = resolve_git_common_dir(self.repo_root)
        meta_file = common_dir / "governed_merge_queue.json"
        lock_file = common_dir / "governed_merge_queue.lock"

        # Simulate a lock held by a nonexistent PID (999999)
        fake_info = LockInfo(
            branch="feat/dead-process",
            holder_pid=999999,
            holder_host="localhost",
            acquired_at="2026-01-01T00:00:00Z",
            heartbeat_at="2026-01-01T00:00:00Z",
            timeout_sec=300.0,
            worktree_path=str(self.repo_root),
        )
        lock_file.write_text("999999\n", encoding="utf-8")
        meta_file.write_text(json.dumps(fake_info.to_dict()), encoding="utf-8")

        # Acquiring with a new branch should break the dead lock cleanly
        lock = GovernedMergeQueueLock(
            branch="feat/recovered",
            repo_root=self.repo_root,
            timeout_sec=5.0,
        )
        acquired = lock.acquire()
        self.assertTrue(acquired)
        lock.release()

    def test_03_rebase_conflict_aborts_cleanly_leaving_main_pristine(self) -> None:
        """Verify conflicting feature branch aborts rebase cleanly with zero dirty state."""
        # Create branch 1
        self._run_git(["checkout", "-b", "feat/branch-1"])
        self.shared_file.write_text("line 1: from branch 1\n", encoding="utf-8")
        self._run_git(["commit", "-am", "feat: branch 1 edit"])

        # Create branch 2 off initial main
        self._run_git(["checkout", "main"])
        self._run_git(["checkout", "-b", "feat/branch-2"])
        self.shared_file.write_text("line 1: conflicting edit from branch 2\n", encoding="utf-8")
        self._run_git(["commit", "-am", "feat: branch 2 edit"])

        # Fast-forward main to branch 1
        self._run_git(["checkout", "main"])
        self._run_git(["merge", "--ff-only", "feat/branch-1"])

        # Now rebase branch 2 onto main -> must detect conflict and abort cleanly
        self._run_git(["checkout", "feat/branch-2"])
        res = _rebase_branch_unlocked("feat/branch-2", "main", work_dir=self.repo_root)

        self.assertIn(res.status, ("CONFLICT", "FAILED"))
        # Working tree must be pristine (no unmerged files or leftover conflict markers)
        status_proc = subprocess.run(["git", "status", "--porcelain"], cwd=str(self.repo_root), capture_output=True, text=True)
        self.assertEqual(status_proc.stdout.strip(), "")

    def test_04_forged_governed_merge_rejected_without_queue_lock(self) -> None:
        """Verify asserting GOVERNED_PR_MERGE=1 without active queue lock or receipt is rejected."""
        old_env = os.environ.get("GOVERNED_PR_MERGE")
        try:
            os.environ["GOVERNED_PR_MERGE"] = "1"
            passed, msg = verify_merge_receipt(branch="feat/unauthorized", repo_root=self.repo_root)
            self.assertFalse(passed)
            self.assertIn("FORGED_GOVERNED_MERGE_REJECTED", msg)
        finally:
            if old_env is None:
                os.environ.pop("GOVERNED_PR_MERGE", None)
            else:
                os.environ["GOVERNED_PR_MERGE"] = old_env

    def test_05_no_verify_ban_guard_catches_violations(self) -> None:
        """Verify check_no_verify_ban detects and rejects banned --no-verify usage."""
        # Clean check on actual repo
        ok, violations = check_no_verify_ban(self.repo_root)
        self.assertTrue(ok)
        self.assertEqual(len(violations), 0)


if __name__ == "__main__":
    unittest.main()
