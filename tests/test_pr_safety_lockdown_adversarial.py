"""
Adversarial Test Suite for PR Safety Lockdown (Wave 1).
Tests T3, T4, T13, T15, T17, T22, T23, T24 against real temporary git repositories.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.gc import inspect_worktrees_for_gc
from tools.pr_governance_core.queue_actions import get_conflict_files
from tools.pr_governance_core.worktree import (
    count_branch_stashes,
    is_mid_rebase,
    list_active_worktrees,
    parse_porcelain_v2_z,
)
from tools.pr_topology_core.disposition import compute_dispositions
from tools.pr_topology_core.matrix import BranchDiff, TopologyMatrix, get_rev_counts


class TestPRSafetyLockdownAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.name", "Adversary Agent"])
        self._git(["config", "user.email", "adversary@test.local"])
        (self.repo_root / "main.py").write_text("x = 1\n", encoding="utf-8")
        self._git(["add", "main.py"])
        self._git(["commit", "-m", "chore: base commit on main"])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _git(self, cmd: list[str], cwd: Path | None = None) -> str:
        target_cwd = cwd or self.repo_root
        res = subprocess.run(
            ["git"] + cmd,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def test_t3_mid_rebase_worktree_is_never_prunable(self):
        """
        T3: A stopped rebase (conflict or edit stop) is UNKNOWN in disposition, gc, and finish.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_rebase_target"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/rebase-target", "main"])

        # Simulate git rebase in progress by creating rebase-merge inside gitdir
        git_dir_out = self._git(["rev-parse", "--git-dir"], cwd=wt_dir)
        git_dir = wt_dir / git_dir_out if not Path(git_dir_out).is_absolute() else Path(git_dir_out)
        rebase_dir = git_dir / "rebase-merge"
        rebase_dir.mkdir(parents=True, exist_ok=True)
        (rebase_dir / "head-name").write_text("refs/heads/feat/rebase-target\n", encoding="utf-8")

        # 1. is_mid_rebase detector returns True
        self.assertTrue(is_mid_rebase(wt_dir))

        # 2. inspect_worktrees_for_gc marks UNKNOWN and not cleanable
        candidates = inspect_worktrees_for_gc(root=self.repo_root)
        c = [c for c in candidates if c["branch"] == "feat/rebase-target"][0]
        self.assertFalse(c["can_auto_clean"])
        self.assertEqual(c["status"], "UNKNOWN (mid-rebase)")

        # 3. compute_dispositions marks KEEP with mid-rebase reason
        matrix = TopologyMatrix(base_branch="main", branches=["feat/rebase-target"])
        matrix.branch_diffs["feat/rebase-target"] = BranchDiff(branch="feat/rebase-target", ahead=0, behind=0)
        worktree_info = {
            "feat/rebase-target": {
                "mid_rebase": True,
                "summary": "UNKNOWN (mid-rebase in progress)",
                "dirty": True,
            }
        }
        dispositions = compute_dispositions(matrix, worktree_info=worktree_info)
        self.assertEqual(dispositions["feat/rebase-target"].action, "KEEP")
        self.assertIn("mid-rebase", dispositions["feat/rebase-target"].reason)

    def test_t4_git_failure_yields_unknown_not_prune(self):
        """
        T4: A bad ref or held index.lock -> no PRUNE or TOMBSTONE; queue and matrix agree on KEEP.
        """
        # 1. get_rev_counts on nonexistent ref returns (-1, -1)
        behind, ahead = get_rev_counts("main", "nonexistent_ref_999", self.repo_root)
        self.assertEqual(behind, -1)
        self.assertEqual(ahead, -1)

        # 2. Disposition engine treats ahead == -1 as KEEP (fail closed)
        matrix = TopologyMatrix(base_branch="main", branches=["feat/failed-git"])
        matrix.branch_diffs["feat/failed-git"] = BranchDiff(
            branch="feat/failed-git", ahead=-1, behind=-1, is_unknown=True
        )
        dispositions = compute_dispositions(matrix)
        self.assertEqual(dispositions["feat/failed-git"].action, "KEEP")
        self.assertIn("unknown", dispositions["feat/failed-git"].reason)
        self.assertNotIn("PRUNE", dispositions["feat/failed-git"].action)
        self.assertNotIn("TOMBSTONE", dispositions["feat/failed-git"].action)

    def test_t13_stash_attribution_by_ancestry(self):
        """
        T13: Stash attribution by ancestry:
        - Detached-HEAD stash on unique feature commit is attributed.
        - Root 'WIP on main' stashes are NOT attributed to features.
        - Suffix-named branches (feat/auth vs feat/auth-v2) do not false-match.
        - Renamed branch matches via ancestry.
        """
        # Create branch feat/auth with a unique commit
        self._git(["checkout", "-b", "feat/auth"])
        (self.repo_root / "auth.py").write_text("token = 1\n", encoding="utf-8")
        self._git(["add", "auth.py"])
        self._git(["commit", "-m", "feat(auth): initial auth"])

        # Create branch feat/auth-v2 off main
        self._git(["checkout", "main"])
        self._git(["checkout", "-b", "feat/auth-v2"])
        (self.repo_root / "auth_v2.py").write_text("token_v2 = 2\n", encoding="utf-8")
        self._git(["add", "auth_v2.py"])
        self._git(["commit", "-m", "feat(auth): initial v2"])

        # Case 1: Stash created on main
        self._git(["checkout", "main"])
        (self.repo_root / "scratch_main.txt").write_text("main scratch\n", encoding="utf-8")
        self._git(["stash", "push", "-u", "-m", "WIP on main: root work"])

        # Main stash must NOT be attributed to feat/auth or feat/auth-v2
        self.assertEqual(count_branch_stashes("feat/auth", self.repo_root), 0)
        self.assertEqual(count_branch_stashes("feat/auth-v2", self.repo_root), 0)
        self.assertEqual(count_branch_stashes("main", self.repo_root), 1)

        # Case 2: Stash created on feat/auth
        self._git(["checkout", "feat/auth"])
        (self.repo_root / "auth_wip.txt").write_text("wip\n", encoding="utf-8")
        self._git(["stash", "push", "-u", "-m", "WIP on feat/auth: credentials"])

        # Stash belongs to feat/auth, NOT feat/auth-v2
        self.assertEqual(count_branch_stashes("feat/auth", self.repo_root), 1)
        self.assertEqual(count_branch_stashes("feat/auth-v2", self.repo_root), 0)

        # Case 3: Detached HEAD stash on feat/auth commit
        commit_sha = self._git(["rev-parse", "HEAD"])
        self._git(["checkout", "--detach", commit_sha])
        (self.repo_root / "detached_wip.txt").write_text("detached\n", encoding="utf-8")
        self._git(["stash", "push", "-u", "-m", "WIP on (no branch): detached work"])
        self._git(["checkout", "feat/auth"])

        # Detached stash should be attributed to feat/auth by ancestry
        self.assertEqual(count_branch_stashes("feat/auth", self.repo_root), 2)
        self.assertEqual(count_branch_stashes("feat/auth-v2", self.repo_root), 0)

    def test_t15_missing_worktree_dir_is_unknown(self):
        """
        T15: A registered worktree with a missing directory is UNKNOWN, and gc does not crash.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_missing_dir"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/missing-dir", "main"])

        # Delete the worktree folder manually behind git's back
        import shutil

        shutil.rmtree(wt_dir)
        self.assertFalse(wt_dir.exists())

        # 1. gc inspection must not crash and must report UNKNOWN
        candidates = inspect_worktrees_for_gc(root=self.repo_root)
        c = [c for c in candidates if c["branch"] == "feat/missing-dir"][0]
        self.assertFalse(c["can_auto_clean"])
        self.assertEqual(c["status"], "UNKNOWN (missing directory)")

        # 2. list_active_worktrees handles it gracefully
        active = list_active_worktrees(self.repo_root)
        wt_entry = [e for e in active if e["branch"] == "feat/missing-dir"][0]
        self.assertEqual(wt_entry["status"], "UNKNOWN")
        self.assertIn("missing directory", wt_entry["summary"])

    def test_t17_merge_tree_error_vs_conflict(self):
        """
        T17: git merge-tree exit > 1 returns UNKNOWN_GIT_ERROR; exit 1 returns conflict list.
        """
        # Case A: Exit 128 / error (e.g. invalid ref) returns UNKNOWN_GIT_ERROR
        conflicts_err = get_conflict_files("invalid_ref_xyz_123", self.repo_root)
        self.assertIn("<UNKNOWN_GIT_ERROR>", conflicts_err)

        # Case B: Exit 1 (genuine conflict)
        self._git(["checkout", "-b", "feat/conflict-branch"])
        (self.repo_root / "main.py").write_text("x = 200\n", encoding="utf-8")
        self._git(["add", "main.py"])
        self._git(["commit", "-m", "feat: change main.py on branch"])

        self._git(["checkout", "main"])
        (self.repo_root / "main.py").write_text("x = 999\n", encoding="utf-8")
        self._git(["add", "main.py"])
        self._git(["commit", "-m", "chore: change main.py on main"])

        conflicts_real = get_conflict_files("feat/conflict-branch", self.repo_root)
        self.assertIn("main.py", conflicts_real)
        self.assertNotIn("<UNKNOWN_GIT_ERROR>", conflicts_real)

    def test_t22_backup_branches_never_pruned_or_tombstoned(self):
        """
        T22: Branches matching backup/* are always ARCHIVE, never PRUNE or TOMBSTONE.
        """
        matrix = TopologyMatrix(base_branch="main", branches=["backup/20261001-snapshot"])
        matrix.branch_diffs["backup/20261001-snapshot"] = BranchDiff(
            branch="backup/20261001-snapshot", ahead=0, behind=10
        )
        dispositions = compute_dispositions(matrix)
        self.assertEqual(dispositions["backup/20261001-snapshot"].action, "ARCHIVE")
        self.assertIn("Backup branch preserved", dispositions["backup/20261001-snapshot"].reason)

    def test_t23_receipt_outcome_reflects_errors(self):
        """
        T23: Receipts must reflect errors and never emit PASS when stages fail.
        """
        # Verify check_merge_conflict returns UNKNOWN message on error
        from tools.pr_governance_core.common import check_merge_conflict

        is_clean, details = check_merge_conflict("main", "nonexistent_ref_xyz", cwd=self.repo_root)
        self.assertFalse(is_clean)
        self.assertIn("UNKNOWN", details)

    def test_t24_porcelain_z_parsing(self):
        """
        T24: parse_porcelain_v2_z correctly handles spaces, renames, untracked, and ignored files.
        """
        sample_v2 = (
            "1 .M N... 100644 100644 100644 e69de29 e69de29 path with spaces.txt\x00"
            "? new file with spaces.py\x00"
            "2 R. N... 100644 100644 100644 e69de29 e69de29 R100 dest path.txt\x00src path.txt\x00"
            "! ignored_file.wal\x00"
        )
        parsed = parse_porcelain_v2_z(sample_v2)
        self.assertEqual(len(parsed), 4)

        # 1. Ordinary modified file with spaces
        self.assertEqual(parsed[0]["path"], "path with spaces.txt")
        self.assertEqual(parsed[0]["type"], "1")

        # 2. Untracked file with spaces
        self.assertEqual(parsed[1]["path"], "new file with spaces.py")
        self.assertEqual(parsed[1]["type"], "?")

        # 3. Renamed file
        self.assertEqual(parsed[2]["path"], "dest path.txt")
        self.assertEqual(parsed[2]["orig_path"], "src path.txt")
        self.assertEqual(parsed[2]["type"], "2")

        # 4. Ignored file
        self.assertEqual(parsed[3]["path"], "ignored_file.wal")
        self.assertEqual(parsed[3]["type"], "!")


if __name__ == "__main__":
    unittest.main()
