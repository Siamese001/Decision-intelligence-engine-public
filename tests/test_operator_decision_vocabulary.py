#!/usr/bin/env python3
"""Unit tests for Operator Decision Vocabulary SSOT and Separation of Concerns."""

import json
import unittest
from pathlib import Path

from config.operator_decision_vocabulary import (
    APPROVAL_SIGNATURE_VALID,
    ASK_USER_QUESTION,
    CANONICAL_VOCABULARY_TERMS,
    NEEDS_OPERATOR_DECISION,
    OPERATOR_INPUT_REQUIRED,
    TOOL_APPROVAL_REQUIRED,
    VOCABULARY_REGISTRY,
    disambiguate_term,
    is_canonical_term,
    validate_decision_record_boundary,
)


class TestOperatorDecisionVocabulary(unittest.TestCase):
    """Test suite verifying SSOT vocabulary terms and boundaries."""

    def test_canonical_constants_defined(self) -> None:
        """Verify all six canonical terms are uniquely defined and present in set."""
        expected = {
            "ask_user_question",
            "needs_operator_decision",
            "TOOL_APPROVAL_REQUIRED",
            "OPERATOR_INPUT_REQUIRED",
            "decision_record",
            "approval_signature_valid",
        }
        self.assertEqual(CANONICAL_VOCABULARY_TERMS, expected)
        for term in expected:
            self.assertTrue(is_canonical_term(term))

    def test_vocabulary_registry_completeness(self) -> None:
        """Verify every canonical term has a complete structured definition."""
        self.assertEqual(len(VOCABULARY_REGISTRY), 6)
        for term in CANONICAL_VOCABULARY_TERMS:
            self.assertIn(term, VOCABULARY_REGISTRY)
            definition = VOCABULARY_REGISTRY[term]
            self.assertEqual(definition.term, term)
            self.assertTrue(len(definition.plain_english_meaning) > 0)
            self.assertTrue(len(definition.operational_boundary) > 0)

    def test_disambiguation_mapping(self) -> None:
        """Verify deprecated terms disambiguate to canonical replacements."""
        self.assertEqual(disambiguate_term("ask_question"), ASK_USER_QUESTION)
        self.assertEqual(disambiguate_term("ask_user"), ASK_USER_QUESTION)
        self.assertEqual(disambiguate_term("requires_hitl"), NEEDS_OPERATOR_DECISION)
        self.assertEqual(disambiguate_term("HITL_REQUIRED_TOOLS"), TOOL_APPROVAL_REQUIRED)
        self.assertEqual(disambiguate_term("HITL_REQUIRED"), OPERATOR_INPUT_REQUIRED)
        self.assertEqual(disambiguate_term("hitl_receipt_bound"), APPROVAL_SIGNATURE_VALID)

    def test_wave1_term_inventory_maps_to_canonical_terms(self) -> None:
        """Acceptance Criterion 1: Each inventory row from Wave 1 maps to exactly one new term."""
        repo_root = Path(__file__).resolve().parents[1]
        inventory_file = (
            repo_root / "reports" / "ask_user_question_enforcement" / "term_inventory.json"
        )
        self.assertTrue(inventory_file.exists(), "Wave 1 term_inventory.json must exist")

        data = json.loads(inventory_file.read_text(encoding="utf-8"))
        inventory_rows = data.get("inventory", [])
        self.assertTrue(len(inventory_rows) > 0, "Inventory must contain rows")

        allowed_tags = {
            "immutable_record",
            "ask_user_question",
            "needs_operator_decision",
            "TOOL_APPROVAL_REQUIRED",
            "OPERATOR_INPUT_REQUIRED",
            "approval_signature_valid",
        }

        for row in inventory_rows:
            tag = row.get("tag")
            self.assertIn(
                tag,
                allowed_tags,
                f"Row {row} has unrecognized or missing tag {tag}",
            )
            # Verify exactly one meaning tag is present
            self.assertIsInstance(tag, str)

    def test_tool_approval_vs_decision_record_separation(self) -> None:
        """Acceptance Criterion 3: Tool approval and ambiguity rule are separate code paths.

        A tool approval must never be recorded as a multiple-choice decision record.
        """
        # Allowed: tool approval alone
        validate_decision_record_boundary(is_tool_approval=True, has_option_selection=False)

        # Allowed: option decision alone
        validate_decision_record_boundary(is_tool_approval=False, has_option_selection=True)

        # Prohibited: conflating tool approval with option selection
        with self.assertRaises(ValueError) as ctx:
            validate_decision_record_boundary(is_tool_approval=True, has_option_selection=True)
        self.assertIn("Separation of concerns violation", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
