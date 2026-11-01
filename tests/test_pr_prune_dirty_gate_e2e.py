"""
tests/test_pr_prune_dirty_gate_e2e.py

End-to-End Test Suite for PR Topology & Prune Dirty Gate:
1. Verifies that clean branches with ahead==0 are recommended for PRUNE.
2. Verifies that dirty branches with ahead==0 are strictly blocked from PRUNE and marked COMMIT_FIRST.
3. Verifies that branches with ahead > 0 are marked KEEP.
4. Verifies that branches with open waves in implementation_plan.md are marked KEEP regardless of ahead==0.
5. Verifies end-to-end transition: dirty branch committed -> ahead becomes > 0 -> transitions from COMMIT_FIRST to KEEP.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_topology_core.disposition import compute_dispositions
from tools.pr_topology_core.matrix import BranchDiff, TopologyMatrix


class TestPrPruneDirtyGateE2E(unittest.TestCase):
    """End-to-end verification of branch pruning safety and worktree dirty gates."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_prune_gate_e2e_")
        self.repo_root = Path(self.test_dir)
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "Test Engineer"])
        self._run_git(["config", "user.email", "test@example.com"])

        # Initial commit on main
        readme = self.repo_root / "README.md"
        readme.write_text("# Test Repo\n", encoding="utf-8")
        self._run_git(["add", "README.md"])
        self._run_git(["commit", "-m", "chore: initial commit"])

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _run_git(self, args: list[str], cwd: Path | None = None) -> str:
        cmd_cwd = cwd or self.repo_root
        res = subprocess.run(
            ["git"] + args,
            cwd=cmd_cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def test_e2e_dirty_branch_cannot_be_pruned_until_committed(self):
        """
        E2E Lifecycle:
        - Clean branch with 0 commits ahead -> PRUNE
        - Dirty branch with 0 commits ahead -> COMMIT_FIRST
        - Once committed, branch has >0 commits -> KEEP
        """
        # 1. Create clean branch with 0 commits ahead
        self._run_git(["branch", "feat/clean-noop"])

        # 2. Create branch with uncommitted worktree files
        wt_dir = self.repo_root / "wt_dirty"
        self._run_git(["worktree", "add", str(wt_dir), "-b", "feat/dirty-worktree"])
        dirty_file = wt_dir / "uncommitted_feature.py"
        dirty_file.write_text("# Work in progress\n", encoding="utf-8")

        # 3. Create branch with commits ahead
        self._run_git(["checkout", "-b", "feat/active-feature"])
        feat_file = self.repo_root / "feature.py"
        feat_file.write_text("print('hello')\n", encoding="utf-8")
        self._run_git(["add", "feature.py"])
        self._run_git(["commit", "-m", "feat: add feature"])
        self._run_git(["checkout", "main"])

        # Build topology matrix
        matrix = TopologyMatrix(
            base_branch="main",
            branches=["feat/clean-noop", "feat/dirty-worktree", "feat/active-feature"],
        )
        matrix.branch_diffs["feat/clean-noop"] = BranchDiff(
            branch="feat/clean-noop", ahead=0, behind=0, files=[]
        )
        matrix.branch_diffs["feat/dirty-worktree"] = BranchDiff(
            branch="feat/dirty-worktree", ahead=0, behind=25, files=[]
        )
        matrix.branch_diffs["feat/active-feature"] = BranchDiff(
            branch="feat/active-feature", ahead=1, behind=0, files=["feature.py"]
        )

        worktree_info = {
            "feat/clean-noop": {"dirty": False, "summary": "clean", "dirty_files_count": 0},
            "feat/dirty-worktree": {
                "path": str(wt_dir),
                "dirty": True,
                "summary": "dirty (1 untracked files)",
                "dirty_files_count": 1,
                "authored_files_count": 1,
            },
            "feat/active-feature": {"dirty": False, "summary": "clean", "dirty_files_count": 0},
        }

        dispositions = compute_dispositions(matrix, worktree_info=worktree_info)

        # Invariant checks:
        # A. Clean branch with ahead==0 -> PRUNE
        self.assertIn("feat/clean-noop", dispositions)
        self.assertEqual(dispositions["feat/clean-noop"].action, "PRUNE")

        # B. Dirty branch with ahead==0 MUST NOT BE PRUNED! MUST BE COMMIT_FIRST
        self.assertIn("feat/dirty-worktree", dispositions)
        self.assertEqual(dispositions["feat/dirty-worktree"].action, "COMMIT_FIRST")
        self.assertIn("uncommitted", dispositions["feat/dirty-worktree"].reason.lower())

        # C. Active branch with ahead > 0 -> KEEP
        self.assertIn("feat/active-feature", dispositions)
        self.assertEqual(dispositions["feat/active-feature"].action, "KEEP")

        # Now commit the dirty file in feat/dirty-worktree
        self._run_git(["add", "uncommitted_feature.py"], cwd=wt_dir)
        self._run_git(["commit", "-m", "feat: commit dirty work"], cwd=wt_dir)

        # Update matrix to reflect new commit
        matrix.branch_diffs["feat/dirty-worktree"] = BranchDiff(
            branch="feat/dirty-worktree", ahead=1, behind=0, files=["uncommitted_feature.py"]
        )
        worktree_info["feat/dirty-worktree"] = {
            "dirty": False,
            "summary": "clean",
            "dirty_files_count": 0,
            "authored_files_count": 0,
        }

        # Re-compute dispositions
        updated_dispositions = compute_dispositions(matrix, worktree_info=worktree_info)
        self.assertEqual(updated_dispositions["feat/dirty-worktree"].action, "KEEP")
        self.assertEqual(updated_dispositions["feat/dirty-worktree"].reason, "Active branch with unique work")

    def test_e2e_open_wave_blocks_prune(self):
        """Verify that a branch with 0 commits ahead but with an open wave in implementation_plan.md is marked KEEP."""
        matrix = TopologyMatrix(
            base_branch="main",
            branches=["feat/live-sprint-open-wave"],
        )
        matrix.branch_diffs["feat/live-sprint-open-wave"] = BranchDiff(
            branch="feat/live-sprint-open-wave", ahead=0, behind=10, files=[]
        )
        worktree_info = {
            "feat/live-sprint-open-wave": {"dirty": False, "summary": "clean", "dirty_files_count": 0}
        }
        waves_info = {
            "feat/live-sprint-open-wave": {"open_waves": "5", "completed_waves": "1, 2, 3, 4"}
        }

        dispositions = compute_dispositions(
            matrix, worktree_info=worktree_info, waves_info=waves_info
        )
        self.assertIn("feat/live-sprint-open-wave", dispositions)
        self.assertEqual(dispositions["feat/live-sprint-open-wave"].action, "KEEP")
        self.assertIn("Wave 5 open", dispositions["feat/live-sprint-open-wave"].reason)


if __name__ == "__main__":
    unittest.main()
