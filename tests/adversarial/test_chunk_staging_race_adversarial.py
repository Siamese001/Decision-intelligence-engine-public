"""tests/adversarial/test_chunk_staging_race_adversarial.py

Adversarial regression suite verifying remediation of:
1. Worktree chunk staging file read/write race condition (atomic deferral & cleanup).
2. Trailing 0-byte stub pruning during shutdown drain (stitch guarantee).
3. Copilot attendee roster synchronization from session manifest.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    EmptyAudioChunkError,
    validate_final_chunk,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context import RosterResolver
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_engine import _build_roster_candidates
from dual_pipeline.live_meeting_recorder_core.worktree_manager import _copy_chunk_if_missing


@pytest.mark.adversarial
class TestChunkStagingRaceAdversarial(unittest.TestCase):
    """Adversarial regression tests verifying that the JARVIS RCA failure modes are fully resolved."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.src_dir = self.tmp_path / "src_chunks"
        self.dst_dir = self.tmp_path / "dst_chunks"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.dst_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_growing_chunk_staging_resilience(self) -> None:
        """Verifies that an actively growing chunk defers safely without raising RuntimeError or leaving tmp stubs."""
        chunk_file = self.src_dir / "chunk_00190.wav"
        chunk_file.write_bytes(b"RIFF" + b"\x00" * 1000)

        # Simulate FFmpeg appending bytes between shutil.copy2 and hashlib verification
        original_copy2 = shutil.copy2

        def growing_copy2(src, dst):
            result = original_copy2(src, dst)
            with open(src, "ab") as f_grow:
                f_grow.write(b"\x00" * 500)
            return result

        with patch("shutil.copy2", side_effect=growing_copy2):
            # Active capture cycle (is_final=False) must defer gracefully without exception
            success = _copy_chunk_if_missing(chunk_file, self.dst_dir, is_final=False)
            self.assertFalse(success)
            self.assertFalse((self.dst_dir / "chunk_00190.wav").exists())
            self.assertEqual(len(list(self.dst_dir.glob(".tmp_*"))), 0)

            # At final drain (is_final=True), an unresolved SHA mismatch fails closed
            with self.assertRaises(RuntimeError) as ctx:
                _copy_chunk_if_missing(chunk_file, self.dst_dir, is_final=True)
            self.assertIn("Integrity check failed: SHA256 mismatch", str(ctx.exception))

    def test_final_chunk_zero_bytes_pruned_resiliently(self) -> None:
        """Verifies that an empty 0-byte trailing stub is pruned rather than aborting shutdown."""
        # Create 5 valid chunks and 1 trailing 0-byte chunk left by abrupt SIGINT
        for i in range(5):
            cf = self.dst_dir / f"chunk_{i:05d}.wav"
            cf.write_bytes(b"RIFF" + b"\x00" * 32000)

        trailing_stub = self.dst_dir / "chunk_00005.wav"
        trailing_stub.write_bytes(b"")

        # Must not raise EmptyAudioChunkError; must prune stub
        validate_final_chunk(self.dst_dir, segment_seconds=15)
        self.assertFalse(trailing_stub.exists())
        self.assertTrue((self.dst_dir / "chunk_00004.wav").exists())

    def test_copilot_roster_manifest_synchronization(self) -> None:
        """Verifies that ConversationContextModel populated from manifest attendees resolves Jason."""
        manifest_attendees = ["Traci", "Amit", "Mitch", "Jason"]

        # When context_model is created with manifest attendees:
        context_model = ConversationContextModel(
            project_scope="project:jarvis",
            attendees=manifest_attendees,
        )
        self.assertEqual(context_model.attendees, ["Traci", "Amit", "Mitch", "Jason"])

        # Mock in_mem graph with NO Jason node:
        mock_graph = MagicMock()
        mock_graph.nodes = {
            "person:mitch": {"canonical_name": "Alex Rivera", "node_type": "person"}
        }

        # Build candidates
        all_turns = []
        candidates = _build_roster_candidates(context_model, mock_graph, all_turns)

        # Roster resolution for Jason must succeed with RESOLVED:
        res = RosterResolver.resolve("Jason", candidates)
        self.assertEqual(res.status, "RESOLVED")
        self.assertEqual(res.canonical_name, "Jason")
