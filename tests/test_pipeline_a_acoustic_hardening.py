"""Unit and integration tests for Wave 1: Pipeline A Acoustic Evidence & Speaker Boundary Hardening.

Verifies:
1. Rejection of ungrounded caller tags; normalization to 'speaker_unknown'.
2. Acoustic quality metrics computation (dBFS, SNR, clipping, speech confidence).
3. Preservation of verified enrolled speaker identities.
4. LocalSTTEngine integration enforcing uncertainty locking.
5. Signal envelope compilation accurately reflecting attribution states.
6. Strict conformance to the 400-line module budget across all Pipeline A intake modules.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from pipeline_a_intake.acoustic_evidence import (
    DEFAULT_SPEAKER_UNKNOWN,
    AcousticSpeakerGate,
    AttributionState,
    compute_acoustic_metrics,
)
from pipeline_a_intake.contracts import AudioChunk
from pipeline_a_intake.local_stt import create_stt_engine
from pipeline_a_intake.text_stitcher import TranscriptStitcher
from storage.database import DatabaseManager


class TestAcousticEvidenceHardening(unittest.TestCase):
    """Verifies acoustic DSP and speaker attribution gate behaviors."""

    def setUp(self) -> None:
        self.gate = AcousticSpeakerGate(enrolled_speakers=["Alice Smith", "Drew Oakley"])

    def test_ungrounded_speaker_normalized_to_speaker_unknown(self) -> None:
        """Placeholders and empty speaker tags must strictly normalize to speaker_unknown."""
        placeholders = ["", "   ", "Speaker", "speaker", "Speaker A", "SPEAKER B", "unknown", "Attendee"]
        for ph in placeholders:
            res = self.gate.evaluate_speaker(ph)
            self.assertEqual(res.speaker_label, DEFAULT_SPEAKER_UNKNOWN)
            self.assertEqual(res.attribution_state, AttributionState.UNATTRIBUTED_UNKNOWN)
            self.assertFalse(res.is_attributed)
            self.assertEqual(res.confidence, 0.0)
            self.assertTrue(res.uncertainty_locked)

    def test_acoustic_quality_metrics_computation(self) -> None:
        """Verifies DSP metrics calculation across synthetic waveforms."""
        # 1. Zero/Empty audio
        empty_metrics = compute_acoustic_metrics(np.zeros(0, dtype=np.float32))
        self.assertFalse(empty_metrics.is_speech)
        self.assertEqual(empty_metrics.clipping_ratio, 0.0)

        # 2. Silence (very low amplitude)
        silence = 1e-4 * np.random.randn(16000).astype(np.float32)
        silence_metrics = compute_acoustic_metrics(silence)
        self.assertLess(silence_metrics.rms_dbfs, -42.0)
        self.assertFalse(silence_metrics.is_speech)

        # 3. Active speech signal (sine wave at moderate level)
        t = np.linspace(0, 1.0, 16000, endpoint=False, dtype=np.float32)
        speech_wave = 0.5 * np.sin(2 * np.pi * 440 * t)
        speech_metrics = compute_acoustic_metrics(speech_wave)
        self.assertTrue(speech_metrics.is_speech)
        self.assertGreater(speech_metrics.speech_confidence, 0.5)
        self.assertEqual(speech_metrics.clipping_ratio, 0.0)

        # 4. Saturated / Clipped signal
        clipped_wave = np.ones(16000, dtype=np.float32)
        clipped_metrics = compute_acoustic_metrics(clipped_wave)
        self.assertAlmostEqual(clipped_metrics.clipping_ratio, 1.0, places=3)
        self.assertAlmostEqual(clipped_metrics.peak_dbfs, 0.0, places=1)

    def test_verified_enrolled_speaker_preserves_attribution(self) -> None:
        """Enrolled roster speakers or cryptographically verified tags retain attribution."""
        # Roster match
        res1 = self.gate.evaluate_speaker("Alice Smith")
        self.assertEqual(res1.speaker_label, "Alice Smith")
        self.assertEqual(res1.attribution_state, AttributionState.VERIFIED_ENROLLED)
        self.assertTrue(res1.is_attributed)
        self.assertGreaterEqual(res1.confidence, 0.90)
        self.assertFalse(res1.uncertainty_locked)

        # Metadata verified enrollment
        res2 = self.gate.evaluate_speaker(
            "Guest VIP",
            metadata={"verified_acoustic_enrollment": True, "acoustic_speaker_confidence": 0.98},
        )
        self.assertEqual(res2.speaker_label, "Guest VIP")
        self.assertEqual(res2.attribution_state, AttributionState.VERIFIED_ENROLLED)
        self.assertTrue(res2.is_attributed)
        self.assertEqual(res2.confidence, 0.98)
        self.assertFalse(res2.uncertainty_locked)

    def test_unverified_named_speaker_marked_cluster_only(self) -> None:
        """Unenrolled custom string preserves name for reference but is unverified (uncertainty locked)."""
        res = self.gate.evaluate_speaker("Unenrolled Stranger")
        self.assertEqual(res.speaker_label, "Unenrolled Stranger")
        self.assertEqual(res.attribution_state, AttributionState.CLUSTER_ONLY)
        self.assertFalse(res.is_attributed)
        self.assertTrue(res.uncertainty_locked)


class TestLocalSTTEngineAcousticGating(unittest.TestCase):
    """Verifies end-to-end LocalSTTEngine acoustic speaker gating and WAL persistence."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir.name,
            allow_unencrypted_dev_fallback=True,
        )
        self.engine = create_stt_engine(
            db_manager=self.db_manager,
            mock_mode=True,
            session_id="meet_wave1_test",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_local_stt_engine_normalizes_ungrounded_speaker_to_speaker_unknown(self) -> None:
        """AudioChunk with speaker='Speaker' or '' must persist as speaker_unknown."""
        chunk = AudioChunk(
            chunk_id="chk_ungrounded",
            meeting_id="meet_wave1_test",
            audio_data=np.zeros(16000, dtype=np.float32),
            speaker="Speaker",
            metadata={"mock_text": "Governed test utterance without speaker proof"},
        )
        deltas = self.engine.feed_audio(chunk)
        self.assertGreater(len(deltas), 0)
        self.assertEqual(deltas[0].speaker, DEFAULT_SPEAKER_UNKNOWN)
        self.assertFalse(deltas[0].is_attributed)

        # Check persisted SQLite evidence
        records = self.db_manager.get_evidence_by_meeting("meet_wave1_test")
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0].speaker, DEFAULT_SPEAKER_UNKNOWN)

    def test_signal_envelope_reflects_uncertainty_state(self) -> None:
        """ForensicSignalEnvelope reflects identity_state='unknown' for unverified speech."""
        chunk = AudioChunk(
            chunk_id="chk_env",
            meeting_id="meet_wave1_test",
            audio_data=np.zeros(16000, dtype=np.float32),
            speaker="Speaker",
            metadata={"mock_text": "Envelope uncertainty verification utterance"},
        )
        self.engine.feed_audio(chunk)
        envelope = self.engine.compile_signal_envelope("meet_wave1_test")
        self.assertGreater(len(envelope.turns), 0)
        turn = envelope.turns[0]
        self.assertEqual(turn.identity_state, "unknown")
        self.assertIsNone(turn.roster_person_id)
        self.assertEqual(turn.speaker_cluster_ids, ())


class TestTextStitcherAcousticContract(unittest.TestCase):
    """Verifies TranscriptStitcher retains is_attributed and confidence fields."""

    def test_text_stitcher_preserves_attribution_contract(self) -> None:
        stitcher = TranscriptStitcher(session_id="sess_stitch_test")
        delta = stitcher.process_window(
            raw_text="Hello world this is a test",
            start_ts=0.0,
            end_ts=1.5,
            is_pause_boundary=True,
            speaker="Alice Smith",
            is_attributed=True,
            speaker_confidence=0.95,
        )
        self.assertEqual(delta.speaker, "Alice Smith")
        self.assertTrue(delta.is_attributed)
        self.assertEqual(delta.speaker_confidence, 0.95)

        # Test ungrounded placeholder passed into stitcher
        delta2 = stitcher.process_window(
            raw_text="Another sentence from unknown voice",
            start_ts=1.5,
            end_ts=3.0,
            is_pause_boundary=True,
            speaker="Speaker",
            is_attributed=False,
            speaker_confidence=0.0,
        )
        self.assertEqual(delta2.speaker, DEFAULT_SPEAKER_UNKNOWN)
        self.assertFalse(delta2.is_attributed)


class TestPipelineAModuleLineBudgets(unittest.TestCase):
    """Verifies that all Pipeline A intake modules strictly conform to the 400-line budget."""

    def test_pipeline_a_modules_strictly_under_400_lines(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        target_files = [
            repo_root / "pipeline_a_intake" / "local_stt.py",
            repo_root / "pipeline_a_intake" / "acoustic_evidence.py",
            repo_root / "pipeline_a_intake" / "text_stitcher.py",
            repo_root / "pipeline_a_intake" / "audio_splicer.py",
            repo_root / "pipeline_a_intake" / "audio_chunker.py",
            repo_root / "pipeline_a_intake" / "local_stt_core" / "envelope.py",
            repo_root / "pipeline_a_intake" / "local_stt_core" / "persistence.py",
        ]
        for fpath in target_files:
            self.assertTrue(fpath.exists(), f"File missing: {fpath}")
            line_count = len(fpath.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                400,
                f"Module {fpath.name} exceeds 400 lines: {line_count} lines found.",
            )


if __name__ == "__main__":
    unittest.main()
