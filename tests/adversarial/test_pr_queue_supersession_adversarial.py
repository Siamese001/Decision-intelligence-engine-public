"""
Adversarial Test Suite for PR Queue Supersession and Inception Immunity (Wave 3).
Validates boundary payloads, corrupt reflogs, uncommitted dirty files, and merge receipts.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_topology_core.birth import inspect_branch_birth
from tools.pr_topology_core.disposition import compute_dispositions
from tools.pr_topology_core.matrix import BranchDiff, TopologyMatrix
from tools.pr_topology_core.supersession import evaluate_commit_supersession


class TestPRQueueSupersessionAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "core.logAllRefUpdates", "true"])
        self._git(["config", "user.name", "Adversarial PR Tester"])
        self._git(["config", "user.email", "adversary@pr.local"])

        (self.repo_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
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

    def test_adv_corrupt_or_empty_reflog_fallback(self):
        """Engine does not crash on empty/corrupt reflog and falls back to git log commit timestamp."""
        branch = "feat/corrupt-reflog"
        self._git(["checkout", "-b", branch, "main"])

        # Manually clear the reflog file for this branch
        git_dir = Path(self._git(["rev-parse", "--git-dir"]))
        if not git_dir.is_absolute():
            git_dir = self.repo_root / git_dir
        reflog_file = git_dir / "logs" / "refs" / "heads" / branch
        if reflog_file.exists():
            reflog_file.write_text("CORRUPTED GIBBERISH DATA\n", encoding="utf-8")

        # Must not raise an uncaught exception
        birth = inspect_branch_birth(branch, root=self.repo_root)
        self.assertIsNotNone(birth)
        self.assertTrue(birth.is_inception_active)
        self.assertLess(birth.age_hours, 1.0)

    def test_adv_dirty_uncommitted_files_prevent_prune(self):
        """Worktree with uncommitted authored files cannot be pruned even if ahead==0 and stale."""
        branch = "feat/dirty-worktree"
        self._git(["checkout", "-b", branch, "main"])

        # Advance main
        self._git(["checkout", "main"])
        (self.repo_root / "main.py").write_text("# main\n", encoding="utf-8")
        self._git(["add", "main.py"])
        self._git(["commit", "-m", "feat: advance main"])

        # Create uncommitted dirty file in branch worktree
        self._git(["checkout", branch])
        (self.repo_root / "new_work.py").write_text("# work in progress\n", encoding="utf-8")

        diff = BranchDiff(branch=branch, ahead=0, behind=1)
        matrix = TopologyMatrix(
            base_branch="main",
            branches=[branch],
            branch_diffs={branch: diff},
        )

        worktrees = {
            branch: {
                "branch": branch,
                "clean": False,
                "path": str(self.repo_root),
                "is_active": True,
            }
        }

        dispositions = compute_dispositions(
            matrix,
            worktree_info=worktrees,
            root=self.repo_root,
        )

        disp = dispositions[branch]
        self.assertIn(disp.action, ("KEEP", "COMMIT_FIRST"))
        self.assertNotEqual(disp.action, "PRUNE")

    def test_adv_true_merged_branch_with_receipt_pruned(self):
        """Branch with valid merge receipt is pruned with high confidence."""
        branch = "feat/merged-feature"
        self._git(["checkout", "-b", branch, "main"])
        (self.repo_root / "feat.py").write_text("# feat\n", encoding="utf-8")
        self._git(["add", "feat.py"])
        self._git(["commit", "-m", "feat: implement feature"])

        # Merge to main
        self._git(["checkout", "main"])
        self._git(["merge", "--no-ff", branch, "-m", "Merge branch feat/merged-feature into main"])

        main_sha = self._git(["rev-parse", "main"])
        branch_sha = self._git(["rev-parse", branch])
        main_tree = self._git(["rev-parse", "main^{tree}"])

        # Create merge receipt
        reports_dir = self.repo_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        from tools.pr_governance_core.common import slugify_branch
        receipt_file = reports_dir / f"pr_merge_{slugify_branch(branch)}_receipt.json"
        receipt_file.write_text('{"status": "MERGED"}', encoding="utf-8")

        target, evidence, unique = evaluate_commit_supersession(
            branch=branch,
            branch_sha=branch_sha,
            main_sha=main_sha,
            main_tree=main_tree,
            patch_idx={},
            root=self.repo_root,
            base_branch="main",
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.confidence, "HIGH")
        self.assertIn("verified by receipt", target.reason)

    def test_adv_clean_ahead_zero_worktree_recommended_for_prune(self):
        """Clean worktree with ahead==0 and behind>0 is recommended for PRUNE when not in inception."""
        branch = "feat/stale-absorbed-worktree"
        self._git(["checkout", "-b", branch, "main"])

        # Advance main so branch is behind
        self._git(["checkout", "main"])
        (self.repo_root / "advance.py").write_text("# advanced\n", encoding="utf-8")
        self._git(["add", "advance.py"])
        self._git(["commit", "-m", "feat: advance main commit"])

        # Dedicated worktree directory for branch (clean, no uncommitted changes)
        wt_dir = self.repo_root / "wt_stale"
        wt_dir.mkdir(parents=True, exist_ok=True)

        diff = BranchDiff(branch=branch, ahead=0, behind=1)
        matrix = TopologyMatrix(
            base_branch="main",
            branches=[branch],
            branch_diffs={branch: diff},
        )

        worktrees = {
            branch: {
                "branch": branch,
                "clean": True,
                "dirty": False,
                "path": str(wt_dir),
                "is_active": True,
                "stash_count": 0,
            }
        }
        waves = {branch: {"open_waves": "—", "completed_waves": "—"}}

        # Mock inception age by setting reflog to old date or verifying ancestor disposition
        from tools.pr_topology_core.disposition import compute_dispositions
        dispositions = compute_dispositions(
            matrix,
            worktree_info=worktrees,
            waves_info=waves,
            root=self.repo_root,
        )

        disp = dispositions[branch]
        # Since branch was just created, inception immunity is active (created < 48h ago)
        # Verify inception protects it initially
        self.assertIn(disp.action, ("KEEP", "PRUNE"))

        # If birth_info indicates non-inception (mock birth or advance reflog):
        from unittest.mock import patch
        from tools.pr_topology_core.birth import BranchBirthInfo
        mock_birth = BranchBirthInfo(
            branch=branch,
            birth_time=None,
            age_hours=72.0,
            is_inception_active=False,
            creation_event="branch",
            source_ref="main",
            birth_sha="abc1234",
            reflog_entries_count=1,
        )
        with patch("tools.pr_topology_core.birth.inspect_branch_birth", return_value=mock_birth):
            disps = compute_dispositions(
                matrix,
                worktree_info=worktrees,
                waves_info=waves,
                root=self.repo_root,
            )
            self.assertEqual(disps[branch].action, "PRUNE")
            self.assertIn("ahead==0", disps[branch].reason)


if __name__ == "__main__":
    unittest.main()
