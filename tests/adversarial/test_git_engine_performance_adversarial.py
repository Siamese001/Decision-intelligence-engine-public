#!/usr/bin/env python3
"""Adversarial tests for Git Engine Performance and Database Decoupling (Wave 2).

Verifies:
1. .gitattributes enforces binary handling on *.sqlite and *.db to block textual 3-way merge corruption.
2. .gitignore ignores *.sqlite-journal and node_modules/ across worktrees.
3. .githooks/post-merge backgrounds worktree hygiene to prevent blocking prompt return.
4. .githooks/pre-push issues a prominent warning banner when NEE_FULL_PRE_PUSH_AUDIT is engaged.
5. audit_worktree_hygiene does not perform unneeded filesystem traversals when clean_tmp is False.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class GitEnginePerformanceAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = ROOT

    def test_01_gitattributes_enforces_binary_on_sqlite_and_db(self) -> None:
        """Verify .gitattributes treats *.sqlite and *.db as binary files."""
        attrs_path = self.repo_root / ".gitattributes"
        self.assertTrue(attrs_path.is_file(), ".gitattributes must exist")
        content = attrs_path.read_text(encoding="utf-8")
        self.assertIn("*.sqlite binary", content, "SQLite databases must be marked binary in .gitattributes")
        self.assertIn("*.db binary", content, "DB files must be marked binary in .gitattributes")

    def test_02_gitignore_ignores_sqlite_journal_and_node_modules(self) -> None:
        """Verify .gitignore ignores *.sqlite-journal and node_modules/."""
        ignore_path = self.repo_root / ".gitignore"
        self.assertTrue(ignore_path.is_file(), ".gitignore must exist")
        content = ignore_path.read_text(encoding="utf-8")
        self.assertIn("*.sqlite-journal", content, "sqlite-journal must be ignored")
        self.assertIn("node_modules/", content, "node_modules must be ignored")

    def test_03_post_merge_backgrounds_hygiene_sweep(self) -> None:
        """Verify .githooks/post-merge backgrounds audit_worktree_hygiene."""
        hook_path = self.repo_root / ".githooks" / "post-merge"
        self.assertTrue(hook_path.is_file(), "post-merge hook must exist")
        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("audit_worktree_hygiene.py", content)
        self.assertIn("&", content, "audit_worktree_hygiene call in post-merge must be backgrounded with &")

    def test_04_pre_push_warns_on_full_audit_flag(self) -> None:
        """Verify .githooks/pre-push emits a prominent advisory banner when NEE_FULL_PRE_PUSH_AUDIT is 1."""
        hook_path = self.repo_root / ".githooks" / "pre-push"
        self.assertTrue(hook_path.is_file(), "pre-push hook must exist")
        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("NEE_FULL_PRE_PUSH_AUDIT", content)
        self.assertIn("WARNING: NEE_FULL_PRE_PUSH_AUDIT=1 is active", content)

    def test_05_audit_worktree_hygiene_guards_tmp_traversal(self) -> None:
        """Verify audit_worktree_hygiene guards find_dangling_tmp_files."""
        script_path = self.repo_root / "tools" / "audit_worktree_hygiene.py"
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("if clean_tmp or check_merged or strict_prune:", content)


if __name__ == "__main__":
    unittest.main()
