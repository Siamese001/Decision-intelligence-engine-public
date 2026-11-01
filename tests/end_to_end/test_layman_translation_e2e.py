"""
tests/end_to_end/test_layman_translation_e2e.py

End-to-End Governance Test Suite for Layman Translation, Zero-Jargon Invariant,
and hitl-option.schema.json v1.1.0 compliance.
Exercises the full lifecycle from option creation, through margin evaluation and
interactive question payload formatting, to schema validation and presentation linting.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
from dual_pipeline.hitl_governance_core.layman_clarity import (
    build_layman_option,
    contains_jargon,
    format_layman_display_label,
    scan_for_jargon,
)
from dual_pipeline.hitl_governance_core.surfacing import (
    evaluate_hitl_surfacing_gate,
    validate_hitl_option,
)
from dual_pipeline.hitl_governance_core.validation import (
    validate_hitl_presentation,
)


class LaymanTranslationE2ETests(unittest.TestCase):
    """End-to-end test cases verifying the Layman Translation Standard."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        assert cls.schema_path.exists(), "hitl-option.schema.json must exist"
        cls.schema = json.loads(cls.schema_path.read_text(encoding="utf-8"))

    def test_e2e_layman_option_creation_and_schema_validation(self) -> None:
        """Verify building a layman option produces a contract-valid v1.1.0 payload."""
        option = build_layman_option(
            label="Retire Legacy Test Server",
            plain_english_summary="Decommission the standalone mock test server and consolidate testing under the primary gateway.",
            impact_statement="Eliminates duplicate maintenance overhead and prevents false positive test runs without affecting coverage.",
            confidence_score=0.88,
            credible_interval=(0.82, 0.94),
            sample_size=24,
            evidence_refs=["urn:evidence:audit-report-20261004#section-2"],
            recommended=True,
            prior_adjustment=0.03,
        )

        # 1. Zero jargon verification
        self.assertFalse(contains_jargon(option["label"]))
        self.assertFalse(contains_jargon(option["plain_english_summary"]))
        self.assertFalse(contains_jargon(option["impact_statement"]))

        # 2. Schema compliance
        jsonschema.validate(instance=option, schema=self.schema)
        self.assertEqual(option["confidence_level"], "HIGH")
        self.assertEqual(option["tier"], "HIGH")
        self.assertEqual(option["confidence_pct"], 88.0)
        self.assertEqual(option["credible_interval"], [0.82, 0.94])
        self.assertTrue(option["recommended"])

    def test_e2e_margin_evaluator_layman_payload_lifecycle(self) -> None:
        """Verify MarginEvaluator generates layman-compliant ask_user_question payloads."""
        opt_a = build_layman_option(
            label="Retire Legacy Test Server",
            plain_english_summary="Decommission the standalone mock test server and consolidate under the primary gateway.",
            impact_statement="Eliminates maintenance overhead and prevents test evasion.",
            confidence_score=0.72,
            credible_interval=(0.66, 0.78),
            sample_size=15,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Rewire Legacy Test Server with Subprocesses",
            plain_english_summary="Maintain the secondary server by spawning dedicated local system processes for each test run.",
            impact_statement="Increases test execution latency and requires maintaining dual server interfaces.",
            confidence_score=0.65,
            credible_interval=(0.58, 0.72),
            sample_size=12,
            recommended=False,
        )

        # Evaluated margin: 0.72 - 0.65 = 0.07 (< 0.15 threshold) -> Must trigger HITL!
        eval_result = MarginEvaluator.evaluate([opt_a, opt_b])
        self.assertTrue(eval_result["surface_hitl"])
        self.assertFalse(eval_result["decision_resolved_autonomously"])

        payload = eval_result["ask_user_question_payload"]
        self.assertIn("options", payload)
        self.assertEqual(len(payload["options"]), 2)

        # Verify formatted strings are layman clean and contain impact statements
        for opt_str in payload["options"]:
            self.assertIn("Confidence:", opt_str)
            self.assertIn("Impact:", opt_str)
            jargon_errors = scan_for_jargon(opt_str)
            self.assertEqual(jargon_errors, [], f"Formatted option contains jargon: {jargon_errors}")

        # Verify full interactive presentation validation passes
        question_payload = {
            "questions": [
                {
                    "question": "Which architecture path should be authorized for the test server?",
                    "options": payload["options"],
                }
            ]
        }
        presentation_errors = validate_hitl_presentation(question_payload)
        self.assertEqual(presentation_errors, [], f"Presentation validation failed: {presentation_errors}")

    def test_e2e_surfacing_gate_standardizes_to_v1_1_schema(self) -> None:
        """Verify evaluate_hitl_surfacing_gate standardizes input options to schema v1.1.0."""
        raw_options = [
            {
                "label": "Authorize Production Release",
                "plain_english_summary": "Promote release candidate to enterprise production cluster.",
                "impact_statement": "Enables new customer features while preserving high availability guarantees.",
                "confidence": 0.92,
                "confidence_interval": [0.88, 0.96],
            },
            {
                "label": "Delay Release for Verification",
                "plain_english_summary": "Postpone deployment until second verification cycle completes.",
                "impact_statement": "Reduces regression risk but delays release schedule by one operational cycle.",
                "confidence": 0.60,
                "confidence_interval": [0.52, 0.68],
            },
        ]

        gate_res = evaluate_hitl_surfacing_gate(raw_options)
        calibrated = gate_res["calibrated_options"]
        self.assertEqual(len(calibrated), 2)

        for opt in calibrated:
            validated = validate_hitl_option(opt)
            jsonschema.validate(instance=validated, schema=self.schema)
            self.assertIn("plain_english_summary", validated)
            self.assertIn("impact_statement", validated)
            self.assertIn("credible_interval", validated)
            self.assertEqual(len(validated["credible_interval"]), 2)

    def test_e2e_display_label_formatting(self) -> None:
        """Verify format_layman_display_label adheres to governed display format."""
        opt = build_layman_option(
            label="Adopt Distributed Cache",
            plain_english_summary="Switch database caching layer to multi-region distributed nodes.",
            impact_statement="Reduces query latency by forty percent while increasing operational complexity.",
            confidence_score=0.91,
            credible_interval=(0.86, 0.95),
            sample_size=30,
            recommended=True,
        )
        display = format_layman_display_label(opt)
        self.assertTrue(display.startswith("(Recommended) Adopt Distributed Cache"))
        self.assertIn("Confidence: HIGH (91%) [86%–95%, n=30]", display)
        self.assertIn("Impact: Reduces query latency", display)
        self.assertFalse(contains_jargon(display))


if __name__ == "__main__":
    unittest.main()
