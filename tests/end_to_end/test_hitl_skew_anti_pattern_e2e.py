#!/usr/bin/env python3
"""tests/end_to_end/test_hitl_skew_anti_pattern_e2e.py

End-to-End incident replay and qualification suite for Wave 4:
Anti-Pattern Elimination for Undiarized Ingestion.

Replays the 10/12/2026 Bryce Poeter (87.3%) vs Amit Ayer (12.7%) incident:
1. Simulates acoustic diarization detecting severe single-speaker skew.
2. Asserts validate_acoustic_diarization raises DiarizationCollapseError with structured evidence.
3. Asserts mlx_transcriber preserves Pyannote acoustic clusters (e.g. SPEAKER_00, SPEAKER_01)
   and attaches single_speaker_collapse metadata without collapsing speaker labels to generic "Speaker".
4. Asserts u0_ingestion strictly disqualifies any attempt to pass allow_undiarized on this audio meeting.
5. Asserts question_validator blocks adversarial injection of "Ingest as undiarized session".
6. Verifies compliant governance options (Approve skew tolerance vs Halt) compile cleanly with QuestionBuilder.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.audio_ingestion_core.diarization_engine import validate_acoustic_diarization
from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.exceptions import DiarizationCollapseError
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.hitl_governance_core.question_validator import (
    QuestionPayloadValidationError,
    validate_question_payload,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class MockTurn:
    """Mock timeline turn representing an audio segment."""

    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class TestHitlSkewAntiPatternE2E(unittest.TestCase):
    """E2E lifecycle test verifying cluster preservation and anti-pattern firewall."""

    def setUp(self) -> None:
        self.meeting_title = "Bryce Poeter / Amit Ayer Sync"
        self.attendees = ["Bryce Poeter", "Amit Ayer"]

    def test_e2e_incident_replay_lifecycle(self) -> None:
        """Full lifecycle: acoustic skew -> cluster preservation -> ingress gate -> HITL firewall."""

        # ---------------------------------------------------------------------
        # Step 1: Diarization Engine detects 87.3% skew and attaches evidence
        # ---------------------------------------------------------------------
        mock_diar = MagicMock()
        # Bryce (SPEAKER_00) talks 873s, Amit (SPEAKER_01) talks 127s -> 87.3%
        tracks = [
            (MockTurn(0.0, 873.0), None, "SPEAKER_00"),
            (MockTurn(873.0, 1000.0), None, "SPEAKER_01"),
        ]
        mock_diar.itertracks.return_value = tracks

        with self.assertRaises(DiarizationCollapseError) as ctx:
            validate_acoustic_diarization(
                mock_diar,
                expected_roster_count=2,
                max_single_speaker_collapse_pct=0.85,
            )

        collapse_err = ctx.exception
        self.assertIn("Acoustic Pre-Synthesis Skew Gate", str(collapse_err))
        self.assertIsNotNone(collapse_err.evidence, "Structured evidence must be attached to exception")
        self.assertAlmostEqual(collapse_err.evidence["max_speaker_pct"], 0.873, places=2)
        self.assertEqual(collapse_err.evidence["cluster_count"], 2)

        # ---------------------------------------------------------------------
        # Step 2: MLXTranscriber preserves Pyannote clusters upon skew exception
        # ---------------------------------------------------------------------
        transcriber = LocalMLXAudioTranscriber()
        mock_engine = MagicMock()
        mock_engine.diarize.return_value = mock_diar
        transcriber.diarization_engine = mock_engine

        raw_segments = [
            {"start": 0.0, "end": 873.0, "text": "Bryce speaking on system architecture and design."},
            {"start": 873.0, "end": 1000.0, "text": "Amit confirming requirements and action items."},
        ]
        aligned_mock = [
            {"start": 0.0, "end": 873.0, "text": "Bryce speaking on system architecture and design.", "speaker": "SPEAKER_00"},
            {"start": 873.0, "end": 1000.0, "text": "Amit confirming requirements and action items.", "speaker": "SPEAKER_01"},
        ]
        metadata = {
            "source_type": "AUDIO",
            "attendees": self.attendees,
            "meeting_name": self.meeting_title,
        }

        with patch("dual_pipeline.audio_ingestion_core.diarization_engine.validate_acoustic_diarization", side_effect=collapse_err):
            with patch("dual_pipeline.audio_ingestion_core.mlx_transcriber._align_segments_with_diarization", return_value=aligned_mock):
                processed_segments = transcriber._diarize_and_align(
                    raw_segments,
                    "incident_recording.wav",
                    metadata,
                )

        # Verify cluster preservation
        self.assertEqual(len(processed_segments), 2)
        self.assertEqual(processed_segments[0]["speaker"], "SPEAKER_00")
        self.assertEqual(processed_segments[1]["speaker"], "SPEAKER_01")
        self.assertNotEqual(processed_segments[0]["speaker"], "Speaker")
        self.assertEqual(metadata.get("diarization_status"), "HITL_REQUIRED")
        self.assertTrue(metadata.get("single_speaker_collapse"))
        self.assertIn("collapse_evidence", metadata)
        self.assertEqual(metadata["collapse_evidence"]["cluster_count"], 2)

        # ---------------------------------------------------------------------
        # Step 3: Ingress Firewall strictly blocks allow_undiarized for this audio
        # ---------------------------------------------------------------------
        unmapped_meta = {
            "source_type": "AUDIO",
            "attendees": self.attendees,
            "allow_undiarized": True,
        }
        with self.assertRaises(ValueError) as val_ctx:
            U0IngestionBoundary._check_generic_speakers(
                transcript="SPEAKER_00: Bryce speaking.\nSPEAKER_01: Amit speaking.",
                metadata=unmapped_meta,
            )
        self.assertIn("UNDIARIZED_DISQUALIFIED_FOR_AUDIO", str(val_ctx.exception))

        # ---------------------------------------------------------------------
        # Step 4: Adversarial attempt to present Option 2 (Undiarized) fails closed
        # ---------------------------------------------------------------------
        adversarial_options = [
            "(Recommended) Approve speaker skew tolerance (Confidence: HIGH (92%)). Valid skew.",
            "Ingest as undiarized session (Confidence: MEDIUM (65%)). Ingest transcript without individual speaker attribution.",
            "Halt ingestion (Confidence: LOW (10%)). Abort ingestion pipeline.",
        ]
        val_res = validate_question_payload(
            question="How should the pipeline resolve this speaker attribution and skew?",
            options=adversarial_options,
        )
        self.assertFalse(val_res.is_valid, "Adversarial undiarized option must be rejected")
        self.assertTrue(any("ANTI_PATTERN_VIOLATION" in e for e in val_res.errors))

        # Ensure QuestionBuilder also refuses to build this payload
        builder_adv = QuestionBuilder(question="How should the pipeline resolve this speaker attribution and skew?")
        builder_adv.add_option(
            label="Approve speaker skew tolerance",
            plain_english_summary="Tolerate authentic speaker skew",
            impact_statement="Enables verified attribution and preserves dominant presenter context.",
            confidence_score=0.92,
            recommended=True,
        )
        builder_adv.add_option(
            label="Ingest as undiarized session",
            plain_english_summary="Ingest transcript without individual speaker attribution",
            impact_statement="Removes speaker attribution and creates risk of data loss.",
            confidence_score=0.65,
        )
        with self.assertRaises(QuestionPayloadValidationError) as q_err:
            builder_adv.build()
        self.assertIn("ANTI_PATTERN_VIOLATION", str(q_err.exception))

        # ---------------------------------------------------------------------
        # Step 5: Legitimate governance choices compile cleanly
        # ---------------------------------------------------------------------
        builder_legit = QuestionBuilder(
            question="How should the pipeline resolve this speaker attribution and skew?"
        )
        builder_legit.add_option(
            label="Approve speaker skew tolerance",
            plain_english_summary="Tolerate authentic speaker skew with known roster",
            impact_statement="Enables verified attribution and preserves dominant presenter context.",
            confidence_score=0.92,
            recommended=True,
        )
        builder_legit.add_option(
            label="Halt ingestion for acoustic review",
            plain_english_summary="Pause pipeline execution for manual verification",
            impact_statement="Halts ingestion to prevent data corruption and preserves raw audio.",
            confidence_score=0.15,
        )
        payload = builder_legit.build()
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload.options), 2)
        self.assertIn("Approve speaker skew tolerance", payload.options[0]["label"])
        self.assertIn("Halt ingestion", payload.options[1]["label"])


if __name__ == "__main__":
    unittest.main()
