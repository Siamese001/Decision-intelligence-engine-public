"""
tests/test_live_transcription_worktree_and_hitl_mandatory.py

Comprehensive test suite verifying two mandatory pieces for live transcription:
1. Mandatory new worktree branch creation (.worktrees/<slug>/ on feat/live-<slug>).
2. Permanent HITL enforcement with zero ability to remove, disable, or bypass HITL.
"""
from __future__ import annotations

import ast
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
)
from dual_pipeline.live_meeting_recorder_core.hitl_manager import (
    assert_no_hitl_bypass,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    ensure_live_transcription_worktree,
    slugify_for_branch,
)


class TestLiveTranscriptionWorktreeMandatory(unittest.TestCase):
    """Verifies that live transcription enforces creation of an isolated worktree branch."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_worktree_live_")
        self.temp_root = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_slugify_for_branch(self):
        """Verify conventional branch naming format."""
        slug = slugify_for_branch("Project LENS — SOW 1 & SOW 2 Alignment!")
        self.assertTrue(slug.startswith("project-lens"))
        self.assertNotIn(" ", slug)
        self.assertNotIn("&", slug)
        self.assertNotIn("—", slug)

    def test_ensure_live_transcription_worktree_provisions_branch(self):
        """Verify ensure_live_transcription_worktree creates .worktrees/<slug> and branch."""
        with mock.patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git") as mock_git:
            mock_git.return_value = ""

            wt_dir, branch_name = ensure_live_transcription_worktree(
                project_name="project_nexus",
                meeting_name="Nexus Architecture Review",
                date_dirname="09-22-2026",
                clean_slug="Nexus_Architecture_Review",
                repo_root=self.temp_root,
            )

            self.assertTrue(branch_name.startswith("feat/live-"))
            self.assertTrue(str(wt_dir).endswith(branch_name.split("/")[-1].replace("-", "_")))
            self.assertGreaterEqual(mock_git.call_count, 2)

    def test_recorder_initializes_worktree_when_not_in_worktree(self):
        """Verify LiveMeetingRecorder provisions isolated worktree when in root workspace."""
        with mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree"
        ) as mock_ensure:
            mock_wt = self.temp_root / ".worktrees" / "live_session"
            mock_ensure.return_value = (mock_wt, "feat/live-nexus-architecture-review")

            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Nexus Architecture Review",
                meeting_date="09/22/2026",
                mock_mode=True,
                enforce_worktree=True,
            )

            mock_ensure.assert_called_once()
            self.assertEqual(recorder.worktree_branch, "feat/live-nexus-architecture-review")
            self.assertTrue(str(recorder.output_dir).startswith(str(mock_wt)))

    def test_resolve_output_directory_provisions_worktree_when_called_from_root(self):
        """Verify resolve_output_directory_and_slug provisions worktree when on root main."""
        from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
            resolve_output_directory_and_slug,
        )

        with mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
            return_value=False,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree"
        ) as mock_ensure:
            mock_wt = self.temp_root / ".worktrees" / "live_standup"
            mock_ensure.return_value = (mock_wt, "feat/live-standup")

            meta = {
                "project": "project_lens",
                "meeting_name": "LENS Standup",
                "date": "09-22-2026",
                "enforce_worktree": True,
            }
            resolved_dir, slug = resolve_output_directory_and_slug(
                metadata=meta,
                transcript_text="Amit Ayer: Morning status check.",
                output_dir="auto",
            )

            mock_ensure.assert_called_once()
            self.assertEqual(meta["worktree_branch"], "feat/live-standup")
            self.assertTrue(str(resolved_dir).startswith(str(mock_wt)))
            self.assertIn("project_lens", resolved_dir)

    def test_resolve_output_directory_preserves_worktree_when_inside_worktree(self):
        """Verify resolve_output_directory_and_slug does not double-provision when already in worktree."""
        from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
            resolve_output_directory_and_slug,
        )

        with mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
            return_value=True,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree"
        ) as mock_ensure:
            meta = {
                "project": "project_lens",
                "meeting_name": "LENS Standup",
                "date": "09-22-2026",
                "enforce_worktree": True,
            }
            resolved_dir, slug = resolve_output_directory_and_slug(
                metadata=meta,
                transcript_text="Amit Ayer: Morning status check.",
                output_dir="auto",
            )

            mock_ensure.assert_not_called()
            self.assertNotIn("worktree_branch", meta)

    def test_resolve_output_directory_enforces_even_when_enforce_worktree_false(self):
        """Verify enforce_worktree=False is overridden and worktree provisioning is unconditionally enforced."""
        from dual_pipeline.core import ROOT
        from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
            resolve_output_directory_and_slug,
        )

        mock_wt = ROOT / ".worktrees" / "live_lens_standup"
        with (
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
                return_value=False,
            ),
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree",
                return_value=(mock_wt, "feat/live-lens-standup"),
            ) as mock_ensure,
        ):
            meta = {
                "project": "project_lens",
                "meeting_name": "LENS Standup",
                "date": "09-22-2026",
                "enforce_worktree": False,
            }
            resolved_dir, slug = resolve_output_directory_and_slug(
                metadata=meta,
                transcript_text="Amit Ayer: Morning status check.",
                output_dir="auto",
            )
            mock_ensure.assert_called_once()
            self.assertTrue(meta["enforce_worktree"])
            self.assertTrue(str(resolved_dir).startswith(str(mock_wt)))


class TestLiveTranscriptionHITLMandatory(unittest.TestCase):
    """Verifies that HITL is permanently toggled ON with zero ability to remove/bypass."""

    def test_live_meeting_recorder_rejects_disabling_hitl(self):
        """Verify LiveMeetingRecorder rejects enable_hitl=False or bypass flags."""
        with self.assertRaises(ValueError) as ctx:
            LiveMeetingRecorder(meeting_name="Test", mock_mode=True, enable_hitl=False)
        self.assertIn("HITL cannot be disabled", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            LiveMeetingRecorder(meeting_name="Test", mock_mode=True, bypass_speaker_hitl=True)
        self.assertIn("HITL cannot be removed or bypassed", str(ctx.exception))

    def test_assert_no_hitl_bypass_guard(self):
        """Verify assert_no_hitl_bypass catches all bypass variants."""
        # Clean dict passes
        assert_no_hitl_bypass({"project": "lens", "date": "09-22-2026"})

        # Bypass variants fail closed
        for key in ["bypass_speaker_hitl", "bypass_hitl", "bypass_roster_gate", "no_hitl"]:
            with self.assertRaises(ValueError):
                assert_no_hitl_bypass({key: True})
            with self.assertRaises(ValueError):
                assert_no_hitl_bypass({"nested": {key: True}})


class TestASTNoHITLRemovalInLiveCodebase(unittest.TestCase):
    """Static AST verification: no ability to remove HITL exists in live transcription files."""

    def _inspect_file_for_no_hitl_args(self, file_path: Path):
        self.assertTrue(file_path.exists(), f"File does not exist: {file_path}")
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))

        for node in ast.walk(tree):
            # Check for CLI argument additions with --no-hitl or --bypass-hitl in live files
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "add_argument":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn(
                                arg.value,
                                ["--no-hitl", "--bypass-hitl", "--disable-hitl"],
                                f"Forbidden flag {arg.value} found in {file_path}",
                            )

    def test_ast_inspect_live_transcript_engine(self):
        root = Path(__file__).resolve().parent.parent
        self._inspect_file_for_no_hitl_args(root / "dual_pipeline" / "live_transcript_engine.py")

    def test_ast_inspect_live_meeting_recorder(self):
        root = Path(__file__).resolve().parent.parent
        self._inspect_file_for_no_hitl_args(root / "dual_pipeline" / "live_meeting_recorder.py")


if __name__ == "__main__":
    unittest.main()
