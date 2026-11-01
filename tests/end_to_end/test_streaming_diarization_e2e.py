"""End-to-End Acceptance Suite for Streaming Diarization and Lock-Free Ring Buffer (Wave 3).

Verifies:
1. LockFreeAudioRingBuffer absorbs 30-second audio bursts without frame dropouts.
2. VADSilenceFilter rejects >= 95% of silent frames from non-speech audio.
3. StreamingDiarizer emits incremental speaker hypotheses with <= 500 ms emission lag.
4. Timestamp monotonicity and non-overlapping turn intervals are strictly preserved.
"""
from __future__ import annotations

import time
from unittest import TestCase

import numpy as np

from dual_pipeline.audio_ingestion_core.streaming_diarizer import (
    DEFAULT_STEP_SECONDS,
    DEFAULT_WINDOW_SECONDS,
    SAMPLE_RATE_HZ,
    LockFreeAudioRingBuffer,
    StreamingDiarizer,
    StreamingSpeakerTurn,
    VADSilenceFilter,
)


class TestStreamingDiarizationE2E(TestCase):
    """End-to-end tests for Wave 3 lock-free ring buffer and streaming diarization."""

    def test_ring_buffer_30s_burst_absorption_zero_drops(self) -> None:
        """Verifies ring buffer absorbs a 30-second audio burst with zero dropped frames."""
        ring = LockFreeAudioRingBuffer(capacity_seconds=60, sample_rate=SAMPLE_RATE_HZ)
        # 30 seconds of audio data
        burst_frames = int(30 * SAMPLE_RATE_HZ)
        burst_data = np.random.randn(burst_frames).astype(np.float32)

        written = ring.write_frames(burst_data)
        self.assertEqual(written, burst_frames)
        self.assertEqual(ring.dropped_frames, 0, "Burst must not drop any frames")
        self.assertEqual(ring.available_frames, burst_frames)

        # Read back in 1-second chunks
        read_frames = int(1.0 * SAMPLE_RATE_HZ)
        total_read = 0
        while ring.available_frames > 0:
            chunk = ring.read_frames(read_frames)
            total_read += chunk.size
        self.assertEqual(total_read, burst_frames)
        self.assertEqual(ring.available_frames, 0)

    def test_vad_silence_filter_rejection_ratio(self) -> None:
        """Verifies VAD silence filter drops >= 95% of frames in a silent audio stream."""
        vad = VADSilenceFilter(energy_threshold=0.005)
        # 10 seconds of background silence/noise (RMS << 0.005)
        silent_audio = np.random.randn(10 * SAMPLE_RATE_HZ).astype(np.float32) * 0.0001

        filtered, silence_count, speech_count = vad.filter_stream(silent_audio, chunk_size=512)
        total = silence_count + speech_count
        rejection_ratio = silence_count / total if total > 0 else 0.0

        self.assertGreaterEqual(rejection_ratio, 0.95, f"Expected >= 95% silence rejection, got {rejection_ratio:.2%}")
        self.assertEqual(filtered.size, 0)

    def test_streaming_diarization_emission_lag_and_monotonicity(self) -> None:
        """Verifies incremental speaker turn hypotheses are emitted within 500 ms with monotonic timestamps."""
        diarizer = StreamingDiarizer(window_seconds=2.0, step_seconds=1.0, sample_rate=SAMPLE_RATE_HZ)

        # Generate 6 seconds of alternating speech signal (RMS > 0.05)
        t = np.linspace(0, 6, 6 * SAMPLE_RATE_HZ, dtype=np.float32)
        speech_signal = np.sin(2 * np.pi * 440 * t) * 0.2

        diarizer.ingest_audio_chunk(speech_signal)
        self.assertEqual(diarizer.ring_buffer.dropped_frames, 0)

        turns: list[StreamingSpeakerTurn] = []
        for _ in range(6):
            new_turns = diarizer.process_next_window()
            turns.extend(new_turns)

        self.assertGreaterEqual(len(turns), 4)

        prev_end = 0.0
        for turn in turns:
            # Emission lag constraint
            self.assertLessEqual(turn.emission_lag_ms, 500.0, f"Emission lag exceeded 500 ms: {turn.emission_lag_ms}")
            # Monotonicity
            self.assertGreaterEqual(turn.start_s, prev_end - 1e-4)
            self.assertGreater(turn.end_s, turn.start_s)
            self.assertIn(turn.speaker_id, ["SPEAKER_00", "SPEAKER_01"])
            prev_end = turn.end_s
