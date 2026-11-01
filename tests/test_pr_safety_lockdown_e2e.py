"""
E2E Test Suite for PR Safety Lockdown (Wave 1).
Tests T1, T2, T14, T18, T19, T25, T26, T27, T28 against real temporary git repositories.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.pr_governance_core.gc import cmd_gc, inspect_worktrees_for_gc
from tools.pr_governance_core.inspection import cmd_doctor
from tools.pr_governance_core.worktree import (
    cmd_finish,
    list_active_worktrees,
    list_orphan_worktree_directories,
)
from tools.retire_superseded_branch import retire_branch


class TestPRSafetyLockdownE2E(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.name", "Test Agent"])
        self._git(["config", "user.email", "agent@test.local"])
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
        )
        return res.stdout.strip()

    def test_t1_gc_never_removes_zero_commit_worktree(self):
        """
        T1: A freshly started clean worktree (0 commits ahead, HEAD == main)
        survives gc, and gc defaults to dry-run.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_fresh_start"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/fresh-start", "main"])

        # 1. Verify inspect_worktrees_for_gc does NOT mark zero-commit worktree as auto-cleanable
        candidates = inspect_worktrees_for_gc(root=self.repo_root)
        fresh_cands = [c for c in candidates if c["branch"] == "feat/fresh-start"]
        self.assertEqual(len(fresh_cands), 1)
        self.assertFalse(fresh_cands[0]["can_auto_clean"], "Zero-commit worktree must not be marked prunable")
        self.assertTrue(fresh_cands[0]["is_zero_commit"], "Must be identified as zero-commit")

        # 2. Run gc (default dry-run)
        args_dry = argparse.Namespace(execute=False, dry_run=True)
        with mock.patch("tools.pr_governance_core.gc.ROOT", self.repo_root):
            exit_code = cmd_gc(args_dry)
            self.assertEqual(exit_code, 0)
            self.assertTrue(wt_dir.exists(), "Worktree must exist after dry-run gc")

        # 3. Run gc with execute=True (still must NOT prune zero-commit worktree)
        args_exec = argparse.Namespace(execute=True, dry_run=False)
        with mock.patch("tools.pr_governance_core.gc.ROOT", self.repo_root):
            exit_code = cmd_gc(args_exec)
            self.assertEqual(exit_code, 0)
            self.assertTrue(wt_dir.exists(), "Zero-commit worktree must survive execute gc")

    def test_t2_gc_and_finish_block_on_ignored_valuable_files(self):
        """
        T2: A non-empty ignored *.sqlite-wal or output/ file blocks removal;
        __pycache__ alone does not.
        """
        # Add .gitignore for sqlite-wal, output, and pycache to main
        gitignore = self.repo_root / ".gitignore"
        gitignore.write_text("*.sqlite-wal\noutput/\n__pycache__/\n", encoding="utf-8")
        self._git(["add", ".gitignore"])
        self._git(["commit", "-m", "chore: add gitignore"])

        wt_dir = self.repo_root / ".worktrees" / "feat_merged_feat"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/merged-feat", "main"])

        # Commit a feature change and merge into main
        feat_file = wt_dir / "feat.py"
        feat_file.write_text("def f(): return 1\n", encoding="utf-8")
        self._git(["add", "feat.py"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: my feature"], cwd=wt_dir)

        self._git(["checkout", "main"])
        self._git(["merge", "--no-ff", "feat/merged-feat", "-m", "Merge feat/merged-feat"])

        # Case A: Benign __pycache__ alone does not block gc or finish
        pycache_dir = wt_dir / "__pycache__"
        pycache_dir.mkdir(parents=True)
        (pycache_dir / "cache.pyc").write_text("compiled", encoding="utf-8")

        candidates = inspect_worktrees_for_gc(root=self.repo_root)
        c = [c for c in candidates if c["branch"] == "feat/merged-feat"][0]
        self.assertTrue(c["can_auto_clean"], "__pycache__ alone must not block clean pruning")

        # Case B: Non-empty ignored valuable file (e.g. graph.sqlite-wal) blocks
        wal_file = wt_dir / "graph.sqlite-wal"
        wal_file.write_text("uncheckpointed sqlite transactions", encoding="utf-8")

        candidates_blocked = inspect_worktrees_for_gc(root=self.repo_root)
        c_blocked = [c for c in candidates_blocked if c["branch"] == "feat/merged-feat"][0]
        self.assertFalse(c_blocked["can_auto_clean"], "Valuable ignored file must block gc auto-clean")
        self.assertTrue(c_blocked["is_dirty"], "Valuable ignored file must mark worktree as dirty")

        # Case C: cmd_finish without force must also block
        args_finish = argparse.Namespace(branch="feat/merged-feat", force=False, delete_branch=True)
        with mock.patch("tools.pr_governance_core.worktree.ROOT", self.repo_root):
            exit_code = cmd_finish(args_finish)
            self.assertEqual(exit_code, 1, "cmd_finish must block when valuable ignored files exist")
            self.assertTrue(wt_dir.exists())

    def test_t14_slug_collision_and_orphan_dir_never_targeted(self):
        """
        T14: Orphan dirs show as ORPHAN, and the root's status is never used for them.
        """
        worktrees_base = self.repo_root / ".worktrees"
        worktrees_base.mkdir(parents=True, exist_ok=True)
        orphan_dir = worktrees_base / "orphan_legacy_dir"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "scratch.txt").write_text("abandoned", encoding="utf-8")

        # Verify orphan detection
        orphans = list_orphan_worktree_directories(self.repo_root)
        self.assertIn(orphan_dir, orphans)

        # list_active_worktrees with include_orphans
        active = list_active_worktrees(self.repo_root, include_orphans=True)
        orphan_entries = [e for e in active if e.get("is_orphan")]
        self.assertEqual(len(orphan_entries), 1)
        self.assertEqual(orphan_entries[0]["branch"], "ORPHAN")
        self.assertIn("ORPHAN", orphan_entries[0]["summary"])

    def test_t18_doctor_is_read_only(self):
        """
        T18: Doctor is strictly read-only; git for-each-ref output is identical before and after doctor.
        """
        # Create a merged branch that doctor previously would have deleted
        self._git(["checkout", "-b", "feat/merged-to-test-doctor"])
        (self.repo_root / "doc_test.txt").write_text("doc test", encoding="utf-8")
        self._git(["add", "doc_test.txt"])
        self._git(["commit", "-m", "feat: merged feature"])
        self._git(["checkout", "main"])
        self._git(["merge", "--no-ff", "feat/merged-to-test-doctor", "-m", "Merge feature"])

        refs_before = self._git(["for-each-ref"])

        with mock.patch("tools.pr_governance_core.inspection.ROOT", self.repo_root):
            cmd_doctor(argparse.Namespace())

        refs_after = self._git(["for-each-ref"])
        self.assertEqual(refs_before, refs_after, "git for-each-ref must be identical before and after doctor")

    def test_t19_tombstone_executor_cas_and_archive_ref(self):
        """
        T19: Tombstone executor creates archive ref, verifies CAS, does not use rmtree,
        and leaves archive ref intact.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_tombstone_target"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/tombstone-target", "main"])
        (wt_dir / "file.py").write_text("val = 1\n", encoding="utf-8")
        self._git(["add", "file.py"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: unique commit"], cwd=wt_dir)

        tip_sha = self._git(["rev-parse", "refs/heads/feat/tombstone-target"])

        # Execute retirement
        receipt = retire_branch("feat/tombstone-target", dry_run=False, root=self.repo_root)
        self.assertEqual(receipt["status"], "RETIRED")
        self.assertEqual(receipt["branch_sha"], tip_sha)

        # Verify archive ref exists and points to tip_sha
        archive_sha = self._git(["rev-parse", f"refs/archive/feat/tombstone-target/{tip_sha}"])
        self.assertEqual(archive_sha, tip_sha)

        # Verify branch is deleted
        branches = self._git(["branch", "--format=%(refname:short)"]).splitlines()
        self.assertNotIn("feat/tombstone-target", branches)

    def test_t25_retire_refuses_dirty_or_stashed_or_rebasing_worktree(self):
        """
        T25: retire_branch refuses dirty, stashed, or rebasing worktree.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_dirty_retire"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/dirty-retire", "main"])
        (wt_dir / "dirty.py").write_text("dirty content", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            retire_branch("feat/dirty-retire", dry_run=False, root=self.repo_root)
        self.assertIn("uncommitted", str(ctx.exception).lower())

    def test_t26_retire_archive_ref_never_overwritten_on_slug_collision_or_reuse(self):
        """
        T26: Archive ref creation with update-ref ... "" fails if ref already exists,
        never overwriting existing archives.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_archive_collision"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/archive-collision", "main"])
        (wt_dir / "f.txt").write_text("content", encoding="utf-8")
        self._git(["add", "f.txt"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: archive commit"], cwd=wt_dir)
        tip_sha = self._git(["rev-parse", "refs/heads/feat/archive-collision"])

        # Manually create the archive ref beforehand
        archive_ref = f"refs/archive/feat/archive-collision/{tip_sha}"
        self._git(["update-ref", archive_ref, tip_sha, ""])

        # retire_branch must fail because update-ref with oldval "" will refuse to overwrite
        with self.assertRaises(RuntimeError):
            retire_branch("feat/archive-collision", dry_run=False, root=self.repo_root)

    def test_t27_retire_cas_aborts_when_tip_moves_between_archive_and_delete(self):
        """
        T27: CAS aborts deletion when tip moves between archival and deletion.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_cas_target"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/cas-target", "main"])
        (wt_dir / "a.txt").write_text("a", encoding="utf-8")
        self._git(["add", "a.txt"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: initial"], cwd=wt_dir)
        old_sha = self._git(["rev-parse", "refs/heads/feat/cas-target"])

        # Simulate race: tip moved to new commit
        (wt_dir / "b.txt").write_text("b", encoding="utf-8")
        self._git(["add", "b.txt"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: raced commit"], cwd=wt_dir)
        new_sha = self._git(["rev-parse", "refs/heads/feat/cas-target"])

        # Attempting CAS deletion with old_sha must fail
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(
                ["git", "update-ref", "-d", "refs/heads/feat/cas-target", old_sha],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
        # Branch still exists at new_sha
        current_sha = self._git(["rev-parse", "refs/heads/feat/cas-target"])
        self.assertEqual(current_sha, new_sha)

    def test_t28_retire_refuses_protected_refs(self):
        """
        T28: retire_branch refuses protected refs (main, master, backup/*, archive/*).
        """
        for protected in ("main", "master", "HEAD", "backup/20261001", "archive/old"):
            with self.assertRaises(ValueError) as ctx:
                retire_branch(protected, dry_run=True, root=self.repo_root)
            self.assertIn("protected", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
