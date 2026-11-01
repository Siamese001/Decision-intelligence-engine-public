"""tests/end_to_end/test_live_recording_staging_and_copilot_e2e.py

End-to-End test suite for Live Recording Chunk Staging, Shutdown Resilience,
and Copilot Manifest Roster Synchronization:
1. SessionManifest persists meeting attendee roster to JSON.
2. Copilot session resolves unindexed attendee (Jason) from manifest roster.
3. Live chunk staging migration isolates active writing heads.
4. Trailing 0-byte stub from SIGINT is pruned and master audio stitched (>4096 bytes).
5. Full recorder lifecycle verification with unindexed attendee roster.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.cli.copilot_session import _resolve_session_and_context_model
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import validate_final_chunk
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.copilot_context import RosterResolver
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _build_roster_candidates,
    _match_roster_target,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionManifest, SessionRegistry
from dual_pipeline.live_meeting_recorder_core.worktree_manager import migrate_staged_files


def _write_pcm_wav(path: Path, num_samples: int = 16000) -> None:
    """Generates a valid 16kHz 16-bit mono PCM WAV file."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = [int(1000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(num_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


@pytest.mark.e2e
class TestLiveRecordingStagingAndCopilotE2E(unittest.TestCase):
    """End-to-end verification of staging resilience and Copilot roster synchronization."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.repo_root = self.tmp_path / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self.registry_dir = self.repo_root / ".session_registry"
        self.registry_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_session_manifest_persists_roster(self) -> None:
        """SessionRegistry registers and serializes attendee roster in manifest JSON."""
        registry = SessionRegistry(self.repo_root)
        attendees = ["Traci", "Amit", "Mitch", "Jason"]
        manifest = registry.register(
            meeting_slug="standup_9_30",
            engagement_name="jarvis",
            attendees=attendees,
        )

        manifest_file = self.registry_dir / f"{manifest.session_id}.json"
        self.assertTrue(manifest_file.exists())

        raw_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(raw_data.get("attendees"), ["Traci", "Amit", "Mitch", "Jason"])

        deserialized = SessionManifest.from_dict(raw_data)
        self.assertEqual(deserialized.attendees, ["Traci", "Amit", "Mitch", "Jason"])

    def test_copilot_resolves_unindexed_attendee_from_manifest(self) -> None:
        """Copilot context model receives manifest attendees and resolves Jason despite absence from graph."""
        registry = SessionRegistry(self.repo_root)
        attendees = ["Traci", "Amit", "Mitch", "Jason"]
        output_dir = self.repo_root / "output" / "jarvis" / "standup_9_30"
        output_dir.mkdir(parents=True, exist_ok=True)

        manifest = registry.register(
            meeting_slug="standup_9_30",
            engagement_name="jarvis",
            output_dir=str(output_dir.relative_to(self.repo_root)),
            attendees=attendees,
        )

        args = argparse.Namespace(
            meeting_dir=None,
            session=manifest.session_id,
            project="jarvis",
            runtime_root=str(self.repo_root),
        )
        reader = LiveBufferReader(self.repo_root)

        res_dir, context_model, res_manifest, slug = _resolve_session_and_context_model(args, reader=reader)

        self.assertEqual(context_model.attendees, ["Traci", "Amit", "Mitch", "Jason"])

        # Graph has ONLY Mitch (Jason is unindexed in graph DB):
        mock_graph = MagicMock()
        mock_graph.nodes = {
            "person:mitch": {"canonical_name": "Alex Rivera", "node_type": "person"}
        }

        candidates = _build_roster_candidates(context_model, mock_graph, all_turns=[])

        # Roster resolution for Jason must succeed with RESOLVED:
        res = RosterResolver.resolve("Jason", candidates)
        self.assertEqual(res.status, "RESOLVED")
        self.assertEqual(res.canonical_name, "Jason")

        # Matching target for Copilot question must return Jason without error
        canonical_target, err = _match_roster_target("Jason", candidates)
        self.assertEqual(canonical_target, "Jason")
        self.assertIsNone(err)

    def test_live_staging_and_shutdown_stitch_lifecycle_e2e(self) -> None:
        """Simulates full recording session with chunk staging, abrupt SIGINT trailing stub, and master stitch."""
        src_chunks = self.repo_root / "local_capture" / "chunks"
        dst_chunks = self.repo_root / "worktree" / "chunks"
        master_wav = self.repo_root / "worktree" / "master.wav"
        src_chunks.mkdir(parents=True, exist_ok=True)
        dst_chunks.mkdir(parents=True, exist_ok=True)

        # 1. FFmpeg writes chunk 0 and begins chunk 1
        _write_pcm_wav(src_chunks / "chunk_00000.wav")
        _write_pcm_wav(src_chunks / "chunk_00001.wav")

        # Active poll cycle: only chunk 0 should be migrated (chunk 1 is active head)
        migrate_staged_files(src_chunks, dst_chunks, [], is_final=False)
        self.assertTrue((dst_chunks / "chunk_00000.wav").exists())
        self.assertFalse((dst_chunks / "chunk_00001.wav").exists())

        # 2. FFmpeg finishes chunk 1, writes chunk 2
        _write_pcm_wav(src_chunks / "chunk_00002.wav")
        migrate_staged_files(src_chunks, dst_chunks, [], is_final=False)
        self.assertTrue((dst_chunks / "chunk_00001.wav").exists())
        self.assertFalse((dst_chunks / "chunk_00002.wav").exists())

        # 3. Shutdown: SIGINT arrives, leaving chunk 2 complete and trailing 0-byte chunk 3
        (src_chunks / "chunk_00003.wav").write_bytes(b"")

        # Final drain migration
        migrate_staged_files(src_chunks, dst_chunks, [], is_final=True)
        self.assertTrue((dst_chunks / "chunk_00002.wav").exists())
        self.assertTrue((dst_chunks / "chunk_00003.wav").exists())

        # 4. Final chunk validation: prunes 0-byte stub
        validate_final_chunk(dst_chunks, segment_seconds=15)
        self.assertFalse((dst_chunks / "chunk_00003.wav").exists())
        self.assertTrue((dst_chunks / "chunk_00000.wav").exists())
        self.assertTrue((dst_chunks / "chunk_00001.wav").exists())
        self.assertTrue((dst_chunks / "chunk_00002.wav").exists())

        # 5. Master audio stitch
        receipt = AudioMasterStitcher.stitch_master_wav(dst_chunks, master_wav, pad_missing_or_corrupt=True)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, "SUCCESS")
        self.assertEqual(receipt.total_chunks, 3)
        self.assertTrue(master_wav.exists())
        self.assertGreater(master_wav.stat().st_size, 4096)
