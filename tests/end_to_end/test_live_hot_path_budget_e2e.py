"""tests/end_to_end/test_live_hot_path_budget_e2e.py

End-to-End hot-path performance & live-UX right-sizing verification suite (Wave 6).
Verifies that:
1. M_1 = 1.0: Zero speculative preview emissions; exactly 1 ASR invocation per finalized chunk.
2. M_2 <= 1: Single-pass chunk health evaluation with in-memory caching eliminates redundant WAV file opens.
3. Decoupled ASR Queue: _chunk_queue isolates ASR inference from poll loop, maintaining poll lag < 1.5s.
4. Zero Listeners on Port 8765: LiveMeetingRecorder binds 0 network sockets and ws_server is None.
5. Clean Decommissioning: ws_server.py, live_transcript_hud.py, and live_speech_view.py are deleted.
"""

from __future__ import annotations

import json
import shutil
import socket
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.chunk_health import ChunkHealth


def create_synthetic_wav(path: Path, n_samples: int = 16000, sample_rate: int = 16000) -> Path:
    """Helper to write a valid 16-bit mono PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        data = struct.pack(f"<{n_samples}h", *[1200] * n_samples)
        w.writeframes(data)
    return path


class TestLiveHotPathBudgetE2E(unittest.TestCase):
    """End-to-end verification of Wave 6 hot-path performance budgets and UX right-sizing."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.output_dir = self.temp_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ChunkHealth.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        ChunkHealth.clear_cache()

    def test_speculative_preview_elimination_m1_budget(self):
        """M_1 Invariant: Exactly 1.0 ASR invocations per chunk; zero speculative previews."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Hot Path M1 Budget Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )

        # Pre-populate 3 valid chunk files
        chunk_files = []
        for i in range(3):
            cf = recorder.chunks_dir / f"chunk_{i:05d}.wav"
            create_synthetic_wav(cf, n_samples=16000)
            chunk_files.append(cf)

        transcribe_call_count = 0
        original_transcribe = recorder._transcribe_chunk

        def counted_transcribe(cf_path: Path, **kwargs):
            nonlocal transcribe_call_count
            transcribe_call_count += 1
            return f"Transcribed speech content for {cf_path.name}."

        recorder._transcribe_chunk = counted_transcribe

        # Run recorder for 3 iterations to process all 3 chunks
        recorder.run(max_iterations=3)

        # M_1 budget: exactly 3 chunks -> exactly 3 ASR calls (1.0 call per chunk)
        self.assertEqual(len(recorder.processed_chunks), 3)
        self.assertEqual(
            transcribe_call_count,
            3,
            f"Expected M_1 = 1.0 (3 ASR calls for 3 chunks), but got {transcribe_call_count}",
        )

        # Verify no preview events recorded in segments.jsonl
        lines = recorder.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            event = json.loads(line)
            self.assertTrue(event.get("is_final", False))
            self.assertFalse(event.get("segment_id", "").startswith("preview_"))

        # Verify emit_speculative_preview returns None (tombstone)
        self.assertIsNone(recorder.emit_speculative_preview(chunk_files[0], 0))

    def test_single_pass_chunk_health_and_caching_m2_budget(self):
        """M_2 Invariant: ChunkHealth evaluates in single file open; subsequent checks hit in-memory cache."""
        test_wav = self.temp_dir / "bench_chunk.wav"
        create_synthetic_wav(test_wav, n_samples=32000)  # 2.0s

        # Wrap wave.open to count physical disk opens
        original_wave_open = wave.open
        open_count = 0

        def counting_wave_open(*args, **kwargs):
            nonlocal open_count
            open_count += 1
            return original_wave_open(*args, **kwargs)

        with patch("wave.open", side_effect=counting_wave_open):
            # First pass: reads header and samples once
            h1 = ChunkHealth.evaluate(test_wav)
            self.assertTrue(h1.is_valid)
            self.assertTrue(h1.is_audible)
            self.assertFalse(h1.is_silence)
            self.assertAlmostEqual(h1.duration_sec, 2.0, places=2)
            self.assertGreater(h1.rms, 1000.0)
            self.assertEqual(open_count, 1)

            # Second pass on identical file: in-memory cache hit
            h2 = ChunkHealth.evaluate(test_wav)
            self.assertIs(h1, h2)
            self.assertEqual(open_count, 1, "Cache miss: wave.open was invoked again!")

            # Third pass via recorder helper: still cache hit
            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Cache Hit Test",
                output_dir=self.output_dir,
                mock_mode=True,
                enforce_worktree=False,
            )
            try:
                dur = recorder._get_chunk_duration(test_wav)
                self.assertAlmostEqual(dur, 2.0, places=2)
                self.assertEqual(open_count, 1, "Duration check opened file instead of hitting cache!")

                audible = recorder._is_chunk_audible(test_wav)
                self.assertTrue(audible)
                self.assertEqual(open_count, 1, "Audibility check opened file instead of hitting cache!")
            finally:
                recorder._release_slug_lock()

    def test_decoupled_asr_queue_and_drain(self):
        """Queue Invariant: _chunk_queue decouples poll loop from ASR worker and drains completely on stop()."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Decoupled Queue Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )

        for i in range(4):
            cf = recorder.chunks_dir / f"chunk_{i:05d}.wav"
            create_synthetic_wav(cf, n_samples=16000)

        # Run recorder
        recorder.run(max_iterations=4)

        # Queue must have drained to 0 items and worker thread joined
        self.assertEqual(recorder._chunk_queue.qsize(), 0)
        self.assertIsNone(recorder._asr_worker_thread)
        self.assertEqual(len(recorder.processed_chunks), 4)

        # Poll lag must be tracked and < 1.5s
        self.assertLess(recorder._poll_lag_sec, 1.5)

    def test_zero_listeners_on_websocket_port(self):
        """Zero Listeners Invariant: No socket bound to port 8765; port remains free for foreign binders."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Zero WS Port Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )
        try:
            self.assertIsNone(recorder.ws_server)

            # Assert port 8765 can be bound by standard socket without address collision
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_sock:
                test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                test_sock.bind(("127.0.0.1", 8765))
                test_sock.listen(1)
                self.assertEqual(test_sock.getsockname()[1], 8765)
        finally:
            recorder._release_slug_lock()

    def test_decommissioned_artifacts_deleted_from_disk(self):
        """Decommissioned Invariant: ws_server.py, live_transcript_hud.py, and live_speech_view.py do not exist."""
        root = Path(__file__).resolve().parents[2]
        self.assertFalse((root / "pipeline_a_intake" / "ws_server.py").exists())
        self.assertFalse((root / "scripts" / "live_transcript_hud.py").exists())
        self.assertFalse((root / "ui" / "views" / "live_speech_view.py").exists())
        self.assertFalse((root / "tests" / "test_websocket_stream_protocol.py").exists())


if __name__ == "__main__":
    unittest.main()
