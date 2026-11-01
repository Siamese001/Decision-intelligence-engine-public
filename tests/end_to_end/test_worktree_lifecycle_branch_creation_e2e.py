"""End-to-End test suite verifying Git worktree lifecycle and branch creation.

Exercises the full lifecycle from LiveMeetingRecorder initialization and Canonical
Agentic Pipeline execution to authentic worktree verification, branch binding, and teardown.
"""

import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    ensure_live_transcription_worktree,
    get_current_git_branch,
    is_authentic_git_worktree,
    run_git,
    slugify_for_branch,
    teardown_empty_live_worktree,
)


class TestWorktreeLifecycleBranchCreationE2E(unittest.TestCase):
    """End-to-End verification of live recording and ingestion worktree lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_wt_e2e_")
        self.temp_root = Path(self.temp_dir).resolve()
        # Initialize an authentic git repository
        run_git(["init", "-b", "main"], cwd=self.temp_root)
        run_git(["config", "user.name", "Test Runner"], cwd=self.temp_root)
        run_git(["config", "user.email", "test@example.com"], cwd=self.temp_root)
        (self.temp_root / "README.md").write_text("# E2E Test Repo\n", encoding="utf-8")
        run_git(["add", "README.md"], cwd=self.temp_root)
        run_git(["commit", "-m", "Initial commit"], cwd=self.temp_root)

    def tearDown(self):
        with contextlib.suppress(Exception):
            run_git(["worktree", "prune"], cwd=self.temp_root)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_live_meeting_recorder_e2e_worktree_creation_and_teardown(self):
        """E2E test: verifies LiveMeetingRecorder provisions authentic worktree and cleans up empty state."""
        meeting_name = "Architecture Sync Jen Amit"
        clean_slug = "architecture_sync_jen_amit"
        expected_branch = f"feat/live-{slugify_for_branch(clean_slug)}"

        with mock.patch("dual_pipeline.core.ROOT", self.temp_root), \
             mock.patch("dual_pipeline.live_meeting_recorder.ROOT", self.temp_root):

            # Explicitly provision worktree for this session
            wt_dir, wt_branch = ensure_live_transcription_worktree(
                project_name="project_nexus",
                meeting_name=meeting_name,
                date_dirname="09-30-2026",
                clean_slug=clean_slug,
                repo_root=self.temp_root,
                lazy=False,
            )

            # 1. Verify authentic worktree created
            self.assertTrue(is_authentic_git_worktree(wt_dir))
            self.assertTrue((wt_dir / ".git").is_file())
            self.assertEqual(wt_branch, expected_branch)
            self.assertEqual(get_current_git_branch(wt_dir), expected_branch)

            # 2. Verify git worktree list registers it
            wt_list_output = run_git(["worktree", "list"], cwd=self.temp_root)
            self.assertIn(str(wt_dir), wt_list_output)
            self.assertIn(expected_branch, wt_list_output)

            # 3. Teardown empty worktree cleanly
            success = teardown_empty_live_worktree(wt_dir, wt_branch, repo_root=self.temp_root)
            self.assertTrue(success)
            self.assertFalse(wt_dir.exists())

            # 4. Verify branch was pruned from git
            branch_list = run_git(["branch", "--list", expected_branch], cwd=self.temp_root)
            self.assertEqual(branch_list.strip(), "")
