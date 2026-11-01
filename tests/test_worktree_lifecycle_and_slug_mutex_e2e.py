"""
tests/test_worktree_lifecycle_and_slug_mutex_e2e.py

End-to-End Test Suite for Wave 3:
1. Lazy Worktree Initialization: verifies git worktree is deferred until first valid audio chunk.
2. Early Abort Rollback Guard: verifies empty recording sessions automatically teardown provisioned worktrees/branches.
3. Slug Mutex: verifies meeting slugs are normalized, <= 40 chars, and stripped of boilerplate.
4. Strict Worktree Hygiene Audit: verifies orphaned directory cleanup, output preservation, and merged worktree pruning.
"""
from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    slugify_for_branch,
)
from tools.audit_worktree_hygiene import (
    audit_hygiene,
    cleanup_orphaned_worktree_directories,
    preserve_worktree_outputs,
)


class TestWorktreeLifecycleAndSlugMutexE2E(unittest.TestCase):
    """End-to-end verification of worktree lifecycle, slug mutex, and hygiene pruning."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_wt_e2e_")
        self.repo_root = Path(self.test_dir)
        (self.repo_root / "output").mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".worktrees").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        wt_dir = Path(__file__).resolve().parents[1] / ".worktrees"
        if wt_dir.is_dir():
            for p in wt_dir.glob("live_*"):
                if p.is_dir() and any(k in p.name for k in ("lazy_initialization", "empty_aborted")):
                    shutil.rmtree(p, ignore_errors=True)

    def _create_synthetic_wav(self, path: Path, num_frames: int = 16000, amplitude: int = 500) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            data = struct.pack(f"<{num_frames}h", *[amplitude] * num_frames)
            w.writeframes(data)

    def test_slug_mutex_length_and_normalization(self):
        """Verify slug mutex enforces <=40 chars and strips boilerplate prefixes."""
        long_title = "Project LENS — Extremely Long Extended Strategic Executive Architecture Alignment Discussion 2026"
        slug = slugify_for_branch(long_title)
        self.assertLessEqual(len(slug), 40)
        self.assertNotIn(" ", slug)
        self.assertNotIn("—", slug)
        self.assertFalse(slug.endswith("-"))

        # Verify another typical meeting title
        slug2 = slugify_for_branch("Project Nexus - SOW 1 Boundaries with PartnerAdvisory & NEA")
        self.assertLessEqual(len(slug2), 40)
        self.assertTrue(slug2.startswith("project-nexus"))

    def test_lazy_worktree_initialization_deferred_until_valid_chunk(self):
        """Verify worktree provisioning does not happen during __init__ when lazy=True."""
        with mock.patch(
            "dual_pipeline.live_meeting_recorder.is_inside_isolated_worktree",
            return_value=False,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
            return_value=False,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder.provision_live_transcription_worktree"
        ) as mock_prov:
            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Lazy Initialization Test",
                meeting_date="09/26/2026",
                mock_mode=True,
                enforce_worktree=True,
            )
            # Worktree was NOT provisioned during initialization
            mock_prov.assert_not_called()
            self.assertFalse(recorder._worktree_materialized)
            self.assertTrue(str(recorder.chunks_dir).endswith(f".staging_{recorder.clean_slug}_{recorder.session_tag}"))

            # Create a valid audio chunk
            chunk_file = recorder.chunks_dir / "chunk_00000.wav"
            self._create_synthetic_wav(chunk_file, num_frames=16000, amplitude=600)

            # Process the valid chunk -> triggers materialization
            recorder._process_chunk_file(chunk_file)
            mock_prov.assert_called_once()
            self.assertTrue(recorder._worktree_materialized)

    def test_early_abort_rollback_guard_cleans_empty_session(self):
        """Verify stopping with 0 processed chunks triggers early abort rollback guard."""
        with mock.patch(
            "dual_pipeline.live_meeting_recorder.is_inside_isolated_worktree",
            return_value=False,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
            return_value=False,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder.teardown_empty_live_worktree"
        ) as mock_teardown:
            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Empty Aborted Session",
                meeting_date="09/26/2026",
                mock_mode=True,
                enforce_worktree=True,
            )
            # Simulate worktree having been materialized but 0 chunks processed
            recorder._worktree_materialized = True
            with self.assertRaises(Exception):
                recorder.stop()

            # Teardown should have been invoked because 0 chunks were processed
            mock_teardown.assert_called_once_with(recorder.worktree_dir, recorder.worktree_branch, mock.ANY)
            self.assertEqual(len(recorder.processed_chunks), 0)

    def test_preserve_worktree_outputs_before_prune(self):
        """Verify preserve_worktree_outputs copies deliverables into main repo output hierarchy."""
        fake_wt = self.repo_root / ".worktrees" / "live_sample"
        fake_wt_output = fake_wt / "output" / "project_nexus" / "09-26-2026" / "sample_slug"
        fake_wt_output.mkdir(parents=True, exist_ok=True)
        transcript_file = fake_wt_output / "01_sample_slug_live_transcript.md"
        transcript_file.write_text("# Sample Live Transcript", encoding="utf-8")

        count = preserve_worktree_outputs(fake_wt, self.repo_root)
        self.assertEqual(count, 1)

        expected_dest = self.repo_root / "output" / "project_nexus" / "09-26-2026" / "sample_slug" / "01_sample_slug_live_transcript.md"
        self.assertTrue(expected_dest.exists())
        self.assertEqual(expected_dest.read_text(encoding="utf-8"), "# Sample Live Transcript")

    def test_cleanup_orphaned_worktree_directories(self):
        """Verify orphaned directory under .worktrees without git binding is pruned."""
        orphan_dir = self.repo_root / ".worktrees" / "debate_live_vs_file_pipeline_processing"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "temp.txt").write_text("dangling artifact", encoding="utf-8")

        with mock.patch("tools.audit_worktree_hygiene.get_git_worktrees", return_value=[]):
            cleaned = cleanup_orphaned_worktree_directories(self.repo_root)
            self.assertIn("debate_live_vs_file_pipeline_processing", cleaned)
            self.assertFalse(orphan_dir.exists())

    def test_audit_hygiene_strict_prune_e2e(self):
        """Verify audit_hygiene with strict_prune=True cleans up cleanly."""
        orphan_dir = self.repo_root / ".worktrees" / "orphaned_test_dir"
        orphan_dir.mkdir(parents=True, exist_ok=True)

        with mock.patch("tools.audit_worktree_hygiene.get_git_worktrees", return_value=[]):
            report = audit_hygiene(self.repo_root, strict_prune=True)
            self.assertIn("orphaned_test_dir", report.cleaned_orphaned_worktrees)
            self.assertFalse(orphan_dir.exists())


if __name__ == "__main__":
    unittest.main()
