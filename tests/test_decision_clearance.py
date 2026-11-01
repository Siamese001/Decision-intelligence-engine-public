"""tests/test_decision_clearance.py

Unit test suite for Decision Clearance (Wave 6).
Verifies:
- require_cleared blocks on open qualifying decisions (exit code 2).
- require_cleared clears when decisions are ratified in the persistent ledger (exit code 0).
- Autonomous resolution for decisions with gap > 30%.
- Mandatory asking for decisions with gap <= 30% (including exact 30% boundary).
- Decision D6 fail-closed behavior on zero decisions (exit code 3).
- Clearance with allow_zero_decisions=True (exit code 0).
- Proper formatting of next_question_payload using Wave 5 QuestionBuilder.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.decision_clearance import (
    EXIT_CODE_CLEARED,
    EXIT_CODE_NO_DECISIONS,
    EXIT_CODE_OPERATOR_INPUT_REQUIRED,
    ScopedDecision,
    require_cleared,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
)


class DecisionClearanceUnitTests(unittest.TestCase):
    """Unit test cases for require_cleared and decision clearance logic."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.sqlite_path = self.temp_path / "hitl_decision_ledger.sqlite"
        self.json_path = self.temp_path / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_require_cleared_open_decision_blocks(self) -> None:
        """An open decision requiring operator review returns exit code 2 and question payload."""
        open_dec = ScopedDecision(
            decision_id="TEST-DEC-001",
            title="Database indexing approach",
            wave=2,
            top_confidence=0.60,
            second_confidence=0.45,
            handling="Ask",
            status="OPEN",
            question="Which database indexing approach should be adopted?",
            options=[
                {
                    "label": "Option A: Hybrid lexical and semantic indexing",
                    "plain_english_summary": "Use hybrid lexical and semantic indexing.",
                    "impact_statement": "Improves query retrieval accuracy without degrading search latency.",
                    "confidence_score": 0.60,
                    "recommended": True,
                },
                {
                    "label": "Option B: Text-only indexing",
                    "plain_english_summary": "Use text-only indexing.",
                    "impact_statement": "Reduces storage overhead while accepting lower search recall.",
                    "confidence_score": 0.45,
                    "recommended": False,
                },
            ],
        )

        res = require_cleared(
            wave=2,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
            extra_decisions=[open_dec],
        )

        self.assertFalse(res.cleared)
        self.assertEqual(res.exit_code, EXIT_CODE_OPERATOR_INPUT_REQUIRED)
        self.assertEqual(res.status_code, "OPERATOR_INPUT_REQUIRED")
        self.assertEqual(len(res.open_decisions), 1)
        self.assertEqual(res.open_decisions[0].decision_id, "TEST-DEC-001")
        self.assertIsNotNone(res.next_question_payload)
        self.assertIn("questions", res.next_question_payload)

    def test_require_cleared_ratified_decision_grants_clearance(self) -> None:
        """An authentic operator answer recorded in the ledger clears the decision."""
        dec_id = "TEST-DEC-002"
        record_operator_answer(
            decision_id=dec_id,
            question="Which caching strategy should be used?",
            options=[
                {"label": "In-memory caching", "confidence_score": 0.70},
                {"label": "Persistent disk caching", "confidence_score": 0.55},
            ],
            selected_option={"label": "In-memory caching", "confidence_score": 0.70},
            operator_origin="Approval verified from authentic human operator origin.",
            prior_user_command="implement",
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        dec = ScopedDecision(
            decision_id=dec_id,
            title="Caching strategy",
            wave=1,
            top_confidence=0.70,
            second_confidence=0.55,
            handling="Ask",
            status="OPEN",
            question="Which caching strategy should be used?",
            options=[
                {"label": "In-memory caching", "confidence_score": 0.70},
                {"label": "Persistent disk caching", "confidence_score": 0.55},
            ],
        )

        res = require_cleared(
            wave=1,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
            extra_decisions=[dec],
        )

        self.assertTrue(res.cleared)
        self.assertEqual(res.exit_code, EXIT_CODE_CLEARED)
        self.assertEqual(res.status_code, "CLEARED")
        self.assertEqual(len(res.open_decisions), 0)
        self.assertEqual(len(res.resolved_decisions), 1)

    def test_require_cleared_gap_greater_than_30pct_resolves_autonomously(self) -> None:
        """Decisions with confidence gap > 30% resolve autonomously without asking."""
        dec = ScopedDecision(
            decision_id="TEST-DEC-AUTO",
            title="Low ambiguity decision",
            wave=1,
            top_confidence=0.75,
            second_confidence=0.35,  # gap = 40% > 30%
            handling="Resolved autonomously",
            status="OPEN",
            question="Low ambiguity question?",
            options=[
                {"label": "Option A", "confidence_score": 0.75},
                {"label": "Option B", "confidence_score": 0.35},
            ],
        )

        res = require_cleared(
            wave=1,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
            extra_decisions=[dec],
        )

        self.assertTrue(res.cleared)
        self.assertEqual(res.exit_code, EXIT_CODE_CLEARED)
        self.assertEqual(len(res.resolved_decisions), 1)

    def test_require_cleared_gap_exactly_30pct_requires_asking(self) -> None:
        """Boundary test: confidence gap exactly 30% (<= 30% inclusive) requires asking."""
        dec = ScopedDecision(
            decision_id="TEST-DEC-BOUNDARY",
            title="Boundary ambiguity decision",
            wave=1,
            top_confidence=0.65,
            second_confidence=0.35,  # gap = 30.0% exactly
            handling="Ask",
            status="OPEN",
            question="Boundary ambiguity question?",
            options=[
                {
                    "label": "Option A",
                    "plain_english_summary": "Adopt Option A.",
                    "impact_statement": "Reduces operational latency.",
                    "confidence_score": 0.65,
                },
                {
                    "label": "Option B",
                    "plain_english_summary": "Adopt Option B.",
                    "impact_statement": "Increases execution throughput.",
                    "confidence_score": 0.35,
                },
            ],
        )

        res = require_cleared(
            wave=1,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
            extra_decisions=[dec],
        )

        self.assertFalse(res.cleared)
        self.assertEqual(res.exit_code, EXIT_CODE_OPERATOR_INPUT_REQUIRED)

    def test_require_cleared_zero_decisions_fails_closed_exit_code_3(self) -> None:
        """Decision D6: automated checkpoint finding zero decisions fails closed with exit code 3."""
        plan_file = self.temp_path / "empty_plan.md"
        plan_file.write_text("# Empty Plan\n## Wave 1: Setup\n### Milestones\n- Out\n### Criteria\n- Pass\n### Gate\n- Run\n", encoding="utf-8")

        res = require_cleared(
            plan_path=plan_file,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
            allow_zero_decisions=False,
        )

        self.assertFalse(res.cleared)
        self.assertEqual(res.exit_code, EXIT_CODE_NO_DECISIONS)
        self.assertEqual(res.status_code, "NO_DECISIONS_FOUND")

    def test_require_cleared_zero_decisions_allowed_with_flag(self) -> None:
        """Decision D6: allow_zero_decisions=True permits execution when zero decisions found."""
        plan_file = self.temp_path / "empty_plan.md"
        plan_file.write_text("# Empty Plan\n## Wave 1: Setup\n### Milestones\n- Out\n### Criteria\n- Pass\n### Gate\n- Run\n", encoding="utf-8")

        res = require_cleared(
            plan_path=plan_file,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
            allow_zero_decisions=True,
        )

        self.assertTrue(res.cleared)
        self.assertEqual(res.exit_code, EXIT_CODE_CLEARED)
        self.assertEqual(res.status_code, "CLEARED")


if __name__ == "__main__":
    unittest.main()
