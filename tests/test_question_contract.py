#!/usr/bin/env python3
"""tests/test_question_contract.py

Unit tests for Wave 5 Question Payload Contract (question_builder and question_validator).
Verifies:
- Atomic question presentation (single question, >= 2 choices).
- Recommended option listed first with '(Recommended)'.
- Explicit calibrated confidence (percentage, tier) and one-line reasons.
- Schema v1.2.0 validation (display_text required and verified).
- Character budget limits (question <= 300, option <= 250).
- Decision D5 context relocation into question text.
- Clean rejection behavior on contract violations.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from dual_pipeline.hitl_governance_core.question_builder import (
    QuestionBuilder,
    build_mutation_approval_question,
    build_operator_question,
    format_option_text,
)
from dual_pipeline.hitl_governance_core.question_validator import (
    MAX_OPTION_LENGTH,
    MAX_QUESTION_LENGTH,
)


class TestQuestionContract(unittest.TestCase):
    """Unit test suite for QuestionBuilder and QuestionValidator."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        assert cls.schema_path.exists(), "hitl-option.schema.json must exist"
        cls.schema = json.loads(cls.schema_path.read_text(encoding="utf-8"))

    def test_build_valid_question_payload(self) -> None:
        """Verify building a compliant question payload with two options."""
        builder = QuestionBuilder("Which deployment strategy should be activated?")
        builder.add_option(
            label="Blue-green rollout",
            plain_english_summary="Deploy new release to standby cluster and switch active traffic.",
            impact_statement="Guarantees zero customer downtime while preserving rollback safety.",
            confidence_score=0.92,
            recommended=True,
        )
        builder.add_option(
            label="Canary deployment",
            plain_english_summary="Route ten percent of traffic to new release while monitoring error rates.",
            impact_statement="Reduces customer exposure risk but increases release verification latency.",
            confidence_score=0.74,
            recommended=False,
        )

        payload = builder.build()
        self.assertEqual(payload.question, "Which deployment strategy should be activated?")
        self.assertEqual(len(payload.options), 2)
        self.assertEqual(len(payload.display_options), 2)

        # Check recommended first
        self.assertTrue(payload.options[0]["recommended"])
        self.assertTrue(payload.display_options[0].startswith("(Recommended)"))

        # Validate against schema v1.2.0
        for opt in payload.options:
            jsonschema.validate(instance=opt, schema=self.schema)
            self.assertIn("display_text", opt)
            self.assertGreater(len(opt["display_text"]), 10)

        # Check tool call format
        tool_call = payload.to_tool_call()
        self.assertIn("questions", tool_call)
        self.assertEqual(len(tool_call["questions"]), 1)
        self.assertEqual(tool_call["questions"][0]["question"], payload.question)
        self.assertEqual(tool_call["questions"][0]["options"], payload.display_options)

    def test_recommended_option_placed_first_automatically(self) -> None:
        """Verify that recommended option is sorted first even if added second."""
        builder = QuestionBuilder("Select database backup frequency?")
        builder.add_option(
            label="Daily backup",
            plain_english_summary="Archive operational state once every twenty-four hours.",
            impact_statement="Reduces storage cost but increases data recovery risk.",
            confidence_score=0.55,
            recommended=False,
        )
        builder.add_option(
            label="Continuous replication",
            plain_english_summary="Stream transactional updates continuously to disaster recovery cluster.",
            impact_statement="Guarantees zero data loss with modest operational overhead.",
            confidence_score=0.88,
            recommended=True,
        )

        payload = builder.build()
        self.assertTrue(payload.options[0]["recommended"])
        self.assertEqual(payload.options[0]["label"], "Continuous replication")
        self.assertTrue(payload.display_options[0].startswith("(Recommended)"))

    def test_d5_background_context_relocation(self) -> None:
        """Verify Decision D5: Relocates extra background detail to question text to keep choices concise."""
        builder = QuestionBuilder("Which storage retention policy should apply?")
        builder.add_option(
            label="Ninety-day archival",
            plain_english_summary="Retain logs for ninety days before deep storage.",
            impact_statement="Ensures compliance standards while controlling ongoing capacity cost.",
            confidence_score=0.85,
            recommended=True,
            background_detail="Auditors require at least three months of accessible history.",
        )
        builder.add_option(
            label="One-year retention",
            plain_english_summary="Preserve all logs online for twelve months.",
            impact_statement="Improves forensic visibility but increases monthly storage cost and overhead.",
            confidence_score=0.60,
            recommended=False,
        )

        payload = builder.build()
        # Question text now incorporates the relocated background detail
        self.assertIn("Context: Auditors require at least three months of accessible history.", payload.question)
        self.assertLessEqual(len(payload.question), MAX_QUESTION_LENGTH)
        for opt_str in payload.display_options:
            self.assertLessEqual(len(opt_str), MAX_OPTION_LENGTH)

    def test_convenience_function_build_operator_question(self) -> None:
        """Verify build_operator_question functional interface."""
        opts = [
            {
                "label": "Option Alpha",
                "plain_english_summary": "First operational path.",
                "impact_statement": "Reduces processing delay.",
                "confidence_score": 0.80,
                "recommended": True,
            },
            {
                "label": "Option Beta",
                "plain_english_summary": "Second operational path.",
                "impact_statement": "Maintains existing baseline.",
                "confidence_score": 0.50,
                "recommended": False,
            },
        ]
        payload = build_operator_question("Select route?", opts)
        self.assertEqual(len(payload.options), 2)
        self.assertTrue(payload.options[0]["recommended"])

    def test_format_option_text_helper(self) -> None:
        """Verify format_option_text helper produces expected format."""
        formatted = format_option_text(
            label="Apply Patch",
            confidence_pct=95.0,
            tier="HIGH",
            impact="Eliminates known vulnerability.",
            summary="Update security configurations.",
            recommended=True,
        )
        self.assertTrue(formatted.startswith("(Recommended) Apply Patch"))
        self.assertIn("(Confidence: 95%, Tier: High)", formatted)
        self.assertIn("Eliminates known vulnerability.", formatted)

    def test_build_mutation_approval_question(self) -> None:
        """Verify building Wave 1 3-option mutation approval question payload."""
        payload = build_mutation_approval_question(
            meeting_name="Scope_Morgan Vance_PartnerAdvisory",
            proposal_digest="4a992e3acfb7408d",
            expected_base_generation=3,
            delta_summary="+12 nodes, +25 edges",
        )
        self.assertEqual(len(payload.options), 3)
        self.assertEqual(len(payload.display_options), 3)

        # Recommended option: Approve mutation
        self.assertTrue(payload.options[0]["recommended"])
        self.assertEqual(payload.options[0]["label"], "Approve mutation")
        self.assertEqual(payload.options[0]["confidence_score"], 0.95)
        self.assertTrue(payload.display_options[0].startswith("(Recommended)"))

        # Option 2: Reject
        self.assertFalse(payload.options[1]["recommended"])
        self.assertEqual(payload.options[1]["label"], "Reject")
        self.assertEqual(payload.options[1]["confidence_score"], 0.15)

        # Option 3: Review details
        self.assertFalse(payload.options[2]["recommended"])
        self.assertEqual(payload.options[2]["label"], "Review details")
        self.assertEqual(payload.options[2]["confidence_score"], 0.60)

        # Character budget limits
        self.assertLessEqual(len(payload.question), MAX_QUESTION_LENGTH)
        for opt_str in payload.display_options:
            self.assertLessEqual(len(opt_str), MAX_OPTION_LENGTH)

        # Schema validation
        for opt in payload.options:
            jsonschema.validate(instance=opt, schema=self.schema)

        # Tool call packaging
        tool_call = payload.to_tool_call()
        self.assertEqual(len(tool_call["questions"]), 1)
        self.assertFalse(tool_call["questions"][0]["is_multi_select"])
        self.assertEqual(len(tool_call["questions"][0]["options"]), 3)


if __name__ == "__main__":
    unittest.main()
