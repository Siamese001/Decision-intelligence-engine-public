#!/usr/bin/env python3
"""Unit tests for surname apostrophe and hyphen grounding in Gate 1.10 (Constraint C-27)."""
from __future__ import annotations

import unittest
from dual_pipeline.transcript_qa_core.gate1_entity_context import (
    _is_bare_name_ungrounded,
    evaluate_referenced_entity_context_gate,
)


class TestGate1SurnameApostrophe(unittest.TestCase):
    """Verifies that surnames containing apostrophes or hyphens are recognized as valid surnames."""

    def test_apostrophe_surname_not_ungrounded(self) -> None:
        line = "Confirm executive sponsor (Craig O'Meilly / Margie / Matt Hodges) to establish discovery steering forum."
        self.assertFalse(_is_bare_name_ungrounded(line, "craig"))

    def test_curly_apostrophe_surname_not_ungrounded(self) -> None:
        line = "Discussion with Craig O’Meilly regarding transmission assets."
        self.assertFalse(_is_bare_name_ungrounded(line, "craig"))

    def test_hyphenated_surname_not_ungrounded(self) -> None:
        line = "Consultation with Craig Smith-Jones on regional control room operations."
        self.assertFalse(_is_bare_name_ungrounded(line, "craig"))

    def test_bare_first_name_remains_flagged(self) -> None:
        line = "Craig will provide follow-up notes on Friday afternoon."
        self.assertTrue(_is_bare_name_ungrounded(line, "craig"))


if __name__ == "__main__":
    unittest.main()
