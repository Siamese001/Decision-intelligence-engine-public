"""
tests/test_escalation_cascades_and_hitl_shield.py

Unit test suite verifying Wave 4: Two-Stage Verification, Fallback Cascades & HITL Shield:
- Fast deterministic schema validator (<50ms) enforcing Schema 5.7 and W2 Semantic Spine
- RemediationHealer instant Sol escalation on schema violation
- Two-Stage Ambiguity Shield (suppressing false-alarm operator modals when Sol resolves)
- Two-Stage Ambiguity Shield surfacing HITL when genuine ambiguity is confirmed
- Multi-Generation SRE Fallback Cascade recovering from simulated HTTP 429/503 errors
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from dual_pipeline.manifest_runner.remediation_healer import heal_output_3_with_fast_validation
from dual_pipeline.query_router_core.escalation_shield import (
    execute_with_multigen_fallback,
    resolve_two_stage_ambiguity,
    validate_fast_schema_57,
)
from dual_pipeline.speaker_disambiguation_core.disambiguator import SpeakerDisambiguator
from dual_pipeline.speaker_disambiguation_core.models import AttendeeContext, RoomMicConfig


class TestEscalationCascadesAndHITLShield(unittest.TestCase):
    """Test suite for Wave 4 escalation cascades and ambiguity shield."""

    def setUp(self) -> None:
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "FAST_ROUTING_MODEL": "",
                "FAST_ROUTING_REASONING_EFFORT": "",
                "COPILOT_MODEL": "",
                "COPILOT_REASONING_EFFORT": "",
                "QUERY_REASONING_MODEL": "",
                "QUERY_REASONING_REASONING_EFFORT": "",
                "OPENAI_MODEL": "",
                "OPENAI_REASONING_EFFORT": "",
            },
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_fast_schema_validation_sub_50ms_and_w2_enforcement(self) -> None:
        """Verifies fast schema validation completes in <50ms and enforces W2 entity prefixes."""
        # 1. Valid Output 03 payload
        valid_payload = {
            "project": "project_nexus",
            "entities": [
                {"id": "person:jen", "type": "person", "canonical_name": "Jen"},
                {"id": "org:partner_advisory", "type": "organization", "canonical_name": "PartnerAdvisory"},
                {"id": "workstream:lens", "type": "workstream", "canonical_name": "LENS"},
            ],
            "relationships": [
                {"source": "person:jen", "target": "org:partner_advisory", "predicate": "AFFILIATED_WITH"},
            ],
        }
        res_valid = validate_fast_schema_57(valid_payload)
        self.assertTrue(res_valid.is_valid)
        self.assertEqual(len(res_valid.errors), 0)
        self.assertLess(res_valid.elapsed_ms, 50.0)

        # 2. Invalid entity prefix (violation of W2 Semantic Spine)
        invalid_prefix_payload = {
            "project": "project_nexus",
            "entities": [
                {"id": "user:jen", "type": "person", "canonical_name": "Jen"},
                {"id": "external_vendor:partner_advisory", "type": "org", "canonical_name": "PartnerAdvisory"},
            ],
            "relationships": [],
        }
        res_invalid = validate_fast_schema_57(invalid_prefix_payload)
        self.assertFalse(res_invalid.is_valid)
        self.assertTrue(any("W2_PREFIX_VIOLATION" in err for err in res_invalid.errors))
        self.assertLess(res_invalid.elapsed_ms, 50.0)

        # 3. Missing required top-level keys
        missing_keys_payload = {"notes": "Draft only"}
        res_missing = validate_fast_schema_57(missing_keys_payload)
        self.assertFalse(res_missing.is_valid)
        self.assertTrue(any("MISSING_REQUIRED_KEY" in err for err in res_missing.errors))

    def test_remediation_healer_instant_sol_escalation(self) -> None:
        """Verifies heal_output_3_with_fast_validation escalates to Sol upon schema failure."""
        invalid_f3 = '{"notes": "Corrupted non-conforming json without required fields"}'
        result_dict, was_escalated, msg = heal_output_3_with_fast_validation(
            f3_text=invalid_f3,
            f1_text="",
            model=None,
            client=None,
        )
        self.assertTrue(was_escalated)
        self.assertIn("Escalated to", msg)

    def test_two_stage_ambiguity_shield_suppresses_false_alarm_hitl(self) -> None:
        """
        Verifies that when primary model has narrow margin (Delta=0.08 < 0.15)
        but secondary Sol 6.1 resolves with decisive margin (Delta=0.25 >= 0.15),
        operator HITL modal is suppressed (hitl_required=False).
        """
        def mock_sol_evaluator():
            # Sol resolves decisively for Alex with 0.25 margin
            return "Alex", 0.75, 0.25

        res = resolve_two_stage_ambiguity(
            candidate_name_1="Alex",
            confidence_1=0.54,
            candidate_name_2="Chris",
            confidence_2=0.46,
            secondary_evaluator_fn=mock_sol_evaluator,
            threshold=0.15,
        )
        self.assertFalse(res.hitl_required)
        self.assertEqual(res.resolved_by, "SOL_SHIELD_AUTONOMOUS")
        self.assertEqual(res.final_assigned, "Alex")
        self.assertAlmostEqual(res.final_margin, 0.25, places=2)
        self.assertIn("Two-Stage Shield: Sol 6.1 resolved", res.audit_notes)

    def test_two_stage_ambiguity_shield_requires_hitl_when_both_ambiguous(self) -> None:
        """
        Verifies that when BOTH primary model and Sol 6.1 confirm epistemic ambiguity
        (< 15%), an interactive HITL modal is surfaced.
        """
        def mock_sol_still_ambiguous():
            # Sol also cannot decisively distinguish Alex vs Chris (margin 0.08)
            return "Alex", 0.52, 0.08

        res = resolve_two_stage_ambiguity(
            candidate_name_1="Alex",
            confidence_1=0.51,
            candidate_name_2="Chris",
            confidence_2=0.47,
            secondary_evaluator_fn=mock_sol_still_ambiguous,
            threshold=0.15,
        )
        self.assertTrue(res.hitl_required)
        self.assertEqual(res.resolved_by, "HITL_REQUIRED")
        self.assertAlmostEqual(res.final_margin, 0.08, places=2)
        self.assertIn("confirmed genuine epistemic ambiguity", res.audit_notes)

    def test_multigen_sre_fallback_cascade_on_429(self) -> None:
        """
        Simulates HTTP 429 quota exhaustion on GPT-6 Luna, HTTP 503 on Sol 6.1,
        and verifies clean recovery on SRE fallback (GPT-5.6 Terra/Sol) without data loss.
        """
        class MockHTTPError(Exception):
            def __init__(self, code: int, msg: str):
                super().__init__(msg)
                self.code = code

        def call_endpoint(model: str) -> str:
            if "luna" in model:
                raise MockHTTPError(429, "Rate limit exceeded (TPM quota reached)")
            if "6.1" in model or "sol" in model:
                # First sol fails with 503
                raise MockHTTPError(503, "Service temporarily unavailable")
            if "terra" in model:
                return f"Success response from {model}"
            return f"Success from {model}"

        custom_chain = ["gpt-6-luna", "gpt-6.1-sol", "gpt-5.6-terra", "gpt-6-luna"]
        res, successful_model, log = execute_with_multigen_fallback(
            call_fn=call_endpoint,
            primary_model="gpt-6-luna",
            fallback_chain=custom_chain,
            max_retries_per_tier=1,
        )

        self.assertEqual(successful_model, "gpt-5.6-terra")
        self.assertIn("gpt-5.6-terra", res)
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]["model"], "gpt-6-luna")
        self.assertEqual(log[0]["status"], "FAILED")
        self.assertEqual(log[1]["model"], "gpt-6.1-sol")
        self.assertEqual(log[1]["status"], "FAILED")
        self.assertEqual(log[2]["model"], "gpt-5.6-terra")
        self.assertEqual(log[2]["status"], "SUCCESS")

    def test_speaker_disambiguator_two_stage_shield_integration(self) -> None:
        """Verifies SpeakerDisambiguator.disambiguate_with_two_stage_shield executes cleanly."""
        room_cfg = RoomMicConfig(
            mic_label="mic_1",
            display_label="Room Mic A",
            in_room_attendees=[
                AttendeeContext(
                    name="Jen",
                    person_id="person:jen",
                    role="Architect",
                    company="PartnerAdvisory",
                    domain_topics=["architecture", "blueprint"],
                ),
                AttendeeContext(
                    name="Amit",
                    person_id="person:amit",
                    role="Tech Lead",
                    company="NEA",
                    domain_topics=["opentelemetry", "plumtree"],
                ),
            ],
        )
        disambiguator = SpeakerDisambiguator(mock=True)
        transcript = (
            "Speaker 1: Welcome everyone.\n"
            "Speaker 2: We need to discuss the architecture blueprint and opentelemetry plumtree integration.\n"
        )
        result = disambiguator.disambiguate_with_two_stage_shield(
            transcript=transcript,
            room_config=room_cfg,
        )
        self.assertIsNotNone(result.resolved_transcript)
        self.assertIn("two_stage_shield", result.attribution_manifest["telemetry"])


if __name__ == "__main__":
    unittest.main()
