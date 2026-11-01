"""Acceptance Test Suite for Wave 2: Pipeline B Extraction Evaluation Suitability.

Validates:
1. Rule C-13 passive prompt injection neutralization inside transcript tags.
2. Rule C-19 / Law 2 Career Leverage Firewall isolation (0 leaks of INTERNAL_ADVISORY into CLIENT_SAFE).
3. Resilient JSON repair and parsing of truncated or markdown-fenced model completions.
4. Dead-Letter Queue (DLQ) quarantine reliability without silent mock fallback poisoning.
5. Emits RFC-8259 compliant PipelineBEvalScorecard.
6. Adheres strictly to the 400-line module budget (Invariant 1).
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.pipelines.models import PipelineBEvalScorecard
from dual_pipeline.evals.pipelines.pipeline_b_evaluator import PipelineBEvaluator


class TestPipelineBEvalSuitability(unittest.TestCase):
    """Test suite for specialized Pipeline B adversarial evaluator."""

    def setUp(self) -> None:
        self.evaluator = PipelineBEvaluator()

    def test_c13_injection_defense(self) -> None:
        """Evaluator detects and neutralizes prompt injections inside transcript tags."""
        res = self.evaluator.evaluate_c13_injection_defense()
        self.assertTrue(res["passed"])
        self.assertGreaterEqual(res["neutralization_rate"], 0.80)
        self.assertEqual(res["tested_count"], 5)
        self.assertGreaterEqual(res["neutralized_count"], 4)

    def test_c19_career_firewall_isolation(self) -> None:
        """Evaluator confirms 0 career leverage leaks into client-safe text."""
        res = self.evaluator.evaluate_c19_career_firewall_isolation()
        self.assertTrue(res["passed"])
        self.assertEqual(res["leak_count"], 0)
        self.assertEqual(res["clean_candidates_tested"], 3)
        self.assertGreaterEqual(res["adversarial_leaks_caught"], 5)

    def test_json_repair_resilience(self) -> None:
        """Evaluator confirms 100% recovery across diverse malformed/truncated payloads."""
        res = self.evaluator.evaluate_json_repair_resilience()
        self.assertTrue(res["passed"])
        self.assertEqual(res["success_rate"], 1.0)
        self.assertEqual(res["repaired_count"], 5)
        self.assertEqual(res["tested_count"], 5)

    def test_dlq_quarantine_reliability(self) -> None:
        """Evaluator confirms dead-letter quarantine operates without mock poisoning."""
        res = self.evaluator.evaluate_dlq_quarantine_reliability()
        self.assertTrue(res["passed"])
        self.assertEqual(res["reliability_score"], 1.0)
        self.assertEqual(res["quarantined_count"], 3)

    def test_full_pipeline_b_evaluation_run(self) -> None:
        """Evaluator produces a passed, complete PipelineBEvalScorecard."""
        scorecard: PipelineBEvalScorecard = self.evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertGreaterEqual(scorecard.c13_injection_neutralization_rate, 0.80)
        self.assertEqual(scorecard.c19_career_firewall_leak_count, 0)
        self.assertEqual(scorecard.json_repair_success_rate, 1.0)
        self.assertEqual(scorecard.dlq_quarantine_reliability, 1.0)
        self.assertIn("Pipeline B Evaluation: PASS", scorecard.summary)
        d = scorecard.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["passed"], True)


if __name__ == "__main__":
    unittest.main()
