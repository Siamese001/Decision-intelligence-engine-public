#!/usr/bin/env python3
"""tests/unit/test_acoustic_collapse_preservation.py

Verifies Wave 1 remediation:
1. mlx_transcriber preserves Pyannote acoustic cluster labels during DiarizationCollapseError.
2. u0_ingestion disqualifies allow_undiarized for audio or multi-attendee meetings.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.exceptions import DiarizationCollapseError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class TestAcousticCollapsePreservation(unittest.TestCase):
    """Unit tests for acoustic cluster preservation and ingress disqualification."""

    def test_mlx_transcriber_preserves_clusters_on_collapse_error(self) -> None:
        """When DiarizationCollapseError is caught, acoustic segments retain their cluster IDs."""
        transcriber = LocalMLXAudioTranscriber()
        mock_engine = MagicMock()
        mock_diarization = MagicMock()
        mock_engine.diarize.return_value = mock_diarization
        transcriber.diarization_engine = mock_engine

        sanitized_segments = [
            {"start": 0.0, "end": 2.0, "text": "Turn 1"},
            {"start": 2.1, "end": 4.0, "text": "Turn 2"},
        ]
        aligned_mock = [
            {"start": 0.0, "end": 2.0, "text": "Turn 1", "speaker": "SPEAKER_00"},
            {"start": 2.1, "end": 4.0, "text": "Turn 2", "speaker": "SPEAKER_01"},
        ]
        metadata = {
            "attendees": ["Bryce Poeter", "Amit Ayer"],
            "meeting_name": "Test Skew Meeting",
        }

        collapse_err = DiarizationCollapseError("87.3% skew")
        collapse_err.evidence = {"max_speaker_pct": 0.873, "threshold_pct": 0.85}

        with patch("dual_pipeline.audio_ingestion_core.diarization_engine.validate_acoustic_diarization", side_effect=collapse_err):
            with patch("dual_pipeline.audio_ingestion_core.mlx_transcriber._align_segments_with_diarization", return_value=aligned_mock):
                res = transcriber._diarize_and_align(sanitized_segments, "dummy.wav", metadata)

        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["speaker"], "SPEAKER_00")
        self.assertEqual(res[1]["speaker"], "SPEAKER_01")
        self.assertEqual(metadata.get("diarization_status"), "HITL_REQUIRED")
        self.assertTrue(metadata.get("single_speaker_collapse"))
        self.assertIn("collapse_evidence", metadata)

    def test_validate_acoustic_diarization_attaches_evidence_on_skew(self) -> None:
        """validate_acoustic_diarization attaches structured evidence when raising DiarizationCollapseError."""
        from dual_pipeline.audio_ingestion_core.diarization_engine import validate_acoustic_diarization

        mock_diar = MagicMock()
        class MockTurn:
            def __init__(self, start: float, end: float):
                self.start = start
                self.end = end
        tracks = [
            (MockTurn(0.0, 90.0), None, "SPEAKER_00"),
            (MockTurn(90.0, 100.0), None, "SPEAKER_01"),
        ]
        mock_diar.itertracks.return_value = tracks

        with self.assertRaises(DiarizationCollapseError) as ctx:
            validate_acoustic_diarization(mock_diar, expected_roster_count=2, max_single_speaker_collapse_pct=0.85)

        self.assertIn("Acoustic Pre-Synthesis Skew Gate", str(ctx.exception))
        evidence = getattr(ctx.exception, "evidence", {})
        self.assertEqual(evidence.get("max_speaker_pct"), 0.90)
        self.assertEqual(evidence.get("threshold_pct"), 0.85)
        self.assertEqual(evidence.get("cluster_count"), 2)

    def test_u0_ingestion_disqualifies_undiarized_for_audio_source(self) -> None:
        """u0_ingestion strictly rejects allow_undiarized if source_type is AUDIO."""
        meta = {
            "allow_undiarized": True,
            "source_type": "AUDIO",
        }
        transcript = "**Speaker** [00:01]: Hello\n**Speaker** [00:05]: Hi"
        with self.assertRaises(ValueError) as ctx:
            U0IngestionBoundary._check_generic_speakers(transcript, meta)
        self.assertIn("UNDIARIZED_DISQUALIFIED_FOR_AUDIO", str(ctx.exception))

    def test_u0_ingestion_disqualifies_undiarized_for_multi_attendee_roster(self) -> None:
        """u0_ingestion strictly rejects allow_undiarized if attendees >= 2."""
        meta = {
            "allow_undiarized": True,
            "attendees": ["Bryce Poeter", "Amit Ayer"],
        }
        transcript = "**Speaker** [00:01]: Hello\n**Speaker** [00:05]: Hi"
        with self.assertRaises(ValueError) as ctx:
            U0IngestionBoundary._check_generic_speakers(transcript, meta)
        self.assertIn("UNDIARIZED_DISQUALIFIED_FOR_AUDIO", str(ctx.exception))

    def test_u0_ingestion_allows_undiarized_for_single_attendee_notes(self) -> None:
        """u0_ingestion permits allow_undiarized for single-attendee non-audio text notes."""
        meta = {
            "allow_undiarized": True,
            "source_type": "TEXT_DOCUMENT",
            "attendees": ["Solo Author"],
        }
        transcript = "**Speaker** [00:01]: Monologue note"
        result = U0IngestionBoundary._check_generic_speakers(transcript, meta)
        self.assertEqual(result, "UNDIARIZED")


if __name__ == "__main__":
    unittest.main()
