"""
tests/test_pr_lifecycle_fastpath_e2e.py

End-to-End Acceptance Tests for Wave 4:
- O(1) Worktree Lookup (resolve_branch_worktree)
- Porcelain Whitespace Preservation in run_git
- Disjoint Rebase Attestation Reuse
- Unified sync -> review -> merge Fast-Path
"""
import argparse
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.pr_governance_core.common import ROOT, run_git, slugify_branch
from tools.pr_governance_core.lifecycle import (
    _resolve_pr_review_receipt,
    _validate_merge_receipt,
    cmd_review,
)
from tools.pr_governance_core.sync import cmd_sync
from tools.pr_governance_core.worktree import (
    clear_branch_worktree_cache,
    resolve_branch_worktree,
)
from tools.readiness_planner import validate_readiness_cache_binding


class TestWorktreeFastLookupE2E(unittest.TestCase):
    """Verifies O(1) worktree resolution and memoization."""

    def setUp(self) -> None:
        clear_branch_worktree_cache()

    def tearDown(self) -> None:
        clear_branch_worktree_cache()

    def test_resolve_branch_worktree_canonical_hit(self) -> None:
        """Verifies resolve_branch_worktree detects canonical slug path directly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            branch = "feat/fast-lookup-test"
            slug = slugify_branch(branch)
            wt_dir = root / ".worktrees" / slug
            wt_dir.mkdir(parents=True)
            (wt_dir / ".git").write_text("gitdir: /fake/git/dir\n", encoding="utf-8")

            with patch("tools.pr_governance_core.worktree.run_git") as mock_git, \
                 patch("tools.pr_governance_core.worktree.subprocess.run") as mock_sub:
                def fake_git(args, cwd=None, **kwargs):
                    if "--abbrev-ref" in args:
                        return branch
                    if "rev-parse" in args and "HEAD" in args:
                        return "12345678abcdef"
                    if "status" in args:
                        return ""
                    return ""

                mock_git.side_effect = fake_git
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = b""
                mock_sub.return_value = mock_proc

                entry = resolve_branch_worktree(root, branch)
                self.assertIsNotNone(entry)
                self.assertEqual(entry["branch"], branch)
                self.assertEqual(entry["path"], str(wt_dir))
                self.assertFalse(entry["dirty"])

                # Second call should hit in-process RAM cache without git calls
                mock_git.reset_mock()
                cached_entry = resolve_branch_worktree(root, branch)
                self.assertEqual(cached_entry, entry)
                mock_git.assert_not_called()

    def test_clear_branch_worktree_cache(self) -> None:
        """Verifies clear_branch_worktree_cache evicts memoized entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            branch = "feat/cache-clear"
            slug = slugify_branch(branch)
            wt_dir = root / ".worktrees" / slug
            wt_dir.mkdir(parents=True)
            (wt_dir / ".git").write_text("gitdir: /fake/git/dir\n", encoding="utf-8")

            with patch("tools.pr_governance_core.worktree.run_git", side_effect=lambda args, **k: branch if "--abbrev-ref" in args else "12345678"):
                resolve_branch_worktree(root, branch)

            from tools.pr_governance_core.worktree import _BRANCH_WORKTREE_CACHE
            self.assertGreater(len(_BRANCH_WORKTREE_CACHE), 0)
            clear_branch_worktree_cache()
            self.assertEqual(len(_BRANCH_WORKTREE_CACHE), 0)


class TestPorcelainPreservationE2E(unittest.TestCase):
    """Verifies that run_git preserves leading whitespace for porcelain parsing."""

    def test_run_git_preserves_porcelain_leading_whitespace(self) -> None:
        """Verifies that leading space in porcelain output is preserved by rstrip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("tools.pr_governance_core.common.subprocess.run") as mock_sub:
                mock_proc = MagicMock()
                # Leading space indicates worktree modified (not staged)
                mock_proc.stdout = " M dual_pipeline/core.py\n?? untracked.txt\n"
                mock_sub.return_value = mock_proc

                out = run_git(["status", "--porcelain"], cwd=root)
                lines = out.splitlines()
                self.assertEqual(len(lines), 2)
                self.assertTrue(lines[0].startswith(" M "), f"Leading space lost: '{lines[0]}'")


class TestDisjointRebaseAttestationReuseE2E(unittest.TestCase):
    """Verifies attestation reuse across disjoint rebases."""

    def test_disjoint_rebase_cache_binding_reused(self) -> None:
        """Mainline commits affecting unrelated files do not invalidate readiness attestation."""
        cached_entry = {
            "terminal_outcome": "PASS",
            "head_commit_sha": "feat_tip_123",
            "base_commit_sha": "main_base_old",
            "manifest_version": "1.0.0",
            "is_live": True,
            "target_files": ["dual_pipeline/core.py"],
            "stages": [
                {"dependencies": ["dual_pipeline/core.py", "dual_pipeline/parameters.py"]}
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("tools.readiness_planner.get_mainline_diff_files") as mock_diff:
                # Main diff touches only unrelated docs
                mock_diff.return_value = ["docs/guide.md", "reports/old_run.json"]

                valid, reason = validate_readiness_cache_binding(
                    cached_entry,
                    current_head_sha="feat_tip_123",
                    current_base_sha="main_base_new",
                    current_manifest_version="1.0.0",
                    is_live_mode=True,
                    repo_root=root,
                )
                self.assertTrue(valid, f"Expected VALID_REUSE, got {reason}")
                self.assertEqual(reason, "VALID_REUSE")

    def test_intersecting_rebase_cache_binding_invalidated(self) -> None:
        """Mainline commits touching dependent files invalidate readiness attestation."""
        cached_entry = {
            "terminal_outcome": "PASS",
            "head_commit_sha": "feat_tip_123",
            "base_commit_sha": "main_base_old",
            "manifest_version": "1.0.0",
            "is_live": True,
            "target_files": ["dual_pipeline/core.py"],
            "stages": [
                {"dependencies": ["dual_pipeline/core.py"]}
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("tools.readiness_planner.get_mainline_diff_files") as mock_diff:
                # Main diff intersects with dual_pipeline/core.py
                mock_diff.return_value = ["dual_pipeline/core.py"]

                valid, reason = validate_readiness_cache_binding(
                    cached_entry,
                    current_head_sha="feat_tip_123",
                    current_base_sha="main_base_new",
                    current_manifest_version="1.0.0",
                    is_live_mode=True,
                    repo_root=root,
                )
                self.assertFalse(valid)
                self.assertIn("intersecting mainline changes", reason)


class TestSyncReviewFastPathE2E(unittest.TestCase):
    """Verifies that cmd_sync produces a receipt that cmd_review reuses immediately."""

    @patch("tools.pr_governance_core.sync.GovernedMergeQueueLock")
    @patch("tools.pr_governance_core.sync.subprocess.run")
    @patch("tools.pr_governance_core.sync.run_git")
    @patch("tools.pr_governance_core.sync.get_feature_branches")
    def test_sync_emits_review_receipt_and_review_reuses_it(
        self, mock_branches, mock_git, mock_sub, mock_lock
    ) -> None:
        """Verifies end-to-end receipt flow between sync and review."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            branch = "feat/sync-fastpath"
            slug = slugify_branch(branch)
            mock_branches.return_value = [branch]

            wt_dir = root / ".worktrees" / slug
            wt_dir.mkdir(parents=True)
            (wt_dir / ".git").write_text("gitdir: /fake/git/dir\n", encoding="utf-8")

            # Mock git behaviors for sync
            def git_side_effect(args, cwd=None, **kwargs):
                if "--abbrev-ref" in args:
                    return branch
                if "rev-parse" in args:
                    if "main" in args:
                        return "main_base_sha"
                    return "feat_head_sha"
                if "status" in args:
                    return ""
                if "diff" in args:
                    return "dual_pipeline/core.py\n"
                return ""

            mock_git.side_effect = git_side_effect

            # Mock readiness run subprocess as successful and status check as clean
            def sub_side_effect(cmd, *args, **kwargs):
                proc = MagicMock()
                proc.returncode = 0
                if "status" in cmd:
                    proc.stdout = ""
                else:
                    proc.stdout = "ALL 32 GATES PASSED\n"
                return proc
            mock_sub.side_effect = sub_side_effect

            # Run cmd_sync
            args = argparse.Namespace(
                branch=branch,
                repo_root=root,
                skip_readiness=False,
                fail_fast=False,
                full=False,
                dry_run=False,
                prune_caches=False,
            )
            sync_ret = cmd_sync(args)
            self.assertEqual(sync_ret, 0)

            # Verify pr_review receipt was created by sync
            receipt_file = root / "reports" / f"pr_review_{slug}_receipt.json"
            self.assertTrue(receipt_file.exists(), "pr_review receipt must be created by sync")
            data = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(data["terminal_outcome"], "PASS")
            self.assertEqual(data["head_sha"], "feat_head_sha")

            # Now run cmd_review on the same branch
            with patch("tools.pr_governance_core.lifecycle.ROOT", root), \
                 patch("tools.pr_governance_core.lifecycle.run_git") as mock_rev_git, \
                 patch("tools.pr_governance_core.lifecycle.inspect_branch_status") as mock_status, \
                 patch("tools.pr_governance_core.lifecycle.get_feature_branches", return_value=[branch]):

                mock_status.return_value = {
                    "branch": branch,
                    "ahead": 1,
                    "behind": 0,
                    "clean_merge": True,
                    "readiness_receipt_pass": True,
                }

                def rev_git_mock(args, cwd=None, **kwargs):
                    if "rev-parse" in args:
                        if "main" in args:
                            return "main_base_sha"
                        return "feat_head_sha"
                    if "merge-base" in args:
                        return "main_base_sha"
                    return ""

                mock_rev_git.side_effect = rev_git_mock

                rev_args = argparse.Namespace(branch=branch, full=False)
                rev_ret = cmd_review(rev_args)
                self.assertEqual(rev_ret, 0, "cmd_review must succeed via fast-path")

                # Verify merge validation also passes
                ok = _validate_merge_receipt(branch, slug, "feat_head_sha")
                self.assertTrue(ok, "Merge validation must pass with fast-path receipt")


if __name__ == "__main__":
    unittest.main()
