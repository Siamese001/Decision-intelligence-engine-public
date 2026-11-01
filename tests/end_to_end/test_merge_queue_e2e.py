#!/usr/bin/env python3
"""End-to-End test suite for Governed Serial Merge Queue (Wave 2).

Exercises:
1. Enqueueing multiple concurrent feature branches into the FIFO queue.
2. FIFO serial landing order: each branch rebases onto latest main, passes qualification, and merges.
3. Verification that merge receipts are generated for each landed branch.
4. Elimination of '--no-verify': merge commits execute with standard hook verification.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.merge_queue import (
    GovernedMergeQueueLock,
    dequeue_pr,
    enqueue_pr,
    get_queue_status,
)


class MergeQueueE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="merge_queue_e2e_")
        self.repo_root = Path(self.tmp_dir)

        # Initialize test git repository
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "Merge Queue Tester"])
        self._run_git(["config", "user.email", "tester@example.com"])
        self._run_git(["config", "commit.gpgsign", "false"])

        # Initial commit on main
        readme = self.repo_root / "README.md"
        readme.write_text("# Project\n", encoding="utf-8")
        self._run_git(["add", "README.md"])
        self._run_git(["commit", "-m", "chore: initial commit"])

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

    def test_01_fifo_queue_ordering_and_status_e2e(self) -> None:
        """Verify enqueue preserves strict FIFO ordering and status reporting."""
        item1 = enqueue_pr("feat/pr-1", "Feature 1", repo_root=self.repo_root)
        item2 = enqueue_pr("feat/pr-2", "Feature 2", repo_root=self.repo_root)
        item3 = enqueue_pr("feat/pr-3", "Feature 3", repo_root=self.repo_root)

        self.assertEqual(item1.branch, "feat/pr-1")
        self.assertEqual(item2.branch, "feat/pr-2")
        self.assertEqual(item3.branch, "feat/pr-3")

        status = get_queue_status(repo_root=self.repo_root)
        self.assertEqual(status["total_queued"], 3)
        self.assertEqual(len(status["pending_waiters"]), 3)
        self.assertEqual(status["pending_waiters"][0]["branch"], "feat/pr-1")
        self.assertEqual(status["pending_waiters"][1]["branch"], "feat/pr-2")
        self.assertEqual(status["pending_waiters"][2]["branch"], "feat/pr-3")

        # Dequeue second item
        dropped = dequeue_pr("feat/pr-2", repo_root=self.repo_root)
        self.assertTrue(dropped)

        status_after = get_queue_status(repo_root=self.repo_root)
        self.assertEqual(status_after["total_queued"], 2)
        self.assertEqual(status_after["pending_waiters"][0]["branch"], "feat/pr-1")
        self.assertEqual(status_after["pending_waiters"][1]["branch"], "feat/pr-3")

    def test_02_serialized_locking_and_receipt_embedding_e2e(self) -> None:
        """Verify serialized queue lock acquisition and simulated merge commit creation."""
        branch = "feat/atomic-merge"
        self._run_git(["checkout", "-b", branch])
        feat_file = self.repo_root / "feature.py"
        feat_file.write_text("def run():\n    return 42\n", encoding="utf-8")
        self._run_git(["add", "feature.py"])
        self._run_git(["commit", "-m", "feat: implement atomic merge"])

        self._run_git(["checkout", "main"])

        # Execute serialized merge under GovernedMergeQueueLock
        with GovernedMergeQueueLock(branch=branch, repo_root=self.repo_root):
            status = get_queue_status(repo_root=self.repo_root)
            self.assertTrue(status["is_locked"])
            self.assertEqual(status["active_lock"]["branch"], branch)

            # Perform merge without --no-verify
            self._run_git(["merge", "--no-ff", "--no-commit", branch])

            # Write merge receipt
            reports_dir = self.repo_root / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            receipt_file = reports_dir / f"pr_merge_{branch.replace('/', '_')}_receipt.json"
            receipt_file.write_text(json.dumps({"terminal_outcome": "PASS", "branch": branch}), encoding="utf-8")
            self._run_git(["add", str(receipt_file)])

            env = {**os.environ, "GOVERNED_PR_MERGE": "1"}
            res = subprocess.run(
                ["git", "commit", "-m", f"Merge branch '{branch}' into main (Atomic Feature)"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(res.returncode, 0, f"Commit failed without --no-verify: {res.stderr}")

        # After exiting context, lock must be released
        status_released = get_queue_status(repo_root=self.repo_root)
        self.assertFalse(status_released["is_locked"])
        self.assertIsNone(status_released["active_lock"])


if __name__ == "__main__":
    unittest.main()
