"""
tests/test_hitl_confidence_contract_adversarial.py

Adversarial and boundary test suite verifying the mandatory confidence contract
for HITL interactive options (Invariant I-1, Wave 4).
"""
import json
import unittest
from pathlib import Path

from dual_pipeline.hitl_core.envelope import _format_option_confidence
from dual_pipeline.hitl_governance_core import (
    HitlConfidenceMissingError,
    parse_confidence_score,
    validate_hitl_governance_controls,
    validate_hitl_option,
    validate_hitl_presentation,
)


class HitlConfidenceContractAdversarialTests(unittest.TestCase):
    """Adversarial and edge-case verification for option confidence contract."""

    def test_missing_confidence_raises_fail_closed_exception(self) -> None:
        """Verify that an option without confidence fails closed with HitlConfidenceMissingError."""
        uncalibrated_options = [
            "Proceed with Option A",
            {"label": "Delete legacy branch", "key": "delete"},
            {"text": "Approve candidate", "action": "approve"},
            {"description": "No confidence provided here"},
        ]
        for opt in uncalibrated_options:
            with self.subTest(opt=opt):
                with self.assertRaises(HitlConfidenceMissingError):
                    parse_confidence_score(opt, require_confidence=True)

                with self.assertRaises(HitlConfidenceMissingError):
                    validate_hitl_option(opt)

                with self.assertRaises(HitlConfidenceMissingError):
                    if isinstance(opt, dict):
                        _format_option_confidence(opt, strict=True)
                    else:
                        _format_option_confidence({"label": opt}, strict=True)

    def test_valid_confidence_scores_and_tiers_parsed(self) -> None:
        """Verify that percentage strings, numeric scores, and tiers are parsed into canonical schema."""
        test_cases = [
            ("Option 1 (Confidence: HIGH 92%)", 0.92, "HIGH"),
            ("Option 2 (78%)", 0.78, "MEDIUM"),
            ("Option 3 (Confidence: LOW 35%)", 0.35, "LOW"),
            ({"label": "Option 4", "confidence_score": 0.88}, 0.88, "HIGH"),
            ({"label": "Option 5", "confidence": "MEDIUM"}, 0.60, "MEDIUM"),
            ({"label": "Option 6", "confidence_level": "LOW", "score": 0.20}, 0.20, "LOW"),
        ]
        for opt, expected_score, expected_tier in test_cases:
            with self.subTest(opt=opt):
                parsed = validate_hitl_option(opt)
                self.assertAlmostEqual(parsed["confidence_score"], expected_score, places=2)
                self.assertEqual(parsed["confidence_level"], expected_tier)
                self.assertEqual(parsed["tier"], expected_tier)
                self.assertIn("confidence_pct", parsed)
                self.assertIn("label", parsed)

    def test_canonical_json_schema_validation(self) -> None:
        """Verify schema file exists and validates against a canonical option dict."""
        schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        self.assertTrue(schema_path.exists(), "hitl-option.schema.json must exist")
        schema = json.loads(schema_path.read_text())
        self.assertIn("confidence_score", schema["required"])
        self.assertIn("confidence_level", schema["required"])
        self.assertIn("evidence_refs", schema["required"])

        valid_option = {
            "label": "Option A (Recommended)",
            "display_text": "(Recommended) Option A (Confidence: HIGH (91%)) - Retire legacy server component and route through primary gateway. Impact: Eliminates duplicate maintenance overhead while preserving test safety.",
            "plain_english_summary": "Retire legacy server component and route through primary gateway.",
            "impact_statement": "Eliminates duplicate maintenance overhead while preserving test safety.",
            "confidence_score": 0.91,
            "confidence_pct": 91.0,
            "confidence_level": "HIGH",
            "tier": "HIGH",
            "credible_interval": [0.85, 0.96],
            "evidence_refs": ["urn:provenance:meeting-123#span-4"],
            "recommended": True,
            "prior_adjustment": 0.05,
        }
        # Check required fields presence
        for req in schema["required"]:
            self.assertIn(req, valid_option)

    def test_hitl_presentation_validation_rejects_missing_confidence(self) -> None:
        """Verify validate_hitl_presentation returns error on uncalibrated options."""
        payload = {
            "questions": [
                {
                    "question": "Choose routing?",
                    "options": [
                        "Direct to Main",
                        "Quarantine candidate",
                    ],
                }
            ]
        }
        errors = validate_hitl_presentation(payload)
        self.assertTrue(
            any("HITL_VIOLATION_MISSING_CONFIDENCE" in e for e in errors),
            f"Expected missing confidence violation, got: {errors}",
        )

    def test_governance_controls_rule_and_hook_integrity(self) -> None:
        """Verify repository governance controls validate hooks.json and hitl-decisions.md."""
        repo_root = Path(".").resolve()
        errors = validate_hitl_governance_controls(repo_root)
        self.assertEqual(errors, [], f"HITL governance controls failed: {errors}")


if __name__ == "__main__":
    unittest.main()
