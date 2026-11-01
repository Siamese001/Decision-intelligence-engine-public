"""
tests/test_instant_audio_spooler.py

Unit tests for Wave 1 InstantAudioSpooler:
1. Verifies sub-200ms eager startup latency.
2. Verifies mock audio spooling and chunk generation.
3. Verifies handshake_and_splice() migration and handoff receipt.
4. Verifies graceful stop and clean ephemeral discard.
"""
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import (
    SpoolHandoffReceipt,
    SpoolStatus,
)


class TestInstantAudioSpooler(unittest.TestCase):
    """Test suite for InstantAudioSpooler."""

    def setUp(self):
        self.tmp_root = Path(tempfile.mkdtemp(prefix="test_spooler_"))
        self.spool_dir = self.tmp_root / "spool"
        self.target_dir = self.tmp_root / "target_chunks"

    def tearDown(self):
        if self.tmp_root.exists():
            shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_instant_startup_latency_under_200ms(self):
        """InstantAudioSpooler eager spawn completes within 200 milliseconds."""
        t0 = time.perf_counter()
        spooler = InstantAudioSpooler.spawn_instant(
            spool_dir=self.spool_dir,
            segment_seconds=5,
            mock_mode=True,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        try:
            self.assertTrue(spooler.is_running)
            self.assertEqual(spooler.status, SpoolStatus.ACTIVE_SPOOLING)
            self.assertLess(
                spooler.startup_latency_ms,
                200.0,
                f"Spooler startup latency {spooler.startup_latency_ms:.2f}ms exceeded 200ms limit",
            )
            self.assertLess(
                elapsed_ms,
                200.0,
                f"Total spawn execution {elapsed_ms:.2f}ms exceeded 200ms limit",
            )
            self.assertTrue(self.spool_dir.exists())
            self.assertTrue((self.spool_dir / "spool_manifest.json").exists())
        finally:
            spooler.stop()

    def test_ephemeral_spool_chunk_generation(self):
        """InstantAudioSpooler produces valid 16kHz audio chunks into the ephemeral spool."""
        spooler = InstantAudioSpooler.spawn_instant(
            spool_dir=self.spool_dir,
            segment_seconds=1,  # Short 1s segments for fast test
            mock_mode=True,
        )
        try:
            # Wait up to 1.5 seconds for at least one chunk to generate
            deadline = time.time() + 1.5
            found_chunk = None
            while time.time() < deadline:
                chunks = list(self.spool_dir.glob("chunk_*.wav"))
                if chunks:
                    found_chunk = chunks[0]
                    break
                time.sleep(0.05)

            self.assertIsNotNone(found_chunk, "No chunk was generated within timeout")
            self.assertTrue(found_chunk.exists())
            self.assertGreater(found_chunk.stat().st_size, 1000)

            with wave.open(str(found_chunk), "rb") as w:
                self.assertEqual(w.getframerate(), 16000)
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getsampwidth(), 2)
        finally:
            spooler.stop()

    def test_handshake_and_splice_handoff(self):
        """handshake_and_splice migrates spooled chunks into target directory with receipt."""
        spooler = InstantAudioSpooler(
            spool_dir=self.spool_dir,
            segment_seconds=5,
            mock_mode=True,
        )
        spooler.spool_dir.mkdir(parents=True, exist_ok=True)

        # Pre-seed 3 synthetic chunks
        from dual_pipeline.live_meeting_recorder_core.audio_utils import (
            generate_synthetic_speech_chunk,
        )
        generate_synthetic_speech_chunk(self.spool_dir / "chunk_00000.wav", duration_sec=5.0)
        generate_synthetic_speech_chunk(self.spool_dir / "chunk_00001.wav", duration_sec=5.0)
        generate_synthetic_speech_chunk(self.spool_dir / "chunk_00002.wav", duration_sec=5.0)
        generate_synthetic_speech_chunk(self.spool_dir / "master_spool.wav", duration_sec=15.0)

        target_master = self.tmp_root / "master.wav"
        receipt = spooler.handshake_and_splice(
            target_chunks_dir=self.target_dir,
            target_master_wav=target_master,
        )

        self.assertIsInstance(receipt, SpoolHandoffReceipt)
        self.assertEqual(receipt.migrated_chunk_count, 3)
        self.assertEqual(receipt.migrated_chunk_indices, [0, 1, 2])
        self.assertAlmostEqual(receipt.total_audio_duration_seconds, 15.0, delta=0.5)
        self.assertTrue(receipt.is_gapless)
        self.assertLess(receipt.handoff_latency_ms, 500.0)

        # Verify target files exist
        self.assertTrue((self.target_dir / "chunk_00000.wav").exists())
        self.assertTrue((self.target_dir / "chunk_00001.wav").exists())
        self.assertTrue((self.target_dir / "chunk_00002.wav").exists())
        self.assertTrue(target_master.exists())

    def test_discard_ephemeral_cleanup(self):
        """discard() stops capture and completely unlinks the ephemeral spool directory."""
        spooler = InstantAudioSpooler.spawn_instant(
            spool_dir=self.spool_dir,
            segment_seconds=5,
            mock_mode=True,
        )
        self.assertTrue(self.spool_dir.exists())

        spooler.discard()
        self.assertFalse(spooler.is_running)
        self.assertEqual(spooler.status, SpoolStatus.DISCARDED)
        self.assertFalse(self.spool_dir.exists())


if __name__ == "__main__":
    unittest.main()
