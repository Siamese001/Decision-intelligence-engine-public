"""
tests/test_pr_prune_dirty_gate_adversarial.py

Adversarial & Edge-Case Penetration Test Suite for Prune Dirty Gate:
Covers >= 3 adversarial attack vectors:
1. Stash bypass: worktree appears clean but contains stashed commits/work.
2. Review artifact / ephemeral ignore: verify review receipts don't block prune, but authored code files strictly block prune.
3. Finish / teardown race: cmd_finish and pruning tools fail closed when uncommitted changes exist.
4. Active wave protection: zero-commit branches with incomplete waves cannot be pruned.
5. Invariant enforcement: deterministic programmatic reasons (zero fabricated / hallucinated prose).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.pr_governance_core.worktree import cmd_finish
from tools.pr_topology_core.disposition import compute_dispositions
from tools.pr_topology_core.matrix import BranchDiff, TopologyMatrix


class TestPrPruneDirtyGateAdversarial(unittest.TestCase):
    """Adversarial stress-testing of branch pruning and worktree dirty gates."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_prune_adversarial_")
        self.repo_root = Path(self.test_dir)
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "Adversarial Tester"])
        self._run_git(["config", "user.email", "adversary@example.com"])

        readme = self.repo_root / "README.md"
        readme.write_text("# Adversarial Test Repo\n", encoding="utf-8")
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

    def test_adversarial_stash_bypass_detected(self):
        """
        Attack Vector 1: Developer/Agent stashes changes to make git status clean.
        Pruning engine must detect stash and classify branch as COMMIT_FIRST.
        """
        matrix = TopologyMatrix(base_branch="main", branches=["feat/stashed-work"])
        matrix.branch_diffs["feat/stashed-work"] = BranchDiff(
            branch="feat/stashed-work", ahead=0, behind=0, files=[]
        )
        worktree_info = {
            "feat/stashed-work": {
                "dirty": False,
                "summary": "clean",
                "dirty_files_count": 0,
                "stash_count": 2,
            }
        }

        dispositions = compute_dispositions(matrix, worktree_info=worktree_info)
        self.assertIn("feat/stashed-work", dispositions)
        self.assertEqual(dispositions["feat/stashed-work"].action, "COMMIT_FIRST")
        self.assertIn("stash", dispositions["feat/stashed-work"].reason.lower())

    def test_adversarial_ephemeral_vs_authored_files(self):
        """
        Attack Vector 2: Ephemeral test reports vs authored code.
        - Branch with only benign review receipts -> PRUNE
        - Branch with authored code files -> COMMIT_FIRST
        """
        matrix = TopologyMatrix(
            base_branch="main",
            branches=["feat/only-receipts", "feat/authored-code"],
        )
        matrix.branch_diffs["feat/only-receipts"] = BranchDiff(
            branch="feat/only-receipts", ahead=0, behind=5, files=[]
        )
        matrix.branch_diffs["feat/authored-code"] = BranchDiff(
            branch="feat/authored-code", ahead=0, behind=5, files=[]
        )

        worktree_info = {
            "feat/only-receipts": {
                "dirty": False,
                "summary": "clean (review receipts ignored)",
                "dirty_files_count": 0,
                "authored_files_count": 0,
            },
            "feat/authored-code": {
                "dirty": True,
                "summary": "dirty (49 files: modified + untracked)",
                "dirty_files_count": 49,
                "authored_files_count": 49,
            },
        }

        dispositions = compute_dispositions(matrix, worktree_info=worktree_info)
        self.assertEqual(dispositions["feat/only-receipts"].action, "PRUNE")
        self.assertEqual(dispositions["feat/authored-code"].action, "COMMIT_FIRST")
        self.assertIn("49", dispositions["feat/authored-code"].reason)
        self.assertIn("authored", dispositions["feat/authored-code"].reason.lower())

    def test_adversarial_worktree_finish_fails_closed_on_dirty(self):
        """
        Attack Vector 3: Teardown race. Attempting to teardown/prune a dirty worktree
        via cmd_finish without --force MUST fail closed with exit code 1, even if the branch
        is fully merged into main.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_dirty_target"
        self._run_git(["worktree", "add", str(wt_dir), "-b", "feat/dirty-target"])

        # Commit a real feature change on the branch
        feature_file = wt_dir / "feature.py"
        feature_file.write_text("feature = True\n", encoding="utf-8")
        self._run_git(["add", "feature.py"], cwd=wt_dir)
        self._run_git(["commit", "-m", "feat: implement feature"], cwd=wt_dir)

        # Merge branch into main cleanly so branch is genuinely merged
        self._run_git(["checkout", "main"])
        self._run_git(["merge", "--no-ff", "feat/dirty-target", "-m", "Merge feat/dirty-target"])

        # Now add uncommitted dirty work in the worktree
        dirty_file = wt_dir / "valuable_code.py"
        dirty_file.write_text("critical = True\n", encoding="utf-8")

        # Fake args for finish
        args = argparse.Namespace(
            branch="feat/dirty-target",
            force=False,
            delete_branch=True,
        )

        with mock.patch("tools.pr_governance_core.worktree.ROOT", self.repo_root):
            exit_code = cmd_finish(args)
            self.assertEqual(exit_code, 1, "cmd_finish must fail closed on dirty worktree")
            self.assertTrue(wt_dir.exists(), "Worktree directory must be preserved on dirty abort")
            self.assertTrue(dirty_file.exists(), "Dirty files must not be wiped")

    def test_adversarial_open_wave_uncompleted_protection(self):
        """
        Attack Vector 4: Branch has ahead==0 and worktree clean, but wave plan declares open work.
        MUST NOT be pruned. Must be marked KEEP.
        """
        matrix = TopologyMatrix(base_branch="main", branches=["feat/wave-in-progress"])
        matrix.branch_diffs["feat/wave-in-progress"] = BranchDiff(
            branch="feat/wave-in-progress", ahead=0, behind=12, files=[]
        )
        worktree_info = {
            "feat/wave-in-progress": {
                "dirty": False,
                "summary": "clean",
                "dirty_files_count": 0,
            }
        }
        waves_info = {
            "feat/wave-in-progress": {
                "open_waves": "3, 4",
                "completed_waves": "1, 2",
            }
        }

        dispositions = compute_dispositions(
            matrix, worktree_info=worktree_info, waves_info=waves_info
        )
        self.assertEqual(dispositions["feat/wave-in-progress"].action, "KEEP")
        self.assertIn("Wave 3, 4 open", dispositions["feat/wave-in-progress"].reason)


if __name__ == "__main__":
    unittest.main()
