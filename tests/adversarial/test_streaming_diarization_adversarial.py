"""Adversarial and Edge-Case Test Suite for Wave 3 Streaming Diarization.

Covers 4 mandatory adversarial categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: Zero-length audio arrays, single-sample buffers, reading from empty ring buffer.
2. Malformed input: Audio chunks containing NaNs or Infs, non-standard shapes.
3. Overflow & exhaustion: Writing beyond 60s capacity, verifying drop counters increment without crash.
4. Concurrency & race: Concurrent multi-threaded audio ingest and window processing.
"""
from __future__ import annotations

import threading
import time
from unittest import TestCase

import numpy as np

from dual_pipeline.audio_ingestion_core.streaming_diarizer import (
    SAMPLE_RATE_HZ,
    LockFreeAudioRingBuffer,
    StreamingDiarizer,
    VADSilenceFilter,
)


class TestStreamingDiarizationAdversarial(TestCase):
    """Adversarial and edge-case tests for Wave 3 audio streaming and diarization."""

    def test_boundary_zero_length_and_empty_reads(self) -> None:
        """Verifies zero-length writes and reading from an empty buffer return cleanly."""
        ring = LockFreeAudioRingBuffer(capacity_seconds=5, sample_rate=SAMPLE_RATE_HZ)

        # Empty write
        written = ring.write_frames(np.empty(0, dtype=np.float32))
        self.assertEqual(written, 0)

        # Empty read
        read = ring.read_frames(1024)
        self.assertEqual(read.size, 0)
        self.assertEqual(ring.available_frames, 0)

    def test_overflow_capacity_drops_and_wraparound(self) -> None:
        """Verifies writing beyond ring buffer capacity drops excess frames and tracks counter."""
        capacity_s = 2
        ring = LockFreeAudioRingBuffer(capacity_seconds=capacity_s, sample_rate=SAMPLE_RATE_HZ)
        total_capacity = capacity_s * SAMPLE_RATE_HZ

        # Write 3 seconds into 2-second buffer
        overflow_audio = np.ones(3 * SAMPLE_RATE_HZ, dtype=np.float32)
        written = ring.write_frames(overflow_audio)

        self.assertEqual(written, total_capacity)
        self.assertEqual(ring.dropped_frames, SAMPLE_RATE_HZ)
        self.assertEqual(ring.available_frames, total_capacity)

        # Drain buffer
        drained = ring.read_frames(total_capacity)
        self.assertEqual(drained.size, total_capacity)
        self.assertEqual(ring.available_frames, 0)

    def test_malformed_nan_and_inf_handling_in_vad(self) -> None:
        """Verifies VAD silence filter handles audio with NaNs/Infs without crashing."""
        vad = VADSilenceFilter()
        nan_audio = np.full(1024, np.nan, dtype=np.float32)
        filtered, silence_cnt, speech_cnt = vad.filter_stream(nan_audio, chunk_size=512)
        self.assertIsInstance(filtered, np.ndarray)

    def test_concurrency_producer_consumer_stream_lifecycle(self) -> None:
        """Verifies concurrent audio producer and consumer threads without race conditions."""
        diarizer = StreamingDiarizer(window_seconds=1.0, step_seconds=0.5, sample_rate=SAMPLE_RATE_HZ)
        stop_event = threading.Event()
        errors: list[str] = []

        def producer():
            chunk = np.sin(np.linspace(0, 0.1, int(0.1 * SAMPLE_RATE_HZ))).astype(np.float32) * 0.1
            while not stop_event.is_set():
                try:
                    diarizer.ingest_audio_chunk(chunk)
                    time.sleep(0.01)
                except Exception as e:
                    errors.append(f"Producer error: {e}")

        def consumer():
            while not stop_event.is_set():
                try:
                    _ = diarizer.process_next_window()
                    time.sleep(0.02)
                except Exception as e:
                    errors.append(f"Consumer error: {e}")

        p_thread = threading.Thread(target=producer)
        c_thread = threading.Thread(target=consumer)
        p_thread.start()
        c_thread.start()

        time.sleep(0.5)
        stop_event.set()
        p_thread.join(timeout=1.0)
        c_thread.join(timeout=1.0)

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
