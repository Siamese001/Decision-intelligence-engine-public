"""
tests/end_to_end/test_hint_reconciliation_and_scoring_e2e.py

Class B E2E Test Suite for Wave 2:
Live-to-Batch Hint Reconciliation & Attribution Scoring.

Verifies:
1. Operator quote hints in speaker_hints.json boost candidate attribution score (>30% margin).
2. Self-introductions (SELF_ATTRIBUTION_PATTERNS) boost confidence to HIGH tier (>=0.85) autonomously.
3. Addressee handoff cues boost candidate scores for the immediately responding speaker.
4. Silent invitees on attendee roster do not trigger DiarizationCollapseError or single-speaker collapse.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationEngine
from dual_pipeline.speaker_attribution_core import (
    AttributionResult,
    AudioSpeakerAttributionEngine,
    CandidateAttribution,
    score_candidates_for_speaker,
)


class TestHintReconciliationAndScoringE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_wave2_e2e_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_quote_hint_reconciliation_boosts_margin_autonomously(self):
        """Operator quote hints in speaker_hints.json boost candidate scores with >30% margin."""
        hints_file = self.temp_path / "speaker_hints.json"
        hints_data = [
            {
                "quote": "we must deliver the kubernetes cluster deployment by friday",
                "speaker_name": "Alice Chen",
                "voice_id": "SPEAKER_00",
            }
        ]
        hints_file.write_text(json.dumps(hints_data, indent=2), encoding="utf-8")

        transcript = (
            "Speaker 0: We must deliver the Kubernetes cluster deployment by Friday without exception.\n"
            "Speaker 1: I agree with that schedule."
        )

        metadata = {
            "speaker_hints_path": str(hints_file),
            "roster": [
                {"name": "Alice Chen", "org": "PartnerAdvisory", "role": "Lead Architect"},
                {"name": "Bob Martin", "org": "Client", "role": "Product Owner"},
            ],
            "project": "project_test",
            "meeting_name": "Sprint Sync",
            "date": "10-14-2026",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
        }

        engine = AudioSpeakerAttributionEngine()
        result = engine.analyze_and_resolve(
            transcript=transcript,
            metadata=metadata,
            autonomous_if_decisive=True,
        )

        self.assertIsInstance(result, AttributionResult)
        self.assertEqual(result.speaker_map.get("Speaker 0"), "Alice Chen")
        # Injective deduction resolves remaining speaker
        self.assertEqual(result.speaker_map.get("Speaker 1"), "Bob Martin")
        self.assertIn("Alice Chen:", result.resolved_transcript)
        self.assertIn("Bob Martin:", result.resolved_transcript)

    def test_self_attribution_pattern_scoring_and_autonomous_gate(self):
        """Self-introductions boost candidate attribution to >=0.85 with >30% decisive margin."""
        transcript = (
            "Speaker 0: Hello team, this is Alice Chen speaking. Looking at the enterprise architecture...\n"
            "Speaker 1: Thanks Alice, looks good."
        )

        metadata = {
            "roster": [
                {"name": "Alice Chen", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Bob Martin", "org": "Client", "role": "Delivery Lead"},
            ],
            "project": "project_test",
            "meeting_name": "Architecture Review",
            "date": "10-14-2026",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
        }

        candidates = score_candidates_for_speaker(
            spk_id="0",
            transcript=transcript,
            roster=[("Alice Chen", "PartnerAdvisory"), ("Bob Martin", "Client")],
            metadata=metadata,
            total_clusters=2,
        )

        self.assertGreaterEqual(len(candidates), 2)
        top = candidates[0]
        second = candidates[1]

        self.assertEqual(top.attendee_name, "Alice Chen")
        self.assertGreaterEqual(top.confidence, 0.85)
        self.assertEqual(top.confidence_tier, "HIGH")
        # Margin strictly exceeds 30%
        margin = top.confidence - second.confidence
        self.assertGreater(margin, 0.30)
        self.assertTrue(top.is_recommended)
        self.assertIn("Self-attribution cue", top.evidence_rationale)

    def test_addressee_handoff_and_dialogue_cues(self):
        """Preceding addressee handoff boosts candidate score for the answering speaker."""
        transcript = (
            "Speaker 0: Over to you Bob Martin, can you walk us through the sprint commitments?\n"
            "Speaker 1: Absolutely, we are on track to complete all sprint tickets."
        )

        metadata = {
            "roster": [
                {"name": "Alice Chen", "org": "PartnerAdvisory", "role": "Scrum Master"},
                {"name": "Bob Martin", "org": "Client", "role": "Product Owner"},
            ],
            "project": "project_test",
            "meeting_name": "Sprint Standup",
            "date": "10-14-2026",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
        }

        candidates_spk1 = score_candidates_for_speaker(
            spk_id="1",
            transcript=transcript,
            roster=[("Alice Chen", "PartnerAdvisory"), ("Bob Martin", "Client")],
            metadata=metadata,
            total_clusters=2,
        )

        self.assertEqual(candidates_spk1[0].attendee_name, "Bob Martin")
        self.assertGreaterEqual(candidates_spk1[0].confidence, 0.85)
        self.assertIn("Addressee handoff cue", candidates_spk1[0].evidence_rationale)

    def test_silent_attendee_on_roster_does_not_trigger_collapse(self):
        """Silent invitee on roster does not raise DiarizationCollapseError when multi-speaker audio exists."""
        rttm_lines = [
            "SPEAKER audio 1 0.00 5.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 5.50 5.00 <NA> <NA> SPEAKER_01 <NA> <NA>",
            "SPEAKER audio 1 11.00 4.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 15.50 4.50 <NA> <NA> SPEAKER_01 <NA> <NA>",
        ]

        engine = DiarizationEngine(lazy=True)
        # Roster has 4 participants (Alice, Bob, Charlie, Dana), but only 2 spoke
        turns = engine.parse_rttm_turns(
            rttm_lines=rttm_lines,
            source_name="meeting_audio.wav",
            expected_roster_count=4,
            max_single_speaker_collapse_pct=0.85,
        )

        self.assertGreater(len(turns), 0)
        speakers = set(t["speaker"] for t in turns)
        self.assertEqual(len(speakers), 2)


if __name__ == "__main__":
    unittest.main()
