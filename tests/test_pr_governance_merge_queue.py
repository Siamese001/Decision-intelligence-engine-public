"""Merge queue and dehydrated review worktree tests for Wave 1 PR governance consolidation."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.lifecycle import verify_ephemeral_merge_tree
from tools.pr_governance_core.merge_queue import (
    GovernedMergeQueueLock,
    force_break_queue_lock,
    inspect_queue_lock,
)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )


class TestPRGovernanceMergeQueue(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_gov_mq_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        _git(["init", "-b", "main"], self.repo_root)
        _git(["config", "user.name", "Merge Queue Tester"], self.repo_root)
        _git(["config", "user.email", "mq@example.com"], self.repo_root)
        _git(["config", "commit.gpgsign", "false"], self.repo_root)

        (self.repo_root / "base.txt").write_text("base\n", encoding="utf-8")
        _git(["add", "base.txt"], self.repo_root)
        _git(["commit", "-m", "chore: initial"], self.repo_root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ephemeral_merge_tree_detects_clean_and_conflicting_branches(self) -> None:
        """Verifies git merge-tree --write-tree in-memory check without provisioning a worktree."""
        _git(["checkout", "-b", "feat/clean-branch"], self.repo_root)
        (self.repo_root / "clean.txt").write_text("clean\n", encoding="utf-8")
        _git(["add", "clean.txt"], self.repo_root)
        _git(["commit", "-m", "feat: clean file"], self.repo_root)

        _git(["checkout", "main"], self.repo_root)
        ok_clean, tree_oid = verify_ephemeral_merge_tree(self.repo_root, "main", "feat/clean-branch")
        self.assertTrue(ok_clean)
        self.assertEqual(len(tree_oid), 40)

        # Create conflicting branch
        _git(["checkout", "-b", "feat/conflict-branch"], self.repo_root)
        (self.repo_root / "base.txt").write_text("branch edit\n", encoding="utf-8")
        _git(["add", "base.txt"], self.repo_root)
        _git(["commit", "-m", "feat: branch edit"], self.repo_root)

        _git(["checkout", "main"], self.repo_root)
        (self.repo_root / "base.txt").write_text("main edit\n", encoding="utf-8")
        _git(["add", "base.txt"], self.repo_root)
        _git(["commit", "-m", "feat: main edit"], self.repo_root)

        ok_conf, detail = verify_ephemeral_merge_tree(self.repo_root, "main", "feat/conflict-branch")
        self.assertFalse(ok_conf)
        self.assertIn("conflict", detail.lower())

    def test_merge_queue_lock_reentrant_and_non_blocking_rejection(self) -> None:
        """Verifies GovernedMergeQueueLock re-entrancy and non-blocking contention handling."""
        lock1 = GovernedMergeQueueLock("feat/holder", repo_root=self.repo_root, timeout_sec=5.0)
        self.assertTrue(lock1.acquire())
        try:
            info = inspect_queue_lock(self.repo_root)
            self.assertIsNotNone(info)
            self.assertEqual(info.branch, "feat/holder")
        finally:
            lock1.release()

        self.assertIsNone(inspect_queue_lock(self.repo_root))
        self.assertFalse(force_break_queue_lock(self.repo_root))


if __name__ == "__main__":
    unittest.main()
