"""
Unit and Integration Tests for PR Queue Inception Protection (Wave 1).
Validates branch birth detection via git reflog, 48-hour inception immunity leases,
elimination of the ancestor fallacy in supersession, and safe disposition handling.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_topology_core.birth import inspect_branch_birth
from tools.pr_topology_core.disposition import _evaluate_ahead_zero_branch
from tools.pr_topology_core.matrix import BranchDiff
from tools.pr_topology_core.supersession import evaluate_commit_supersession


class TestPRQueueInceptionProtection(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "core.logAllRefUpdates", "true"])
        self._git(["config", "user.name", "Inception Tester"])
        self._git(["config", "user.email", "tester@inception.local"])
        (self.repo_root / "README.md").write_text("# Test Inception Repo\n", encoding="utf-8")
        self._git(["add", "README.md"])
        self._git(["commit", "-m", "chore: initial commit on main"])

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
            timeout=30,
        )
        return res.stdout.strip()

    def test_inspect_branch_birth_fresh_branch(self):
        """Newly created branch has reflog birth info within 48-hour lease."""
        self._git(["checkout", "-b", "feat/fresh-inception", "main"])
        birth = inspect_branch_birth("feat/fresh-inception", root=self.repo_root)

        self.assertIsNotNone(birth)
        self.assertTrue(birth.is_inception_active, "Brand new branch must have is_inception_active == True")
        self.assertLess(birth.age_hours, 1.0, "New branch age must be less than 1 hour")
        self.assertIn("main", birth.source_ref, "Branch must record it was created from main")

    def test_ancestor_fallacy_eliminated_on_ahead_zero(self):
        """
        When main advances after a fresh branch is created, the fresh branch
        is trivially an ancestor of main at the base commit, but it MUST NOT
        be falsely classified as superseded by main.
        """
        self._git(["checkout", "-b", "feat/aidlc-council", "main"])

        # Advance main
        self._git(["checkout", "main"])
        (self.repo_root / "main_feature.py").write_text("# new feature on main\n", encoding="utf-8")
        self._git(["add", "main_feature.py"])
        self._git(["commit", "-m", "feat: advance main past branch base"])

        main_sha = self._git(["rev-parse", "main"])
        main_tree = self._git(["rev-parse", "main^{tree}"])
        branch_sha = self._git(["rev-parse", "feat/aidlc-council"])

        target, evidence, unique = evaluate_commit_supersession(
            branch="feat/aidlc-council",
            branch_sha=branch_sha,
            main_sha=main_sha,
            main_tree=main_tree,
            patch_idx={},
            root=self.repo_root,
            base_branch="main",
        )

        self.assertIsNone(
            target,
            "Fresh branch with ahead=0 must NOT be marked superseded just because main advanced",
        )
        self.assertEqual(evidence, [], "Evidence list should be empty for fresh branch")

    def test_evaluate_ahead_zero_active_worktree_inception(self):
        """
        Active worktree within 48h inception window must be marked KEEP,
        never PRUNE, even if behind main.
        """
        self._git(["checkout", "-b", "feat/aidlc-council", "main"])
        diff = BranchDiff(branch="feat/aidlc-council", ahead=0, behind=5)

        disp = _evaluate_ahead_zero_branch(
            branch="feat/aidlc-council",
            diff=diff,
            wt={"clean": True, "path": str(self.repo_root)},
            waves_info={"feat/aidlc-council": {"open_waves": "?"}},
            root=self.repo_root,
            main_sha="11112222",
            main_tree="33334444",
            base_branch="main",
        )

        self.assertEqual(disp.action, "KEEP")
        self.assertIn("rebase onto main", disp.reason)
        self.assertIn("behind 5 commits", disp.reason)
        self.assertIsNone(disp.superseded_by)

    def test_evaluate_ahead_zero_with_open_waves_question_mark(self):
        """
        Branch with open_waves='?' on an active worktree is in inception planning
        and must be kept.
        """
        self._git(["checkout", "-b", "feat/planning-branch", "main"])
        diff = BranchDiff(branch="feat/planning-branch", ahead=0, behind=0)

        disp = _evaluate_ahead_zero_branch(
            branch="feat/planning-branch",
            diff=diff,
            wt={"clean": True, "path": str(self.repo_root)},
            waves_info={"feat/planning-branch": {"open_waves": "?"}},
            root=self.repo_root,
            main_sha="11112222",
            main_tree="33334444",
            base_branch="main",
        )

        self.assertEqual(disp.action, "KEEP")
        self.assertIn("in progress", disp.reason)

    def test_evaluate_ahead_zero_prunes_only_without_worktree_and_stale(self):
        """
        Branches without active worktrees and outside inception lease can be pruned
        if truly abandoned.
        """
        diff = BranchDiff(branch="feat/abandoned-no-worktree", ahead=0, behind=0)
        disp = _evaluate_ahead_zero_branch(
            branch="feat/abandoned-no-worktree",
            diff=diff,
            wt=None,
            waves_info={"feat/abandoned-no-worktree": {"open_waves": "—"}},
            root=self.repo_root,
            main_sha="11112222",
            main_tree="33334444",
            base_branch="main",
        )

        self.assertEqual(disp.action, "PRUNE")
        self.assertIn("ahead==0", disp.reason)


if __name__ == "__main__":
    unittest.main()
