#!/usr/bin/env python3
"""
Unit and Governance Tests for Worktree IDE Visibility and Gitignore Invariants.
"""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestWorktreeIDEVisibility(unittest.TestCase):
    def test_worktrees_ignored_at_root_to_prevent_submodule_collisions(self) -> None:
        """
        Verify .worktrees/ is explicitly ignored in root .gitignore.
        This prevents git check-ignore from failing with fatal submodule errors
        and keeps root git status clean from submodule gitlinks.
        """
        gitignore_path = ROOT / ".gitignore"
        self.assertTrue(gitignore_path.exists(), ".gitignore must exist at repository root")
        lines = [line.strip() for line in gitignore_path.read_text(encoding="utf-8").splitlines()]
        active_rules = [l for l in lines if l and not l.startswith("#")]
        self.assertTrue(
            any(rule in (".worktrees/", ".worktrees") for rule in active_rules),
            ".worktrees/ must be ignored in root .gitignore to isolate worktrees from root repository",
        )

    def test_vscode_settings_unignored_in_gitignore(self) -> None:
        """Verify .vscode/settings.json is unignored in .gitignore so configs are tracked."""
        gitignore_path = ROOT / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        self.assertIn("!.vscode/settings.json", content, "!.vscode/settings.json must be unignored")

    def test_vscode_settings_configured_for_visibility(self) -> None:
        """Verify .vscode/settings.json contains required IDE visibility and native worktree configurations."""
        settings_path = ROOT / ".vscode" / "settings.json"
        self.assertTrue(settings_path.exists(), ".vscode/settings.json must exist")
        data = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertFalse(data.get("explorer.excludeGitIgnore", True), "explorer.excludeGitIgnore must be false")
        files_exclude = data.get("files.exclude", {})
        self.assertFalse(files_exclude.get("**/.worktrees", True), "**/.worktrees must be explicitly false")
        self.assertTrue(data.get("git.detectWorktrees", False), "git.detectWorktrees must be explicitly true")
        self.assertGreaterEqual(
            data.get("git.detectWorktreesLimit", 0), 10, "git.detectWorktreesLimit must be at least 10"
        )
        self.assertTrue(data.get("scm.alwaysShowRepositories", False), "scm.alwaysShowRepositories must be true")
        self.assertEqual(data.get("scm.countBadge"), "all", "scm.countBadge must be 'all'")

    def test_git_check_ignore_no_submodule_fatal_error(self) -> None:
        """
        Verify git check-ignore on worktree paths exits cleanly (returncode 0)
        and does not trigger fatal submodule pathspec errors (exit code 128).
        """
        sample_path = ROOT / ".worktrees" / "dummy_branch" / "module.py"
        res = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", str(sample_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"Expected git check-ignore to exit 0 (cleanly ignored), got: {res.returncode}, stderr: {res.stderr.strip()}",
        )
        self.assertNotIn("is in submodule", res.stderr, "git check-ignore must not fail with submodule error")

    def test_pre_commit_hook_blocks_worktree_staging(self) -> None:
        """Verify .githooks/pre-commit contains the native shell guard rejecting .worktrees staging."""
        hook_path = ROOT / ".githooks" / "pre-commit"
        self.assertTrue(hook_path.exists(), ".githooks/pre-commit must exist")
        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("STAGED_WORKTREES", content, "Pre-commit hook must declare STAGED_WORKTREES check")
        self.assertIn(r"grep -E '^\.worktrees/'", content, "Pre-commit hook must grep for staged .worktrees/")

    def test_lint_production_purity_rejects_staged_worktrees(self) -> None:
        """Verify tools/lint_production_purity.py flags any staged worktree path."""
        from tools.lint_production_purity import lint_staged_worktrees

        violations = lint_staged_worktrees(ROOT, [".worktrees/feat_foo/some_code.py"])
        self.assertTrue(
            any("WORKTREE STAGING VIOLATION" in v for v in violations),
            f"Expected WORKTREE STAGING VIOLATION, got: {violations}",
        )

    def test_pr_governance_worktree_symlinks_vscode(self) -> None:
        """Verify worktree provisioning includes .vscode and .agents in dependency symlinks."""
        from tools.pr_governance_core import worktree

        source_code = Path(worktree.__file__).read_text(encoding="utf-8")
        self.assertIn('".vscode"', source_code, "worktree provisioning must include .vscode in symlink list")
        self.assertIn('".agents"', source_code, "worktree provisioning must include .agents in symlink list")

    def test_link_worktree_dependencies_relative_symlinks(self) -> None:
        """Verify link_worktree_dependencies creates portable relative symlinks."""
        import os
        import tempfile
        from tools.pr_governance_core.worktree import link_worktree_dependencies

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            fake_venv = tmp_root / ".venv"
            fake_venv.mkdir()
            fake_agents = tmp_root / ".agents"
            fake_agents.mkdir()

            tmp_worktree = tmp_root / ".worktrees" / "test_slug"
            tmp_worktree.mkdir(parents=True)

            linked = link_worktree_dependencies(tmp_root, tmp_worktree)
            self.assertIn(".venv", linked)
            self.assertIn(".agents", linked)

            venv_link = tmp_worktree / ".venv"
            self.assertTrue(venv_link.is_symlink())
            target = os.readlink(venv_link)
            self.assertFalse(os.path.isabs(target), f"Symlink target must be relative, got: {target}")
            self.assertTrue(venv_link.resolve().exists())

    def test_safely_unlink_worktree_dependencies_protects_targets(self) -> None:
        """Verify safely_unlink_worktree_dependencies unlinks symlinks without deleting targets."""
        import os
        import tempfile
        from tools.pr_governance_core.worktree import safely_unlink_worktree_dependencies

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            target_dir = tmp_root / ".venv"
            target_dir.mkdir()
            target_file = target_dir / "keep_me.txt"
            target_file.write_text("critical_payload", encoding="utf-8")

            tmp_worktree = tmp_root / ".worktrees" / "test_slug"
            tmp_worktree.mkdir(parents=True)
            link_path = tmp_worktree / ".venv"
            os.symlink(os.path.relpath(target_dir, tmp_worktree), link_path)

            self.assertTrue(link_path.is_symlink())
            unlinked = safely_unlink_worktree_dependencies(tmp_worktree)
            self.assertIn(".venv", unlinked)
            self.assertFalse(link_path.exists())
            self.assertTrue(target_file.exists(), "Target file in root must not be touched")
            self.assertEqual(target_file.read_text(encoding="utf-8"), "critical_payload")

    def test_list_active_worktrees_structure(self) -> None:
        """Verify list_active_worktrees returns properly structured worktree audit entries."""
        from tools.pr_governance_core.worktree import list_active_worktrees

        worktrees = list_active_worktrees(ROOT)
        self.assertIsInstance(worktrees, list)
        self.assertGreaterEqual(len(worktrees), 1, "At least the active repository/worktree must be listed")
        for entry in worktrees:
            self.assertIn("path", entry)
            self.assertIn("branch", entry)
            self.assertIn("head", entry)
            self.assertIn("dirty", entry)
            self.assertIn("summary", entry)
            self.assertIn("is_root", entry)

    def test_visibility_suite_registered_in_main_readiness(self) -> None:
        """Verify test_worktree_ide_visibility is permanently registered in readiness_core.py."""
        readiness_path = ROOT / "tools" / "readiness_core.py"
        self.assertTrue(readiness_path.exists(), "tools/readiness_core.py must exist")
        content = readiness_path.read_text(encoding="utf-8")
        self.assertIn(
            "tests.governance.test_worktree_ide_visibility",
            content,
            "tests.governance.test_worktree_ide_visibility must be registered in Stage 28 of readiness_core.py",
        )


if __name__ == "__main__":
    unittest.main()
