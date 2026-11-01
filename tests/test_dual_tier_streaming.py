#!/usr/bin/env python3
"""
Wave 2 Unit & Integration Tests: Dual-Tier Fast-Draft / Authoritative Lock Streaming STT.
Target: Apple MacBook Pro (Apple Silicon Metal GPU).

Verifies:
  1. DualTierTranscriber draft vs. lock model dispatching.
  2. Single vs. Dual mode configuration and fallbacks.
  3. AsynchronousAudioQueue decoupled worker execution.
  4. LocalSTTEngine dual-tier feed_audio and speculative-to-confirmed locking.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock

import numpy as np

from pipeline_a_intake.contracts import AudioChunk
from pipeline_a_intake.local_stt import LocalSTTEngine
from pipeline_a_intake.local_stt_core.dual_tier_engine import (
    AsynchronousAudioQueue,
    DualTierTranscriber,
)
from storage.database import DatabaseManager


class TestDualTierEngine(unittest.TestCase):
    def test_dual_tier_transcriber_dispatch(self):
        mock_mlx = MagicMock()
        mock_mlx.transcribe.side_effect = [
            {"text": "fast draft text"},
            {"text": "authoritative locked text"},
        ]

        transcriber = DualTierTranscriber(
            draft_model="mlx-community/whisper-tiny",
            lock_model="mlx-community/whisper-large-v3-mlx",
            tier_mode="dual",
            mock_mode=False,
            mlx_whisper=mock_mlx,
        )

        audio = np.zeros(8000, dtype=np.float32)

        # 1. Draft pass
        draft_text = transcriber.transcribe_window(audio, start_ts=0.0, is_lock_pass=False)
        self.assertEqual(draft_text, "fast draft text")
        _, kwargs_draft = mock_mlx.transcribe.call_args
        self.assertEqual(kwargs_draft.get("language"), "en")
        self.assertEqual(kwargs_draft.get("without_timestamps"), True)

        # 2. Lock pass
        lock_text = transcriber.transcribe_window(audio, start_ts=0.0, is_lock_pass=True)
        self.assertEqual(lock_text, "authoritative locked text")
        _, kwargs_lock = mock_mlx.transcribe.call_args
        self.assertEqual(kwargs_lock.get("language"), "en")

    def test_single_mode_dispatches_lock_model(self):
        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = {"text": "single mode text"}

        transcriber = DualTierTranscriber(
            draft_model="mlx-community/whisper-tiny",
            lock_model="mlx-community/whisper-large-v3-mlx",
            tier_mode="single",
            mock_mode=False,
            mlx_whisper=mock_mlx,
        )

        audio = np.zeros(8000, dtype=np.float32)
        text = transcriber.transcribe_window(audio, start_ts=0.0, is_lock_pass=False)
        self.assertEqual(text, "single mode text")
        # In single mode, the lock model is invoked even for non-lock passes
        _, kwargs = mock_mlx.transcribe.call_args
        self.assertIn("whisper-large-v3-mlx", str(kwargs.get("path_or_hf_repo")))

    def test_async_audio_queue_worker(self):
        processed = []
        def worker(item):
            processed.append(item)

        q = AsynchronousAudioQueue(worker_fn=worker, max_queue_size=10, name="TestWorker")
        q.start()
        try:
            success = q.push("chunk_1")
            self.assertTrue(success)
            success = q.push("chunk_2")
            self.assertTrue(success)

            time.sleep(0.15)
            self.assertEqual(processed, ["chunk_1", "chunk_2"])
        finally:
            q.stop()

    def test_local_stt_engine_dual_tier_streaming_mock(self):
        try:
            db = DatabaseManager(allow_unencrypted_dev_fallback=True)
        except Exception:
            db = MagicMock()

        engine = LocalSTTEngine(
            db_manager=db,
            mock_mode=True,
            tier_mode="dual",
            session_id="test_dual_tier_sess",
        )

        audio = np.random.uniform(-0.1, 0.1, 16000).astype(np.float32)
        chunk = AudioChunk(
            chunk_id="chunk_test_01",
            meeting_id="test_meeting_01",
            audio_data=audio,
            timestamp_offset=0.0,
            speaker="Speaker 1",
            metadata={"mock_text": "Live dual-tier speech segment"},
        )

        deltas = engine.feed_audio(chunk)
        self.assertTrue(len(deltas) >= 1)
        delta = deltas[0]
        self.assertIn("Live dual-tier speech segment", delta.full_confirmed_text)

        # Test async queue interface
        pushed = engine.feed_audio_queued(chunk)
        self.assertTrue(pushed)


if __name__ == "__main__":
    unittest.main()
