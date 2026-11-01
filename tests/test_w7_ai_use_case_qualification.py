#!/usr/bin/env python3
"""Provider-free W7 AI decision-use-case qualification tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.run_w7_ai_use_case_qualification import (
    DEFAULT_CONTRACT,
    DEFAULT_PORTFOLIO,
    evaluate,
)


class W7AiUseCaseQualificationTests(unittest.TestCase):
    def test_all_priority_use_cases_and_hard_negatives_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate(
                Path(directory) / "graph.sqlite",
                DEFAULT_CONTRACT,
                DEFAULT_PORTFOLIO,
            )
        self.assertEqual("PASS", result["status"])
        self.assertEqual(7, result["passed_cases"])
        self.assertEqual(7, result["total_cases"])
        self.assertTrue(result["graph_unchanged"])
        self.assertEqual(0, result["provider_calls"])
        self.assertEqual(0, result["graph_mutations"])
        self.assertFalse(result["release_activation"])

    def test_candidate_cases_remain_candidate_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate(
                Path(directory) / "graph.sqlite",
                DEFAULT_CONTRACT,
                DEFAULT_PORTFOLIO,
            )
        for use_case_id in ("ai-pdlc-pov", "ai-productivity-metrics", "agentic-readiness"):
            case = result["cases"][use_case_id]["result"]
            self.assertEqual("CANDIDATE_ONLY", case["answer_posture"])
            self.assertTrue(case["decision_context"]["source_assertion_ids_by_item"])

    def test_hard_negatives_never_emit_an_answer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate(
                Path(directory) / "graph.sqlite",
                DEFAULT_CONTRACT,
                DEFAULT_PORTFOLIO,
            )
        for case_id in (
            "missing_required_project_status",
            "conflicting_project_status",
            "unreviewed_strategic_evidence",
        ):
            self.assertEqual("NO_ANSWER", result["cases"][case_id]["result"]["answer_posture"])
        self.assertTrue(result["cases"]["undeclared_answer_section"]["rejected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
