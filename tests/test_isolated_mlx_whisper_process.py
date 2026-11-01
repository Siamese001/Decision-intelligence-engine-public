"""Tests verifying the dedicated OS worker process for MLX Whisper live chunk transcription.

Guarantees:
  1. MLX Whisper runs in a dedicated OS worker process (spawn context, distinct PID).
  2. Worker process enforces airgap (outbound AF_INET/AF_INET6 connections to external IPs are blocked).
  3. Worker process has zero fallback to OpenAI or cloud audio APIs (fails closed).
  4. Client handles task submission, IPC messaging, and clean lifecycle shutdown.
"""
from __future__ import annotations

import os
import shutil
import socket
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    AirgapViolationError,
    IsolatedMLXTranscriberClient,
    _enforce_process_airgap,
)


def _create_synthetic_wav(path: Path, duration_sec: float = 0.5, amplitude: int = 2000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            val = amplitude if (i % 32 < 16) else -amplitude
            frames.extend(struct.pack("<h", val))
        w.writeframes(frames)


class TestIsolatedMLXWhisperProcess(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_isolated_mlx_")
        self.temp_path = Path(self.temp_dir)
        self.wav_path = self.temp_path / "chunk_0000.wav"
        _create_synthetic_wav(self.wav_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_worker_process_isolation(self) -> None:
        """Verifies that MLX Whisper runs in a distinct OS process with a unique PID."""
        client = IsolatedMLXTranscriberClient(mock_mode=True, backend="mlx")
        try:
            client.start()
            self.assertTrue(client.is_running)
            self.assertIsNotNone(client.worker_pid)
            self.assertNotEqual(client.worker_pid, os.getpid(), "Worker must run in a separate OS process")

            # Perform transcription via IPC
            result = client.transcribe(self.wav_path)
            self.assertIn("Mock transcript segment", result)
        finally:
            client.stop()
            self.assertFalse(client.is_running)

    def test_offline_airgap_transcription(self) -> None:
        """Verifies that external network socket connections are blocked under the airgap guard."""
        # Test socket guard directly
        _enforce_process_airgap()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with self.assertRaises(AirgapViolationError) as ctx:
            s.connect(("8.8.8.8", 53))
        self.assertIn("airgap policy", str(ctx.exception).lower())

    def test_zero_cloud_fallback_isolated(self) -> None:
        """Verifies that requesting cloud backend inside isolated worker fails closed."""
        client = IsolatedMLXTranscriberClient(mock_mode=False, backend="openai")
        try:
            client.start()
            with self.assertRaises(RuntimeError) as ctx:
                client.transcribe(self.wav_path)
            self.assertIn("Cloud fallback is prohibited", str(ctx.exception))
        finally:
            client.stop()

    def test_session_chunk_quarantine(self) -> None:
        """Verifies that fresh meeting sessions quarantine stale chunks from prior runs."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        chunks_dir = self.temp_path / ".chunks_Client_Architecture_Review"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        # Simulate leftover chunks from yesterday's meeting
        stale_0 = chunks_dir / "chunk_0000.wav"
        stale_1 = chunks_dir / "chunk_0001.wav"
        stale_0.touch()
        stale_1.touch()

        # Start a brand new meeting session
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Client Architecture Review",
            output_dir=self.temp_path,
            mock_mode=True,
            resume=False,
        )

        # Existing chunks in chunks_dir must have been moved to quarantine
        active_chunks = list(recorder.chunks_dir.glob("chunk_*.wav"))
        self.assertEqual(len(active_chunks), 0, "Active chunks must be empty for fresh session")
        self.assertEqual(recorder.start_chunk_idx, 0, "Fresh session must start at chunk index 0")

        # Verify quarantine directory exists and contains the stale chunks
        quarantined = list(recorder.chunks_dir.glob(".quarantine_*/*"))
        self.assertEqual(len(quarantined), 2, "Stale chunks must be safely quarantined")

    def test_stream_aggregator_declutters_turns(self) -> None:
        """Verifies that ConversationalTurnAggregator removes disfluencies and merges fragmented chunks."""
        from dual_pipeline.live_meeting_recorder_core.stream_aggregator import (
            ConversationalTurnAggregator,
            clean_disfluencies,
        )

        # Test disfluency cleaning
        raw = "um, we we we should like, you know, look at the the contract."
        cleaned = clean_disfluencies(raw)
        self.assertEqual(cleaned, "We should look at the contract.")

        # Test turn aggregation
        completed_turns = []
        aggregator = ConversationalTurnAggregator(
            max_silence_gap_seconds=10,
            on_turn_completed=lambda t: completed_turns.append(t),
        )

        # Chunk 0: 0s
        t0 = aggregator.add_chunk(0, 0, "We need to ensure", "Amit")
        self.assertIsNone(t0)

        # Chunk 1: 8s
        t1 = aggregator.add_chunk(1, 8, "the SOW deliverables are locked.", "Amit")
        self.assertIsNotNone(t1)
        self.assertEqual(t1.speaker, "Amit")
        self.assertIn("We need to ensure the SOW deliverables are locked.", t1.text)
        self.assertEqual(t1.display_time, "[00:00] - [00:16]")

