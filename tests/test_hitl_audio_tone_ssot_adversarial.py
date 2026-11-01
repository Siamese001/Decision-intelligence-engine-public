"""
tests/test_hitl_audio_tone_ssot_adversarial.py

Adversarial Verification Suite for HITL, Audio Governance, Tone Arc, and GraphDB SSOT:
1. Invariant Bypass Attempts: Unauthorized Diarization Toggle Off (rejects bogus/forged reason codes, empty tokens).
2. Boundary Condition Gating: Exact 15% Ambiguity Margin Boundary (14.99% triggers HITL, 15.00% resolves autonomously).
3. Cross-Output Role Tampering & SSOT Divergence: Conflicting roles fail closed via StakeholderInconsistencyError.
4. Malformed/Boundary Audio & Tone Telemetry: Extreme bursts, zero duration, empty turns handled resiliently without crash.
5. Attendee Confirmation Bypass Resistance: Invalid/spoofed confirmation receipts fail closed.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
from dual_pipeline.tone_analysis_core import (
    ParalinguisticToneAnalyzer,
)
from dual_pipeline.transcript_pipeline_core.stakeholder_validator import (
    StakeholderInconsistencyError,
    StakeholderResolutionValidator,
    sync_stakeholder_to_graph,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.hitl_guard import (
    DiarizationDefenseReason,
    validate_diarization_toggle,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from storage.connection import create_sqlite_conn


class TestHITLAudioToneSSOTAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.db_path = self.work_dir / "adversarial_graph.db"

        # Create mock GraphDB schema
        conn = create_sqlite_conn(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                display_name TEXT,
                description TEXT,
                status TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

        # Seed authoritative SSOT stakeholder
        sync_stakeholder_to_graph(
            person_name="Colleen Barbato",
            role="Managing Director",
            org="PartnerAdvisory",
            db_path=self.db_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_diarization_bypass_attempts(self) -> None:
        """Attempts to bypass speaker diarization toggle via spoofed reason codes or missing justifications."""
        # 1. Missing reason code
        with pytest.raises(ValueError, match="Speaker diarization is enforced ON by default"):
            validate_diarization_toggle({"speaker_diarization": False})

        with pytest.raises(ValueError, match="Speaker diarization is enforced ON by default"):
            validate_diarization_toggle({"diarize": False})

        with pytest.raises(ValueError, match="Speaker diarization is enforced ON by default"):
            validate_diarization_toggle({"diarization": "off"})

        # 2. Bogus / unmanaged reason codes
        bogus_reasons = ["too_expensive", "lazy_mode", "FAST_MODE", "unspecified", ""]
        for bogus in bogus_reasons:
            with pytest.raises(ValueError, match=r"(?:Invalid diarization defense reason|Speaker diarization is enforced ON by default)"):
                validate_diarization_toggle({
                    "speaker_diarization": False,
                    "diarization_off_reason": bogus,
                })

        # 3. Legitimate reason code must succeed
        valid_meta = {
            "speaker_diarization": False,
            "diarization_off_reason": DiarizationDefenseReason.SINGLE_SPEAKER_CONFIRMED.value,
        }
        res = validate_diarization_toggle(valid_meta)
        self.assertFalse(res["speaker_diarization"])
        self.assertEqual(res["diarization_off_reason"], "SINGLE_SPEAKER_CONFIRMED")

    def test_adversarial_15pct_ambiguity_margin_boundary(self) -> None:
        """Proves exact 15% ambiguity threshold boundary behavior (14.99% triggers HITL, 15.00% resolves)."""
        # Case A: Delta = 0.1499 (< 0.15) -> Surfacing REQUIRED
        options_sub_threshold = [
            {"option_id": "opt1", "label": "Option A", "confidence": 0.6499},
            {"option_id": "opt2", "label": "Option B", "confidence": 0.5000},
        ]
        res_sub = MarginEvaluator.evaluate(options_sub_threshold)
        self.assertTrue(res_sub["surface_hitl"])
        self.assertFalse(res_sub["decision_resolved_autonomously"])
        self.assertAlmostEqual(res_sub["margin"], 0.1499, places=4)

        # Case B: Delta = 0.1500 (== 0.15) -> Autonomous resolution, NO surfacing
        options_at_threshold = [
            {"option_id": "opt1", "label": "Option A", "confidence": 0.6500},
            {"option_id": "opt2", "label": "Option B", "confidence": 0.5000},
        ]
        res_at = MarginEvaluator.evaluate(options_at_threshold)
        self.assertFalse(res_at["surface_hitl"])
        self.assertTrue(res_at["decision_resolved_autonomously"])
        self.assertAlmostEqual(res_at["margin"], 0.1500, places=4)

        # Case C: Delta = 0.1501 (> 0.15) -> Autonomous resolution, NO surfacing
        options_above_threshold = [
            {"option_id": "opt1", "label": "Option A", "confidence": 0.6501},
            {"option_id": "opt2", "label": "Option B", "confidence": 0.5000},
        ]
        res_above = MarginEvaluator.evaluate(options_above_threshold)
        self.assertFalse(res_above["surface_hitl"])
        self.assertTrue(res_above["decision_resolved_autonomously"])
        self.assertAlmostEqual(res_above["margin"], 0.1501, places=4)

    def test_adversarial_stakeholder_role_conflict_fail_closed(self) -> None:
        """Simulates cross-output role inconsistency attack and proves fail-closed defense."""
        validator = StakeholderResolutionValidator()

        # Output 2 claims Colleen is "Managing Director", but Output 3 claims "Junior Analyst"
        o1_text = "| Colleen Barbato | PartnerAdvisory | Managing Director |"
        o2_text = "### Attendees\n- **Colleen Barbato** (Managing Director)"
        o3_tampered = {
            "entities": [
                {"canonical_name": "Colleen Barbato", "role": "Junior Analyst"}
            ]
        }

        with pytest.raises(StakeholderInconsistencyError) as exc_info:
            validator.validate_deliverables(
                output_1_text=o1_text,
                output_2_text=o2_text,
                output_3_payload=o3_tampered,
                fail_closed=True,
            )

        err_msg = str(exc_info.value)
        self.assertIn("Master Invariant 6 Violation", err_msg)
        self.assertIn("Colleen Barbato", err_msg)
        self.assertIn("conflicting roles across deliverables", err_msg)

    def test_adversarial_malformed_and_boundary_audio_telemetry(self) -> None:
        """Injects corrupted, inverted, and extreme acoustic/lexical telemetry into tone analyzer."""
        analyzer = ParalinguisticToneAnalyzer()

        # Extreme / boundary turn data:
        # Turn 0: Zero duration with 50 words (division by zero / infinite wps stress)
        # Turn 1: Negative timestamps
        # Turn 2: Empty text
        # Turn 3: 1000-character wall of text with repeated markers
        corrupted_turns = [
            {"speaker": "Spk1", "text": "word " * 50, "start": 10.0, "end": 10.0},
            {"speaker": "Spk2", "text": "normal sentence", "start": -5.0, "end": -1.0},
            {"speaker": "Spk3", "text": "", "start": 15.0, "end": 20.0},
            {"speaker": "Spk4", "text": "approved agreed decided " * 40, "start": 25.0, "end": 45.0},
        ]

        # Engine must not raise ZeroDivisionError or unhandled exception
        arc = analyzer.analyze_turns(corrupted_turns)
        self.assertIsNotNone(arc)
        self.assertTrue(0.0 <= arc.engagement_score <= 1.0)
        self.assertGreaterEqual(arc.decisiveness_count, 1)

        # Deployed formatting must survive corrupted turns
        formatted_recap = analyzer.format_output_2_tone_arc(arc)
        self.assertIn("## Meeting Tone Arc & Strategic Dynamics", formatted_recap)

        signals = analyzer.format_output_3_tone_signals(arc)
        self.assertIsInstance(signals, list)

    def test_adversarial_unconfirmed_attendees_bypass_resistance(self) -> None:
        """Verifies that spoofed attendee confirmation receipts fail closed."""
        # Spoofed flag: string 'true' instead of boolean True
        spoofed_meta_str = {
            "_from_audio_transcription": True,
            "meeting_name": "Budget_Review",
            "attendees_confirmed_by_operator": "true",  # Truthy in python, but not `is True`
            "roster": ["Colleen Barbato"],
        }
        with pytest.raises(HITLRequiredError):
            verify_mandatory_attendee_confirmation(spoofed_meta_str)

        # Spoofed wrong key: attendees_confirmed instead of attendees_confirmed_by_operator
        spoofed_wrong_key = {
            "_from_audio_transcription": True,
            "meeting_name": "Budget_Review",
            "attendees_confirmed": True,
            "roster": ["Colleen Barbato"],
        }
        with pytest.raises(HITLRequiredError):
            verify_mandatory_attendee_confirmation(spoofed_wrong_key)


if __name__ == "__main__":
    unittest.main()
