"""tests/adversarial/test_shutdown_drain_stub_resilience.py

Adversarial test suite validating shutdown drain and stub pruning resilience:
1. Pruning of trailing 0-byte stubs when valid prior audio chunks exist.
2. Proper fail-closed EmptyAudioChunkError when zero valid audio was captured.
3. Master audio stitch guarantee (>4096 bytes) after abrupt SIGINT simulation.
4. Non-fatal exception isolation ensuring _stitch_master_audio always runs.
"""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    EmptyAudioChunkError,
    validate_final_chunk,
)
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher


def _create_pcm_wav(path: Path, num_samples: int = 16000) -> None:
    """Helper to generate a valid 16kHz 16-bit mono PCM WAV file."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = [int(1000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(num_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


@pytest.mark.adversarial
class TestShutdownDrainStubResilience(unittest.TestCase):
    """Verifies that trailing 0-byte stubs do not abort shutdown or block master stitching."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.chunks_dir = self.tmp_path / "chunks"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.master_wav = self.tmp_path / "master.wav"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validate_final_chunk_prunes_single_trailing_zero_byte_stub(self) -> None:
        """When 3 valid chunks and 1 trailing 0-byte stub exist, prune stub without raising."""
        for i in range(3):
            _create_pcm_wav(self.chunks_dir / f"chunk_{i:05d}.wav")

        stub = self.chunks_dir / "chunk_00003.wav"
        stub.write_bytes(b"")

        # Must not raise
        validate_final_chunk(self.chunks_dir, segment_seconds=15)

        # Stub must be unlinked/pruned
        self.assertFalse(stub.exists())
        # Prior valid chunks must remain intact
        self.assertTrue((self.chunks_dir / "chunk_00000.wav").exists())
        self.assertTrue((self.chunks_dir / "chunk_00001.wav").exists())
        self.assertTrue((self.chunks_dir / "chunk_00002.wav").exists())

    def test_validate_final_chunk_prunes_multiple_trailing_zero_byte_stubs(self) -> None:
        """When multiple 0-byte stubs follow valid chunks, prune all trailing stubs."""
        for i in range(2):
            _create_pcm_wav(self.chunks_dir / f"chunk_{i:05d}.wav")

        stub1 = self.chunks_dir / "chunk_00002.wav"
        stub2 = self.chunks_dir / "chunk_00003.wav"
        stub1.write_bytes(b"")
        stub2.write_bytes(b"")

        validate_final_chunk(self.chunks_dir, segment_seconds=15)

        self.assertFalse(stub1.exists())
        self.assertFalse(stub2.exists())
        self.assertTrue((self.chunks_dir / "chunk_00000.wav").exists())
        self.assertTrue((self.chunks_dir / "chunk_00001.wav").exists())

    def test_validate_final_chunk_fails_closed_when_all_chunks_empty(self) -> None:
        """When no valid audio was captured (only 0-byte stubs), fail closed with EmptyAudioChunkError."""
        stub = self.chunks_dir / "chunk_00000.wav"
        stub.write_bytes(b"")

        with self.assertRaises(EmptyAudioChunkError):
            validate_final_chunk(self.chunks_dir, segment_seconds=15)

    def test_validate_final_chunk_fails_closed_on_empty_dir(self) -> None:
        """When chunks_dir contains zero chunks, fail closed with EmptyAudioChunkError."""
        with self.assertRaises(EmptyAudioChunkError):
            validate_final_chunk(self.chunks_dir, segment_seconds=15)

    def test_shutdown_drain_and_master_stitch_guarantee(self) -> None:
        """Simulates abrupt SIGINT: 3 valid chunks followed by 0-byte stub stitch to master >4096 bytes."""
        for i in range(3):
            _create_pcm_wav(self.chunks_dir / f"chunk_{i:05d}.wav")

        # Simulate trailing stub created by FFmpeg receiving SIGINT
        stub = self.chunks_dir / "chunk_00003.wav"
        stub.write_bytes(b"")

        # Validate final chunk (should prune stub)
        validate_final_chunk(self.chunks_dir, segment_seconds=15)
        self.assertFalse(stub.exists())

        # Stitch master audio
        receipt = AudioMasterStitcher.stitch_master_wav(
            self.chunks_dir,
            self.master_wav,
            pad_missing_or_corrupt=True,
        )

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, "SUCCESS")
        self.assertEqual(receipt.total_chunks, 3)
        self.assertTrue(self.master_wav.exists())
        self.assertGreater(self.master_wav.stat().st_size, 4096)

    def test_recorder_stop_executes_stitch_even_if_validation_raises(self) -> None:
        """Guarantees that _stitch_master_audio is executed in stop() even if _validate_final_chunk encounters error."""
        recorder = MagicMock()
        recorder._is_stopped = False
        recorder.running = True
        recorder.mock_mode = True
        recorder.capture_manager = MagicMock()
        recorder.processed_chunks = ["chunk_00000.wav", "chunk_00001.wav"]
        recorder.chunks_dir = self.chunks_dir
        recorder._worktree_materialized = True
        recorder.worktree_dir = None
        recorder.worktree_branch = None
        recorder.transcript_md = self.tmp_path / "transcript.md"
        recorder.session_manifest = self.tmp_path / "manifest.json"
        recorder.acoustic_evidence_receipt_path = self.tmp_path / "acoustic_receipt.json"
        recorder._drain_remaining_chunks = MagicMock()
        recorder._flush_pending_pause = MagicMock()
        recorder._shutdown_background_services = MagicMock()
        recorder._cleanup_empty_session_or_seal = MagicMock()
        recorder._run_post_recording_diarization = MagicMock()

        # Create valid chunks
        _create_pcm_wav(self.chunks_dir / "chunk_00000.wav")
        _create_pcm_wav(self.chunks_dir / "chunk_00001.wav")

        # Force _validate_final_chunk to raise
        recorder._validate_final_chunk = MagicMock(side_effect=RuntimeError("Unexpected validation glitch"))
        recorder._stitch_master_audio = MagicMock()

        # Execute stop()
        LiveMeetingRecorder.stop(recorder)

        # _stitch_master_audio must have been called despite validation error
        recorder._stitch_master_audio.assert_called_once()
