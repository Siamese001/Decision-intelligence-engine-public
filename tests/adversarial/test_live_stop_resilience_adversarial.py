"""Adversarial and edge-case test suite for Wave 4: stop() resilience, skip-and-pad stitching, and anti-autonomous attribution."""
from __future__ import annotations

import contextlib
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from dual_pipeline.exceptions import DiarizationCollapseError
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import (
    AudioMasterStitcher,
    StitchReceipt,
)


def _create_sine_wav(path: Path, n_samples: int, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = [int(800 * (i % 32) / 32) for i in range(n_samples)]
        w.writeframes(struct.pack(f"<{n_samples}h", *samples))


class TestLiveStopResilienceAdversarial(unittest.TestCase):
    """Adversarial stress testing across corrupt chunks, missing sequence gaps, and diarization exceptions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_w4_adv_")
        self.tmp_path = Path(self.temp_dir)
        self._recorders: list[Any] = []

    def tearDown(self) -> None:
        for rec in self._recorders:
            with contextlib.suppress(Exception):
                rec._release_slug_lock()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_category_a_mid_session_corrupt_chunk_padded_with_silence(self) -> None:
        """Category A (Boundary/Corrupt Payloads): Corrupted chunk in sequence is padded without aborting stop()."""
        chunks_dir = self.tmp_path / "chunks"
        master_wav = self.tmp_path / "master.wav"

        _create_sine_wav(chunks_dir / "chunk_0000.wav", 16000)
        # Corrupt chunk_0001: garbage truncated bytes
        bad_chunk = chunks_dir / "chunk_0001.wav"
        bad_chunk.write_bytes(b"RIFF\x00\x00\x00\x00CORRUPT_GARBAGE_PAYLOAD")
        _create_sine_wav(chunks_dir / "chunk_0002.wav", 16000)

        # In strict fail-fast mode (pad=False), it must raise
        with self.assertRaises(RuntimeError):
            AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav, pad_missing_or_corrupt=False)

        # In resilient skip-and-pad mode (pad=True), it succeeds with PADDED_SUCCESS
        receipt = AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav, pad_missing_or_corrupt=True)
        self.assertIsInstance(receipt, StitchReceipt)
        self.assertTrue(receipt.success)
        self.assertEqual(receipt.status, "PADDED_SUCCESS")
        self.assertEqual(receipt.total_chunks, 3)
        self.assertEqual(receipt.valid_chunks, 2)
        self.assertEqual(receipt.padded_chunks, 1)
        self.assertIn(1, receipt.details.get("padded_indices", []))
        self.assertAlmostEqual(receipt.duration_s, 3.0, places=1)

    def test_category_b_sequence_discontinuity_missing_middle_chunk(self) -> None:
        """Category B (Sequence Discontinuity): Missing chunk_0001 in sequence [0, 2] is synthesized as silence."""
        chunks_dir = self.tmp_path / "chunks_gap"
        master_wav = self.tmp_path / "master_gap.wav"

        _create_sine_wav(chunks_dir / "chunk_0000.wav", 16000)
        # chunk_0001 is omitted entirely
        _create_sine_wav(chunks_dir / "chunk_0002.wav", 16000)

        # Strict mode raises gap detected
        with self.assertRaises(RuntimeError) as ctx:
            AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav, pad_missing_or_corrupt=False)
        self.assertIn("STITCHER-GAP-DETECTED", str(ctx.exception))

        # Resilient mode pads the missing chunk span with exact duration
        receipt = AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav, pad_missing_or_corrupt=True)
        self.assertTrue(receipt.success)
        self.assertEqual(receipt.status, "PADDED_SUCCESS")
        self.assertEqual(receipt.total_chunks, 3)
        self.assertEqual(receipt.padded_chunks, 1)
        self.assertEqual(receipt.details["padded_indices"], [1])

        # Verify master WAV frame count
        with wave.open(str(master_wav), "rb") as w:
            self.assertEqual(w.getnframes(), 48000)  # 3 x 16,000 frames = 3.0 seconds

    def test_category_c_diarization_collapse_never_aborts_seal(self) -> None:
        """Category C (Downstream / Enrichment Exceptions): DiarizationCollapseError in Stage 4 never unseals."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Collapse Test Session",
            meeting_date="10/04/2026",
            attendees="Marcus PartnerAdvisory, Fred Silent, Amit Ayer",
            output_dir=self.tmp_path / "output_collapse",
            mock_mode=True,
        )
        self._recorders.append(recorder)

        recorder.chunks_dir.mkdir(parents=True, exist_ok=True)
        c0 = recorder.chunks_dir / "chunk_00000.wav"
        _create_sine_wav(c0, 16000)
        recorder.process_chunk(c0, 0)

        # Mock diarization engine to simulate DiarizationCollapseError
        mock_engine = MagicMock()
        mock_engine.extract_speaker_turns.side_effect = DiarizationCollapseError(
            "Expected 3 roster speakers but acoustic diarization collapsed to 1 cluster."
        )
        recorder.diarization_engine = mock_engine

        # stop() must NOT raise; it must seal manifest and footer cleanly
        recorder.stop()

        # 1. Manifest exists and is sealed
        self.assertTrue(recorder.session_manifest.exists())
        manifest = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertIn("stopped_at", manifest)

        # 2. Transcript exists and footer is sealed
        self.assertTrue(recorder.transcript_md.exists())
        md_text = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("[SESSION_SEALED]", md_text)

        # 3. Enrichment receipt records failure without crashing
        self.assertTrue(hasattr(recorder, "enrichment_receipt"))
        self.assertEqual(recorder.enrichment_receipt["status"], "FAILED")

    def test_category_d_anti_autonomous_attribution_invariant_preservation(self) -> None:
        """Category D (Anti-Autonomous Speaker Attribution): stop() never mutates live speech turns."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Immutability Verification",
            meeting_date="10/04/2026",
            attendees="Marcus, Amit",
            output_dir=self.tmp_path / "output_immutability",
            mock_mode=True,
        )
        self._recorders.append(recorder)

        recorder.chunks_dir.mkdir(parents=True, exist_ok=True)
        c0 = recorder.chunks_dir / "chunk_00000.wav"
        _create_sine_wav(c0, 16000)
        recorder.process_chunk(c0, 0)

        # Read live transcript before stop()
        pre_stop_content = recorder.transcript_md.read_text(encoding="utf-8")

        recorder.stop()

        # Read after stop()
        post_stop_content = recorder.transcript_md.read_text(encoding="utf-8")

        # The body before the footer append must be IDENTICAL to pre_stop_content
        self.assertTrue(post_stop_content.startswith(pre_stop_content.rstrip()))
        self.assertIn("[SESSION_SEALED]", post_stop_content)


if __name__ == "__main__":
    unittest.main()
