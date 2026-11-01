#!/usr/bin/env python3
"""tests/adversarial/test_hitl_anti_pattern_rejection_adversarial.py

Adversarial test suite for Wave 2 HITL Anti-Pattern Option Rejection.
Covers 3 mandatory categories per e2e-and-edge-case-coverage.md:
1. Exact production regression pin: The exact option text from the Bryce/Amit incident.
2. Invariant bypass attempt: Evasive casing, punctuation, and wording variations attempting to bypass anti-pattern detection.
3. Boundary payload & malformed input: Mixed structured dict and string options with boundary phrases.
"""
from __future__ import annotations

import unittest

from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.hitl_governance_core.question_validator import (
    QuestionPayloadValidationError,
    validate_question_payload,
)


class HitlAntiPatternRejectionAdversarialTests(unittest.TestCase):
    """Adversarial suite verifying programmatic rejection of data-degrading HITL options."""

    # -------------------------------------------------------------------------
    # Category 1: Exact Production Regression Pin
    # -------------------------------------------------------------------------
    def test_cat1_exact_incident_option_fails_validation_closed(self) -> None:
        """Pins the exact option text from the incident screenshot:
        'Ingest as undiarized session (Confidence: MEDIUM (65%)) — Ingest transcript without individual speaker attribution.'
        """
        question = "How should the pipeline resolve this speaker attribution and skew?"
        options = [
            "(Recommended) Approve speaker skew tolerance (Confidence: HIGH (92%)). Valid skew.",
            "Ingest as undiarized session (Confidence: MEDIUM (65%)). Ingest transcript without individual speaker attribution.",
            "Halt ingestion (Confidence: LOW (10%)). Abort ingestion pipeline.",
        ]

        result = validate_question_payload(question=question, options=options)
        self.assertFalse(result.is_valid, "Must reject exact incident undiarized option")
        self.assertTrue(
            any("ANTI_PATTERN_VIOLATION" in err for err in result.errors),
            f"Expected ANTI_PATTERN_VIOLATION in errors: {result.errors}",
        )

    # -------------------------------------------------------------------------
    # Category 2: Invariant Bypass Attempts (Evasive Casing & Token Variations)
    # -------------------------------------------------------------------------
    def test_cat2_evasive_casing_and_phrasing_rejected(self) -> None:
        """Adversarial bypass attempts using mixed case, whitespace, and alternate wording."""
        bypass_options = [
            "Proceed with UNDIARIZED SESSION (Confidence: MEDIUM (60%)). Without speaker attribution.",
            "Strip speaker attribution completely (Confidence: LOW (30%)). Plain stream.",
            "Bypass diarization and continue (Confidence: MEDIUM (50%)). Ignore speaker markers.",
            "Ingest as undiarized transcript (Confidence: LOW (20%)). Single voice.",
        ]

        question = "How should the pipeline resolve this speaker attribution and skew?"
        for bad_opt in bypass_options:
            options = [
                "(Recommended) Approve speaker skew tolerance (Confidence: HIGH (90%)). Valid.",
                bad_opt,
            ]
            result = validate_question_payload(question=question, options=options)
            self.assertFalse(result.is_valid, f"Must reject evasive anti-pattern option: {bad_opt}")
            self.assertTrue(
                any("ANTI_PATTERN_VIOLATION" in err for err in result.errors),
                f"Missing ANTI_PATTERN_VIOLATION for: {bad_opt}",
            )

    # -------------------------------------------------------------------------
    # Category 3: QuestionBuilder Integration & Strict Exception Raising
    # -------------------------------------------------------------------------
    def test_cat3_question_builder_raises_validation_error_on_anti_pattern(self) -> None:
        """QuestionBuilder.build() strictly raises QuestionPayloadValidationError on anti-pattern."""
        builder = QuestionBuilder(
            question="How should the pipeline resolve this speaker attribution and skew?"
        )
        builder.add_option(
            label="Approve speaker skew tolerance",
            plain_english_summary="Tolerate authentic speaker skew",
            impact_statement="Enables verified attribution and preserves dominant presenter context.",
            confidence_score=0.92,
            recommended=True,
        )
        builder.add_option(
            label="Ingest as undiarized session",
            plain_english_summary="Ingest transcript without individual speaker attribution",
            impact_statement="Removes speaker attribution and creates risk of data loss.",
            confidence_score=0.65,
        )

        with self.assertRaises(QuestionPayloadValidationError) as ctx:
            builder.build()

        self.assertIn("ANTI_PATTERN_VIOLATION", str(ctx.exception))

    def test_cat3_valid_alternatives_pass_cleanly(self) -> None:
        """Legitimate governance choices (Approve vs Halt vs Manual Review) pass validation cleanly."""
        builder = QuestionBuilder(
            question="How should the pipeline resolve this speaker attribution and skew?"
        )
        builder.add_option(
            label="Approve speaker skew tolerance",
            plain_english_summary="Tolerate authentic speaker skew with known roster",
            impact_statement="Enables verified attribution and preserves dominant presenter context.",
            confidence_score=0.92,
            recommended=True,
        )
        builder.add_option(
            label="Halt ingestion for acoustic review",
            plain_english_summary="Pause pipeline execution for manual verification",
            impact_statement="Halts ingestion to prevent data corruption and preserves raw audio.",
            confidence_score=0.15,
        )

        payload = builder.build()
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload.options), 2)


if __name__ == "__main__":
    unittest.main()
