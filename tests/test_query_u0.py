"""Unit tests for QueryU0Validator — extracted U0 query validation stage."""
from __future__ import annotations

import unittest

from dual_pipeline.query_u0 import QueryU0Validator, ValidatedQueryRequest


class TestQueryU0ValidatorBasic(unittest.TestCase):
    """Basic validation tests for the QueryU0Validator."""

    def setUp(self) -> None:
        self.validator = QueryU0Validator()

    def test_valid_simple_question(self) -> None:
        result = self.validator.validate("What is the status?")
        self.assertIsInstance(result, ValidatedQueryRequest)
        self.assertEqual(result.question, "What is the status?")
        self.assertEqual(result.classification, "internal")
        self.assertEqual(result.audiences, ["nee-engineering"])
        self.assertIsNotNone(result.question_digest)

    def test_question_whitespace_normalization(self) -> None:
        result = self.validator.validate("  What   is   the   status?  ")
        self.assertEqual(result.question, "What is the status?")

    def test_empty_question_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("")

    def test_whitespace_only_question_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("   ")

    def test_non_string_question_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate(123)  # type: ignore[arg-type]

    def test_long_question_rejected(self) -> None:
        long_q = "a" * 20_001
        with self.assertRaises(ValueError):
            self.validator.validate(long_q)

    def test_invalid_classification_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("What?", classification="top_secret")

    def test_valid_classifications(self) -> None:
        for cls in ("public", "internal", "confidential", "restricted"):
            result = self.validator.validate("What?", classification=cls)
            self.assertEqual(result.classification, cls)

    def test_invalid_materiality_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("What?", materiality="critical")

    def test_valid_materiality_values(self) -> None:
        for mat in ("low", "medium", "high"):
            result = self.validator.validate("What?", materiality=mat)
            self.assertEqual(result.materiality, mat)

    def test_none_materiality_accepted(self) -> None:
        result = self.validator.validate("What?")
        self.assertIsNone(result.materiality)

    def test_card_view_without_card_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("What?", card_view_version=1)

    def test_card_candidate_review_without_card_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("What?", card_candidate_review=True)

    def test_card_view_version_zero_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("What?", card_id="card-001", card_view_version=0)

    def test_valid_card_options(self) -> None:
        result = self.validator.validate(
            "What?", card_id="card-001", card_view_version=1
        )
        self.assertEqual(result.card_id, "card-001")
        self.assertEqual(result.card_view_version, 1)

    def test_multiple_audiences(self) -> None:
        result = self.validator.validate(
            "What?", audience="nee-engineering,leadership"
        )
        self.assertEqual(result.audiences, ["nee-engineering", "leadership"])

    def test_empty_audience_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.validator.validate("What?", audience="")


class TestQueryU0ValidatorProduction(unittest.TestCase):
    """Production qualification boundary tests."""

    def test_production_requires_auth_context(self) -> None:
        validator = QueryU0Validator(production_qualified=True)
        with self.assertRaises(PermissionError):
            validator.validate("What?")


if __name__ == "__main__":
    unittest.main()
