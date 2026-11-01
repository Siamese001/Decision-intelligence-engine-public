"""
tests/test_pr_queue_supersession_adversarial.py

Production regression pin (10/03/2026): stacked branches feat/prune-dirty-gate and
feat/pr-queue-clarity (same commit) were each tombstoned as "superseded by" the other,
and the tombstone ignored uncommitted work. Covers: stacked lineage, mutual supersession,
dirty-worktree tombstone gate, stash bypass, rollback/recovery after commit.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_topology_core.disposition import compute_dispositions
from tools.pr_topology_core.matrix import (
    BranchDiff,
    TopologyMatrix,
    _detect_superseded,
    compute_topology_matrix,
)


class TestSupersessionGuards(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pr_supersede_"))
        self.git("init", "-b", "main")
        self.git("config", "user.name", "T")
        self.git("config", "user.email", "t@example.com")
        (self.dir / "a.txt").write_text("1\n")
        self.git("add", "-A")
        self.git("commit", "-m", "chore: base")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.dir, capture_output=True, text=True, check=True).stdout.strip()

    def test_stacked_branches_are_not_superseded_by_each_other(self):
        self.git("checkout", "-q", "-b", "feat/parent")
        (self.dir / "p.txt").write_text("p\n")
        self.git("add", "-A")
        self.git("commit", "-m", "feat: parent")
        self.git("branch", "feat/child")  # identical tip: stacked / same commit set
        self.git("checkout", "-q", "main")

        matrix = compute_topology_matrix(["feat/parent", "feat/child"], self.dir, "main")
        self.assertEqual(matrix.superseded_candidates, [])
        actions = {d.action for d in compute_dispositions(matrix).values()}
        self.assertNotIn("TOMBSTONE", actions)

    def test_diverged_duplicate_commit_sets_are_not_mutually_tombstoned(self):
        for name in ("feat/x", "feat/y"):
            self.git("checkout", "-q", "-b", name, "main")
            (self.dir / "same.txt").write_text("same\n")
            self.git("add", "-A")
            self.git("commit", "-m", "feat: same change", "--date", "2026-01-01T00:00:00")
            self.git("checkout", "-q", "main")
        matrix = compute_topology_matrix(["feat/x", "feat/y"], self.dir, "main")
        pairs = {(c["older_branch"], c["newer_branch"]) for c in _detect_superseded(
            ["feat/x", "feat/y"], matrix.branch_diffs, self.dir, "main")}
        self.assertFalse({("feat/x", "feat/y"), ("feat/y", "feat/x")} <= pairs, "mutual tombstone is forbidden")


class TestTombstoneDirtyGate(unittest.TestCase):
    def _matrix(self):
        m = TopologyMatrix(base_branch="main", branches=["old", "new"])
        m.branch_diffs["old"] = BranchDiff(branch="old", ahead=2, behind=0, files=["a.py"])
        m.branch_diffs["new"] = BranchDiff(branch="new", ahead=3, behind=0, files=["a.py"])
        m.superseded_candidates.append({"older_branch": "old", "newer_branch": "new"})
        return m

    def test_clean_superseded_branch_is_tombstoned(self):
        d = compute_dispositions(self._matrix(), worktree_info={"old": {"dirty": False, "authored_files_count": 0}})
        self.assertEqual(d["old"].action, "TOMBSTONE")

    def test_dirty_superseded_branch_must_commit_first(self):
        d = compute_dispositions(self._matrix(), worktree_info={"old": {"dirty": True, "authored_files_count": 5}})
        self.assertEqual(d["old"].action, "COMMIT_FIRST")
        self.assertIn("5 uncommitted authored", d["old"].reason)
        self.assertEqual(d["old"].target, "new")

    def test_stash_bypass_blocks_tombstone(self):
        d = compute_dispositions(self._matrix(), worktree_info={"old": {"dirty": False, "stash_count": 2}})
        self.assertEqual(d["old"].action, "COMMIT_FIRST")
        self.assertIn("stash", d["old"].reason)

    def test_recovery_after_commit_allows_tombstone(self):
        wt = {"old": {"dirty": True, "authored_files_count": 5}}
        self.assertEqual(compute_dispositions(self._matrix(), worktree_info=wt)["old"].action, "COMMIT_FIRST")
        wt = {"old": {"dirty": False, "authored_files_count": 0, "stash_count": 0}}
        self.assertEqual(compute_dispositions(self._matrix(), worktree_info=wt)["old"].action, "TOMBSTONE")

    def test_missing_worktree_info_keeps_legacy_behavior(self):
        self.assertEqual(compute_dispositions(self._matrix())["old"].action, "TOMBSTONE")


if __name__ == "__main__":
    unittest.main()
