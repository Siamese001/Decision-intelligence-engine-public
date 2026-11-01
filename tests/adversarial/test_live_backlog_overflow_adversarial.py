"""tests/adversarial/test_live_backlog_overflow_adversarial.py

Adversarial stress and edge-case testing for the decoupled ASR worker queue (Wave 6).
Verifies:
1. Slow ASR worker buffering: when ASR inference is slower than chunk emission, the queue
   buffers chunks safely and logs backlog warnings without audio loss.
2. Complete queue drain on stop(): stop() blocks until all buffered chunks are processed,
   ensuring zero chunk drop on session termination.
3. Corrupt/truncated chunk resilience: malformed WAV chunks injected into the queue are rejected
   by ChunkHealth without crashing the worker thread or blocking subsequent valid chunks.
4. Bounded queue protection: _chunk_queue has a maximum capacity (64) to prevent unbounded memory growth.
"""

from __future__ import annotations

import shutil
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

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


class TestLiveBacklogOverflowAdversarial(unittest.TestCase):
    """Adversarial suite testing queue backpressure, worker resilience, and drain safety."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.output_dir = self.temp_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ChunkHealth.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        ChunkHealth.clear_cache()

    def test_slow_asr_worker_buffering_and_drain(self):
        """Simulates high chunk arrival rate with slow ASR inference; verifies buffering, warning, and 100% drain."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Slow Worker Backlog Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )

        # Pre-populate 5 valid chunk files
        chunk_count = 5
        for i in range(chunk_count):
            cf = recorder.chunks_dir / f"chunk_{i:05d}.wav"
            create_synthetic_wav(cf, n_samples=16000)

        # Simulate artificial delay in ASR worker
        original_process = recorder.process_chunk

        def slow_process(wav_path: Path, chunk_idx: int):
            time.sleep(0.04)  # 40ms per chunk
            return original_process(wav_path, chunk_idx)

        recorder.process_chunk = slow_process

        # Start worker and enqueue all chunks rapidly
        recorder._start_asr_worker()
        self.assertIsNotNone(recorder._asr_worker_thread)
        self.assertTrue(recorder._asr_worker_thread.is_alive())

        for cf in sorted(recorder.chunks_dir.glob("chunk_*.wav")):
            recorder._process_chunk_file(cf)

        # Verify that backlog was tracked and reached > 0
        self.assertGreater(recorder._max_backlog, 0)

        # Trigger stop() - must drain all queued items
        recorder.stop()

        # All chunks must be processed
        self.assertEqual(len(recorder.processed_chunks), chunk_count)
        self.assertEqual(recorder._chunk_queue.qsize(), 0)
        self.assertIsNone(recorder._asr_worker_thread)

    def test_corrupted_and_truncated_chunk_rejection_in_queue(self):
        """Verifies that corrupted, truncated, and header-only chunks are safely skipped by ChunkHealth."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Corrupt Injection Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )

        # 1. Valid chunk
        c0 = recorder.chunks_dir / "chunk_00000.wav"
        create_synthetic_wav(c0, n_samples=16000)

        # 2. Corrupted file (garbage bytes, 30 bytes)
        c1 = recorder.chunks_dir / "chunk_00001.wav"
        c1.write_bytes(b"CORRUPTED_RAW_GARBAGE_PAYLOAD")

        # 3. Header-only WAV (44 bytes, 0 frames)
        c2 = recorder.chunks_dir / "chunk_00002.wav"
        with wave.open(str(c2), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)

        # 4. Valid chunk
        c3 = recorder.chunks_dir / "chunk_00003.wav"
        create_synthetic_wav(c3, n_samples=16000)

        recorder._start_asr_worker()

        # Enqueue all chunks
        for cf in [c0, c1, c2, c3]:
            recorder._process_chunk_file(cf)

        recorder.stop()

        # Only the 2 valid chunks should have been processed
        self.assertIn("chunk_00000.wav", recorder.processed_chunks)
        self.assertNotIn("chunk_00001.wav", recorder.processed_chunks)
        self.assertNotIn("chunk_00002.wav", recorder.processed_chunks)
        self.assertIn("chunk_00003.wav", recorder.processed_chunks)

        # Worker must have terminated cleanly without exception death
        self.assertIsNone(recorder._asr_worker_thread)

    def test_bounded_queue_capacity_and_drain_idempotence(self):
        """Queue capacity is bounded at 64, and draining an empty or already-stopped worker is idempotent."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Queue Bounded Capacity Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )
        try:
            self.assertEqual(recorder._chunk_queue.maxsize, 64)

            # Drain on non-started worker should be a clean no-op
            recorder._drain_asr_worker()
            self.assertIsNone(recorder._asr_worker_thread)

            # Start worker, do not put items, drain immediately
            recorder._start_asr_worker()
            self.assertIsNotNone(recorder._asr_worker_thread)
            recorder._drain_asr_worker()
            self.assertIsNone(recorder._asr_worker_thread)

            # Second drain is safe
            recorder._drain_asr_worker()
            self.assertIsNone(recorder._asr_worker_thread)
        finally:
            recorder._release_slug_lock()


if __name__ == "__main__":
    unittest.main()
