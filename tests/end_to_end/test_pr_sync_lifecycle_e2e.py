#!/usr/bin/env python3
"""tests/end_to_end/test_pr_sync_lifecycle_e2e.py

End-to-End tests for Stage A: Branch Alignment & Post-Rebase Readiness (Wave 2).

Verifies:
1. cmd_sync rebases branch behind main cleanly under lock and establishes linear history.
2. cmd_sync triages merge conflicts and aborts cleanly without dirtying the worktree.
3. verify_main_readiness rejects unaligned branches (behind > 0) unless --allow-behind is passed.
4. cmd_sync rejects dirty worktrees with uncommitted modifications before rebase.
5. cmd_sync short-circuits gracefully when branch is already up-to-date with main.
6. rebase_orchestrator.resolve_worktree_for_branch resolves .worktrees/<slug> properly.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.pr_governance_core.common import compute_first_parent_sync
from tools.pr_governance_core.sync import cmd_sync, _resolve_worktree
from tools.rebase_orchestrator import resolve_worktree_for_branch
import tools.verify_main_readiness as vmr_module


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


class PRSyncLifecycleE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_sync_lifecycle_e2e_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        # Initialize clean git repo with main branch
        _run_git(["init", "-b", "main"], self.repo_root)
        _run_git(["config", "user.name", "Sync Test Bot"], self.repo_root)
        _run_git(["config", "user.email", "sync-bot@example.com"], self.repo_root)
        _run_git(["config", "commit.gpgsign", "false"], self.repo_root)

        readme = self.repo_root / "README.md"
        readme.write_text("# Sync Test Repo\n", encoding="utf-8")
        _run_git(["add", "README.md"], self.repo_root)
        _run_git(["commit", "-m", "chore: initial commit"], self.repo_root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_sync_clean_rebase_e2e(self) -> None:
        """Vector 1: Branch 2 commits behind main rebases cleanly onto main."""
        branch = "feat/sync-feature"
        _run_git(["branch", branch], self.repo_root)

        # Provision worktree for feature branch
        wt_dir = self.repo_root / ".worktrees" / "feat_sync_feature"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", str(wt_dir), branch], self.repo_root)

        # Commit on feature branch in worktree
        f1 = wt_dir / "feature.txt"
        f1.write_text("feature content\n", encoding="utf-8")
        _run_git(["add", "feature.txt"], wt_dir)
        _run_git(["commit", "-m", "feat(sync): add feature file"], wt_dir)

        # Advance main by 2 commits in primary repo
        m1 = self.repo_root / "main1.txt"
        m1.write_text("main advance 1\n", encoding="utf-8")
        _run_git(["add", "main1.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: main advance 1"], self.repo_root)

        m2 = self.repo_root / "main2.txt"
        m2.write_text("main advance 2\n", encoding="utf-8")
        _run_git(["add", "main2.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: main advance 2"], self.repo_root)

        main_head = _run_git(["rev-parse", "main"], self.repo_root).stdout.strip()

        # Checkpoint: branch is 2 commits behind main
        behind, ahead = compute_first_parent_sync("main", branch, cwd=wt_dir)
        self.assertEqual(behind, 2)
        self.assertEqual(ahead, 1)

        # Execute cmd_sync with skip_readiness to verify rebase mechanics
        args = argparse.Namespace(
            branch=branch,
            repo_root=self.repo_root,
            skip_readiness=True,
            fail_fast=False,
            full=False,
            dry_run=False,
        )
        rc = cmd_sync(args)
        self.assertEqual(rc, 0)

        # Verify linear history: merge-base of branch with main is now main's HEAD
        fork_point = _run_git(["merge-base", "HEAD", "main"], wt_dir).stdout.strip()
        self.assertEqual(fork_point, main_head)

        behind_after, ahead_after = compute_first_parent_sync("main", branch, cwd=wt_dir)
        self.assertEqual(behind_after, 0)
        self.assertEqual(ahead_after, 1)

        # Verify all files present
        self.assertTrue((wt_dir / "feature.txt").is_file())
        self.assertTrue((wt_dir / "main1.txt").is_file())
        self.assertTrue((wt_dir / "main2.txt").is_file())

    def test_02_sync_conflict_aborts_cleanly_e2e(self) -> None:
        """Vector 2: Merge conflict is safely detected and aborts without dirtying worktree."""
        conflict_file = self.repo_root / "shared.txt"
        conflict_file.write_text("initial shared content\n", encoding="utf-8")
        _run_git(["add", "shared.txt"], self.repo_root)
        _run_git(["commit", "-m", "chore: add shared file"], self.repo_root)

        branch = "feat/conflict-branch"
        _run_git(["branch", branch], self.repo_root)
        wt_dir = self.repo_root / ".worktrees" / "feat_conflict_branch"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", str(wt_dir), branch], self.repo_root)

        # Branch edits shared.txt
        (wt_dir / "shared.txt").write_text("branch conflicting line\n", encoding="utf-8")
        _run_git(["commit", "-am", "feat: branch change on shared file"], wt_dir)

        # Main edits shared.txt with conflicting content
        (self.repo_root / "shared.txt").write_text("main conflicting line\n", encoding="utf-8")
        _run_git(["commit", "-am", "fix: main change on shared file"], self.repo_root)

        # Execute cmd_sync
        args = argparse.Namespace(
            branch=branch,
            repo_root=self.repo_root,
            skip_readiness=True,
            fail_fast=False,
            full=False,
            dry_run=False,
        )
        rc = cmd_sync(args)
        self.assertEqual(rc, 1)

        # Verify worktree is pristine and not left mid-rebase
        status_proc = _run_git(["status", "--porcelain"], wt_dir)
        self.assertEqual(status_proc.stdout.strip(), "")

        # Check git rebase state files do not exist
        git_dir = Path(_run_git(["rev-parse", "--git-dir"], wt_dir).stdout.strip())
        self.assertFalse((git_dir / "rebase-apply").exists())
        self.assertFalse((git_dir / "rebase-merge").exists())

    def test_03_verify_readiness_rejects_unaligned_branch_e2e(self) -> None:
        """Vector 3: verify_main_readiness fails-fast when behind main unless allow_behind=True."""
        branch = "feat/unaligned-branch"
        _run_git(["branch", branch], self.repo_root)
        wt_dir = self.repo_root / ".worktrees" / "feat_unaligned_branch"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", str(wt_dir), branch], self.repo_root)

        # Advance main
        (self.repo_root / "advance.txt").write_text("advance\n", encoding="utf-8")
        _run_git(["add", "advance.txt"], self.repo_root)
        _run_git(["commit", "-m", "chore: advance main"], self.repo_root)

        # Patch ROOT to wt_dir so verify_main_readiness evaluates against test repo
        with patch.object(vmr_module, "ROOT", wt_dir):
            # Behind main without allow_behind -> Rejected (rc == 1)
            rc_rejected = vmr_module.verify_main_readiness(
                allow_behind=False,
                dry_run=False,
            )
            self.assertEqual(rc_rejected, 1)

            # Behind main with allow_behind=True and dry_run=True -> Accepted (rc == 0)
            rc_accepted = vmr_module.verify_main_readiness(
                allow_behind=True,
                dry_run=True,
            )
            self.assertEqual(rc_accepted, 0)

    def test_04_sync_dirty_worktree_rejected_e2e(self) -> None:
        """Vector 4: cmd_sync refuses to operate on a dirty worktree."""
        branch = "feat/dirty-branch"
        _run_git(["branch", branch], self.repo_root)
        wt_dir = self.repo_root / ".worktrees" / "feat_dirty_branch"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", str(wt_dir), branch], self.repo_root)

        # Create uncommitted dirty file in worktree
        (wt_dir / "dirty_untracked.txt").write_text("dirty edits\n", encoding="utf-8")

        args = argparse.Namespace(
            branch=branch,
            repo_root=self.repo_root,
            skip_readiness=True,
            fail_fast=False,
            full=False,
            dry_run=False,
        )
        rc = cmd_sync(args)
        self.assertEqual(rc, 1)

    def test_05_sync_up_to_date_short_circuit_e2e(self) -> None:
        """Vector 5: cmd_sync on already-aligned branch short-circuits gracefully."""
        branch = "feat/aligned-branch"
        _run_git(["branch", branch], self.repo_root)
        wt_dir = self.repo_root / ".worktrees" / "feat_aligned_branch"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", str(wt_dir), branch], self.repo_root)

        args = argparse.Namespace(
            branch=branch,
            repo_root=self.repo_root,
            skip_readiness=True,
            fail_fast=False,
            full=False,
            dry_run=False,
        )
        t0 = time.monotonic()
        rc = cmd_sync(args)
        dur = time.monotonic() - t0

        self.assertEqual(rc, 0)
        self.assertLess(dur, 2.0)

    def test_06_rebase_orchestrator_resolves_worktree_e2e(self) -> None:
        """Vector 6: resolve_worktree_for_branch locates .worktrees/<slug> directory."""
        branch = "feat/custom-test-slug"
        _run_git(["branch", branch], self.repo_root)
        wt_dir = self.repo_root / ".worktrees" / "feat_custom_test_slug"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", str(wt_dir), branch], self.repo_root)

        resolved = resolve_worktree_for_branch(branch, self.repo_root)
        self.assertEqual(resolved.resolve(), wt_dir.resolve())


if __name__ == "__main__":
    unittest.main()
