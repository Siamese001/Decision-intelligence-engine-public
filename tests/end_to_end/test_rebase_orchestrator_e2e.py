#!/usr/bin/env python3
"""End-to-End tests for Governed Multi-Agent Rebase Orchestrator (Wave 5).

Verifies:
1. Full serialized rebase workflow in isolated temporary git repository.
2. Already up-to-date branch short-circuit (<0.2s).
3. Clean merge conflict abortion without leaving dirty working tree state.
4. Status inspection and force break recovery across reservations.
5. Rebase orchestrator CLI entrypoints for queue status and lock breaking.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tools.pr_governance_core.merge_queue import (
    GovernedMergeQueueLock,
    force_break_queue_lock,
    inspect_queue_lock,
)
from tools.rebase_orchestrator import (
    cmd_break,
    cmd_status,
    rebase_branch_serialized,
)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


class RebaseOrchestratorE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="rebase_orch_e2e_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        # Initialize clean git repo
        _run_git(["init", "-b", "main"], self.repo_root)
        _run_git(["config", "user.name", "Test Orchestrator"], self.repo_root)
        _run_git(["config", "user.email", "orchestrator@example.com"], self.repo_root)
        _run_git(["config", "commit.gpgsign", "false"], self.repo_root)

        # Initial commit on main
        readme = self.repo_root / "README.md"
        readme.write_text("# Test Repo\n", encoding="utf-8")
        _run_git(["add", "README.md"], self.repo_root)
        _run_git(["commit", "-m", "chore: initial commit"], self.repo_root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_serialized_rebase_success(self) -> None:
        """Vector 1: Serialized rebase applies commits onto updated main cleanly."""
        # Create feature branch
        _run_git(["checkout", "-b", "feat/feature-1"], self.repo_root)
        f1 = self.repo_root / "feat1.txt"
        f1.write_text("feature 1 content\n", encoding="utf-8")
        _run_git(["add", "feat1.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: feature 1 addition"], self.repo_root)

        # Advance main
        _run_git(["checkout", "main"], self.repo_root)
        m1 = self.repo_root / "main1.txt"
        m1.write_text("main 1 content\n", encoding="utf-8")
        _run_git(["add", "main1.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: main advance"], self.repo_root)

        # Switch back to feature branch
        _run_git(["checkout", "feat/feature-1"], self.repo_root)

        # Perform governed serialized rebase
        result = rebase_branch_serialized(
            branch="feat/feature-1",
            base_branch="main",
            repo_root=self.repo_root,
        )

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.commits_rebased, 1)
        self.assertNotEqual(result.head_sha_before, result.head_sha_after)
        self.assertTrue((self.repo_root / "feat1.txt").is_file())
        self.assertTrue((self.repo_root / "main1.txt").is_file())

        # Verify queue lock is cleanly released
        self.assertIsNone(inspect_queue_lock(self.repo_root))

    def test_02_up_to_date_short_circuit_sub_second(self) -> None:
        """Vector 2: Up-to-date branch short-circuits instantly (<0.2s)."""
        _run_git(["checkout", "-b", "feat/already-current"], self.repo_root)

        t0 = time.monotonic()
        result = rebase_branch_serialized(
            branch="feat/already-current",
            base_branch="main",
            repo_root=self.repo_root,
        )
        dur = time.monotonic() - t0

        self.assertEqual(result.status, "UP_TO_DATE")
        self.assertEqual(result.commits_rebased, 0)
        self.assertEqual(result.head_sha_before, result.head_sha_after)
        self.assertLess(dur, 0.2, f"Up-to-date branch must short circuit (<0.2s), took {dur:.4f}s")
        self.assertIsNone(inspect_queue_lock(self.repo_root))

    def test_03_merge_conflict_aborted_cleanly(self) -> None:
        """Vector 3: Merge conflict aborts rebase cleanly leaving working tree pristine."""
        # Conflict setup: both branches modify conflict.txt differently
        conflict_file = self.repo_root / "conflict.txt"
        conflict_file.write_text("base content\n", encoding="utf-8")
        _run_git(["add", "conflict.txt"], self.repo_root)
        _run_git(["commit", "-m", "chore: base conflict file"], self.repo_root)

        # Feature branch edit
        _run_git(["checkout", "-b", "feat/conflicting-feat"], self.repo_root)
        conflict_file.write_text("feature modified content\n", encoding="utf-8")
        _run_git(["commit", "-am", "feat: edit conflict file on branch"], self.repo_root)

        # Main branch edit
        _run_git(["checkout", "main"], self.repo_root)
        conflict_file.write_text("main modified content\n", encoding="utf-8")
        _run_git(["commit", "-am", "fix: edit conflict file on main"], self.repo_root)

        # Attempt rebase from feature branch
        _run_git(["checkout", "feat/conflicting-feat"], self.repo_root)
        result = rebase_branch_serialized(
            branch="feat/conflicting-feat",
            base_branch="main",
            repo_root=self.repo_root,
        )

        self.assertEqual(result.status, "CONFLICT")
        # Ensure working tree was pristine (no rebase-apply or in-progress rebase dir)
        status_proc = _run_git(["status", "--porcelain"], self.repo_root)
        self.assertEqual(status_proc.stdout.strip(), "", "Working tree must be pristine after abort")
        self.assertIsNone(inspect_queue_lock(self.repo_root))

    def test_04_status_inspection_and_force_break(self) -> None:
        """Vector 4: inspect_queue_lock and force_break_queue_lock manage active reservations."""
        self.assertIsNone(inspect_queue_lock(self.repo_root))

        lock = GovernedMergeQueueLock("feat/active-worker", self.repo_root, timeout_sec=10.0)
        self.assertTrue(lock.acquire())

        info = inspect_queue_lock(self.repo_root)
        self.assertIsNotNone(info)
        self.assertEqual(info.branch, "feat/active-worker")
        self.assertEqual(info.holder_pid, os.getpid())

        # Forcibly break lock
        broken = force_break_queue_lock(self.repo_root)
        self.assertTrue(broken)
        self.assertIsNone(inspect_queue_lock(self.repo_root))

        # Re-acquire immediately
        lock2 = GovernedMergeQueueLock("feat/new-worker", self.repo_root, non_blocking=True)
        self.assertTrue(lock2.acquire())
        lock2.release()

    def test_05_rebase_orchestrator_cli_status_and_break(self) -> None:
        """Vector 5: CLI status and break subcommands execute cleanly."""
        status_args = argparse.Namespace(json=False, repo_root=self.repo_root)
        self.assertEqual(cmd_status(status_args), 0)

        # Create lock and check CLI status
        lock = GovernedMergeQueueLock("feat/cli-test", self.repo_root)
        lock.acquire()

        break_args = argparse.Namespace(force=True, repo_root=self.repo_root)
        self.assertEqual(cmd_break(break_args), 0)
        self.assertIsNone(inspect_queue_lock(self.repo_root))


if __name__ == "__main__":
    unittest.main()
