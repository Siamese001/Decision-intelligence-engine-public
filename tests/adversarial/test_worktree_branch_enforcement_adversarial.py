"""Adversarial test suite verifying fail-closed Git worktree branch enforcement.

Guarantees:
1. ADV-WT-01: Pre-allocated or unmanaged directories in .worktrees/ do not bypass Git worktree provisioning.
2. ADV-WT-02: get_current_git_branch() prevents upward directory traversal to the parent repository.
3. ADV-WT-03: is_authentic_git_worktree() strictly differentiates authentic Git worktrees from unmanaged folders.
4. ADV-WT-04: Dry-run ingestion resolves candidate branch slugs without polluting .worktrees/ with phantom folders.
"""

import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    _provision_git_worktree,
    ensure_live_transcription_worktree,
    get_current_git_branch,
    is_authentic_git_worktree,
    run_git,
    slugify_for_branch,
)


class TestWorktreeBranchEnforcementAdversarial(unittest.TestCase):
    """Adversarial verification of Git worktree branch enforcement and anti-parent-bleeding."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_wt_adv_")
        self.temp_root = Path(self.temp_dir)
        # Create a mock git repository in temp_root to test isolation safely
        run_git(["init", "-b", "main"], cwd=self.temp_root)
        run_git(["config", "user.name", "Test Runner"], cwd=self.temp_root)
        run_git(["config", "user.email", "test@example.com"], cwd=self.temp_root)
        (self.temp_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        run_git(["add", "README.md"], cwd=self.temp_root)
        run_git(["commit", "-m", "Initial commit"], cwd=self.temp_root)

    def tearDown(self):
        # Prune any worktrees created in temp_root before rmtree
        with contextlib.suppress(Exception):
            run_git(["worktree", "prune"], cwd=self.temp_root)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_is_authentic_git_worktree_semantics(self):
        """ADV-WT-03: Verifies authentic worktree vs unmanaged subdirectory."""
        # 1. Repo root is authentic
        self.assertTrue(is_authentic_git_worktree(self.temp_root))

        # 2. Non-existent path is False
        self.assertFalse(is_authentic_git_worktree(self.temp_root / "nonexistent"))

        # 3. Unmanaged subdirectory is False even if named .worktrees
        unmanaged = self.temp_root / ".worktrees" / "live_phantom_folder"
        unmanaged.mkdir(parents=True, exist_ok=True)
        (unmanaged / "sample.txt").write_text("hello", encoding="utf-8")
        self.assertFalse(is_authentic_git_worktree(unmanaged))

        # 4. Provisioned worktree is True
        wt_dir = self.temp_root / ".worktrees" / "live_real_branch"
        _provision_git_worktree(self.temp_root, wt_dir, "feat/live-real-branch")
        self.assertTrue(is_authentic_git_worktree(wt_dir))
        self.assertTrue((wt_dir / ".git").is_file())

    def test_get_current_git_branch_blocks_parent_traversal(self):
        """ADV-WT-02: Verifies that an unmanaged subfolder never bleeds parent repo branch."""
        # Switch temp_root to a distinct branch name
        run_git(["checkout", "-b", "parent-feature-branch"], cwd=self.temp_root)
        self.assertEqual(get_current_git_branch(self.temp_root), "parent-feature-branch")

        # Create an unmanaged directory inside .worktrees/
        unmanaged = self.temp_root / ".worktrees" / "live_unmanaged_folder"
        unmanaged.mkdir(parents=True, exist_ok=True)

        # Standard git would walk up and return 'parent-feature-branch'
        raw_git_branch = run_git(["branch", "--show-current"], cwd=unmanaged)
        self.assertEqual(raw_git_branch, "parent-feature-branch")

        # Hardened get_current_git_branch MUST refuse parent traversal and return empty string
        hardened_branch = get_current_git_branch(unmanaged)
        self.assertEqual(hardened_branch, "")

    def test_unmanaged_directory_pre_existence_adopts_cleanly(self):
        """ADV-WT-01: Verifies that pre-existing unmanaged directory is adopted into authentic worktree."""
        clean_slug = "adversarial_adoption_test"
        branch_name = f"feat/live-{slugify_for_branch(clean_slug)}"
        worktree_dir = self.temp_root / ".worktrees" / f"live_{clean_slug}"

        # Simulate prior aborted process or early file write creating the directory
        worktree_dir.mkdir(parents=True, exist_ok=True)
        output_sub = worktree_dir / "output" / "project_nexus" / "09-30-2026" / clean_slug
        output_sub.mkdir(parents=True, exist_ok=True)
        precious_file = output_sub / "early_chunk.wav"
        precious_file.write_text("AUDIO_DATA", encoding="utf-8")

        self.assertFalse(is_authentic_git_worktree(worktree_dir))

        # Call ensure_live_transcription_worktree with lazy=False
        res_dir, res_branch = ensure_live_transcription_worktree(
            project_name="project_nexus",
            meeting_name="Adversarial Adoption Test",
            date_dirname="09-30-2026",
            clean_slug=clean_slug,
            repo_root=self.temp_root,
            lazy=False,
        )

        self.assertEqual(res_dir.resolve(), worktree_dir.resolve())
        self.assertEqual(res_branch, branch_name)

        # Verify it has been converted to an authentic Git worktree
        self.assertTrue(is_authentic_git_worktree(worktree_dir))
        self.assertTrue((worktree_dir / ".git").is_file())
        self.assertEqual(get_current_git_branch(worktree_dir), branch_name)

        # Verify precious pre-existing files were preserved during worktree adoption
        self.assertTrue(precious_file.exists())
        self.assertEqual(precious_file.read_text(encoding="utf-8"), "AUDIO_DATA")

    def test_dry_run_ingest_does_not_pollute_worktree_filesystem(self):
        """ADV-WT-04: Verifies dry-run ingestion does not create unmanaged directories in .worktrees/."""
        clean_slug = "dry_run_no_pollution_test"
        import argparse

        from dual_pipeline.cli.ingest import ingest_canonical

        dummy_input = self.temp_root / "sample_transcript.md"
        dummy_input.write_text("# Meeting\n**Attendees:** Alice, Bob\n", encoding="utf-8")

        wt_dir_expected = self.temp_root / ".worktrees" / f"live_{clean_slug}"
        self.assertFalse(wt_dir_expected.exists())

        args = argparse.Namespace(
            input=str(dummy_input),
            meeting="Dry Run No Pollution Test",
            project="project_nexus",
            date="09/30/2026",
            dry_run=True,
            enforce_worktree=True,
            mutate_graph=False,
            model=None,
            judge_model=None,
            reasoning_effort="high",
            provider="llm",
            directive_id="directive-test-01",
            reviewer=None,
            rationale=None,
        )

        with mock.patch("dual_pipeline.core.ROOT", self.temp_root):
            result = ingest_canonical(args)

        self.assertEqual(result.get("status"), "PASS")
        self.assertTrue(result.get("dry_run"))
        # Verify that no directory was created on disk in .worktrees/
        self.assertFalse(wt_dir_expected.exists())
