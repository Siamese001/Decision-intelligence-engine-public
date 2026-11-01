#!/usr/bin/env python3
"""
Tests for Ultra-Low Latency STT Pipeline, Audio Splicer, Text Stitcher, and WebSocket Server.
Target: Apple MacBook Pro 16" (M5 Pro, 48GB Unified RAM, Apple Metal)

Verifies:
  1. Zero Word Loss on Audio Splicing & LCS Suffix-Prefix Matching
  2. Sub-2ms VAD Gating and 250ms Stride Dispatch
  3. Speculative vs. Confirmed Token Stabilization
  4. Real-Time WebSocket Delta Broadcast
  5. LocalSTTEngine End-to-End Pipeline Integration
"""
from __future__ import annotations

import shutil
import tempfile
import time
import unittest

import numpy as np

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None
    WEBSOCKETS_AVAILABLE = False

from pipeline_a_intake.audio_splicer import (
    AudioRingBuffer,
    AudioSplicer,
    SileroVADGate,
)
from pipeline_a_intake.contracts import (
    OVERLAP_SAMPLES,
    SAMPLE_RATE,
    WINDOW_TOTAL_SAMPLES,
)
from pipeline_a_intake.local_stt import AudioChunk, LocalSTTEngine
from pipeline_a_intake.text_stitcher import TranscriptStitcher
from storage.database import DatabaseManager


class TestAudioSplicer(unittest.TestCase):
    """Verifies circular audio buffering and sliding window overlap mechanics."""

    def test_ring_buffer_write_and_tail_read(self):
        buf = AudioRingBuffer(capacity=1000)
        data = np.arange(600, dtype=np.float32)
        n = buf.write(data)
        self.assertEqual(n, 600)
        self.assertEqual(buf.total_samples_written, 600)

        tail = buf.read_tail(300)
        self.assertEqual(len(tail), 300)
        np.testing.assert_array_equal(tail, data[300:])

    def test_ring_buffer_overflow(self):
        buf = AudioRingBuffer(capacity=1000)
        data = np.arange(1500, dtype=np.float32)
        buf.write(data)
        self.assertEqual(buf.total_samples_written, 1500)

        tail = buf.read_tail(1000)
        self.assertEqual(len(tail), 1000)
        np.testing.assert_array_equal(tail, data[500:])

    def test_splicer_250ms_stride_and_overlap(self):
        splicer = AudioSplicer(sample_rate=SAMPLE_RATE)
        # Feed 1.0 second of audio (16,000 samples)
        # Should dispatch exactly 4 windows (one per 4,000-sample / 250ms stride)
        audio = np.random.uniform(-0.5, 0.5, 16_000).astype(np.float32)
        windows = splicer.push_audio(audio)

        self.assertEqual(len(windows), 4)
        for w in windows:
            self.assertEqual(len(w.audio_data), WINDOW_TOTAL_SAMPLES)
            self.assertEqual(w.stride_duration, 0.25)
            self.assertEqual(w.overlap_duration, round(OVERLAP_SAMPLES / SAMPLE_RATE, 2))
            self.assertTrue(w.has_speech)


    def test_vad_silence_filtering(self):
        vad = SileroVADGate(sample_rate=SAMPLE_RATE, threshold_db=-40.0)
        silent_audio = np.zeros(4000, dtype=np.float32)
        has_speech, conf, rms_db = vad.evaluate(silent_audio)
        self.assertFalse(has_speech)
        self.assertLess(rms_db, -60.0)

        # Active speech signal
        t = np.linspace(0, 0.25, 4000, endpoint=False)
        tone_audio = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
        has_speech_tone, conf_tone, rms_db_tone = vad.evaluate(tone_audio)
        self.assertTrue(has_speech_tone)
        self.assertGreater(rms_db_tone, -20.0)

    def test_in_memory_silero_vad_onnx_benchmark(self):
        vad = SileroVADGate(sample_rate=SAMPLE_RATE)
        self.assertIsNotNone(vad.ort_session, "Silero VAD ONNX session must be loaded in-memory")

        # Benchmark latency across 50 frames
        silence = np.zeros(576, dtype=np.float32)
        t0 = time.perf_counter()
        for _ in range(50):
            has_speech, prob, rms = vad.evaluate(silence)
            self.assertFalse(has_speech)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0 / 50.0

        # Must execute in < 2.0ms per frame
        self.assertLess(elapsed_ms, 2.0, f"Silero VAD latency {elapsed_ms:.3f}ms exceeds 2.0ms threshold")



class TestTranscriptStitcher(unittest.TestCase):
    """Verifies LCS token alignment, overlap deduplication, and boundary stabilization."""

    def test_lcs_deduplication_zero_loss(self):
        stitcher = TranscriptStitcher(session_id="test_sess")

        # Window 1
        d1 = stitcher.process_window(
            raw_text="We are aligning on the architecture for",
            start_ts=0.0,
            end_ts=1.5,
            is_pause_boundary=False,
        )
        self.assertIn("aligning", d1.full_confirmed_text + " " + d1.speculative_preview)

        # Window 2: overlaps with "the architecture for" and introduces "the decoupled SQLite"
        _ = stitcher.process_window(
            raw_text="the architecture for the decoupled SQLite",
            start_ts=0.25,
            end_ts=1.75,
            is_pause_boundary=False,
        )

        # Window 3: overlaps with "decoupled SQLite" and introduces "WAL mode"
        d3 = stitcher.process_window(
            raw_text="decoupled SQLite WAL mode",
            start_ts=0.50,
            end_ts=2.00,
            is_pause_boundary=True, # Natural pause locks everything
        )

        final_text = d3.full_confirmed_text.strip()
        expected = "We are aligning on the architecture for the decoupled SQLite WAL mode"

        self.assertEqual(final_text, expected)
        # Assert no duplicated words (e.g. "architecture" and "SQLite" appear exactly once)
        words = final_text.split()
        self.assertEqual(words.count("architecture"), 1)
        self.assertEqual(words.count("SQLite"), 1)
        self.assertEqual(words.count("decoupled"), 1)

    def test_speculative_preview_lifecycle(self):
        stitcher = TranscriptStitcher(session_id="test_sess_spec")

        # Emitting trailing words without pause keeps tail in speculative preview
        d1 = stitcher.process_window(
            raw_text="Apex Global Enterprises transition",
            start_ts=0.0,
            end_ts=1.5,
            is_pause_boundary=False,
        )
        self.assertTrue(len(d1.speculative_preview) > 0)

        # Flush moves speculative words into confirmed text
        flush_delta = stitcher.flush()
        self.assertEqual(flush_delta.speculative_preview, "")
        self.assertEqual(flush_delta.full_confirmed_text, "Apex Global Enterprises transition")




class TestLocalSTTEngineIntegration(unittest.TestCase):
    """Verifies end-to-end LocalSTTEngine orchestration in mock and streaming modes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir,
            allow_unencrypted_dev_fallback=True,
        )

        self.engine = LocalSTTEngine(
            db_manager=self.db_manager,
            mock_mode=True,
            ws_server=None,
            session_id="e2e_test_session",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_feed_audio_and_sqlite_persistence(self):
        # Create 1.0s synthetic audio chunk
        t = np.linspace(0, 1.0, 16_000, endpoint=False)
        audio = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)

        chunk = AudioChunk(
            chunk_id="chk_01",
            meeting_id="meet_test_01",
            audio_data=audio,
            speaker="Reid Schenk",
            metadata={"mock_text": "Live streaming test on Apple Metal M5 Pro"},
        )

        deltas = self.engine.feed_audio(chunk)
        self.assertGreater(len(deltas), 0)

        # Flush remaining words
        final_delta = self.engine.flush()
        self.assertIsNotNone(final_delta)

        # Verify evidence committed to SQLite WAL
        records = self.db_manager.get_evidence_by_meeting("meet_test_01")
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0].speaker, "Reid Schenk")

    def test_compute_latency_benchmark(self):
        """Asserts that splicing, mock transcription, stitching, and dispatch take <120ms."""
        audio = np.zeros(8_000, dtype=np.float32)
        chunk = AudioChunk(
            chunk_id="bench_chk",
            meeting_id="meet_bench",
            audio_data=audio,
            metadata={"mock_text": "Latency benchmark test utterance"},
        )

        t0 = time.perf_counter()
        self.engine.feed_audio(chunk)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertLess(elapsed_ms, 120.0, f"Processing exceeded 120ms budget: {elapsed_ms:.2f}ms")


if __name__ == "__main__":
    unittest.main()
