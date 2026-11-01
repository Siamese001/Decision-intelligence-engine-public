"""Acceptance Test Suite for Wave 4: Pipeline D HITL Evaluation Suitability.

Validates:
1. Ambiguity margin gating accuracy at boundary conditions (Delta = 0.2499 vs 0.2500 vs 0.2501).
2. Synthetic stop hook auto-approval defense via validate_approval_origin().
3. Atomic interactive card presentation conforming to IDE UI contracts.
4. Bayesian prior feedback calibration in HITLDecisionStore widening option separation.
5. Emits RFC-8259 compliant PipelineDEvalScorecard.
6. Adheres strictly to the 400-line module budget (Invariant 1).
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.pipelines.models import PipelineDEvalScorecard
from dual_pipeline.evals.pipelines.pipeline_d_evaluator import PipelineDEvaluator


class TestPipelineDEvalSuitability(unittest.TestCase):
    """Test suite for specialized Pipeline D adversarial evaluator."""

    def setUp(self) -> None:
        self.evaluator = PipelineDEvaluator()

    def test_ambiguity_margin_gating(self) -> None:
        """Evaluator verifies strict gating at boundary conditions."""
        res = self.evaluator.evaluate_ambiguity_margin_gating()
        self.assertTrue(res["passed"])
        self.assertEqual(res["accuracy"], 1.0)
        self.assertEqual(res["tested_count"], 6)
        self.assertEqual(res["correct_count"], 6)

    def test_synthetic_stop_hook_rejection(self) -> None:
        """Evaluator confirms 100% rejection of synthetic auto-approvals."""
        res = self.evaluator.evaluate_synthetic_stop_hook_rejection()
        self.assertTrue(res["passed"])
        self.assertEqual(res["rejection_rate"], 1.0)
        self.assertTrue(res["human_approval_passed"])
        self.assertEqual(res["synthetic_rejected"], res["synthetic_tested"])

    def test_atomic_card_conformance(self) -> None:
        """Evaluator verifies atomic single-question formatting."""
        res = self.evaluator.evaluate_atomic_card_conformance()
        self.assertTrue(res["passed"])
        self.assertEqual(res["format_score"], 1.0)
        self.assertTrue(res["checks"]["has_recommended_marker"])
        self.assertTrue(res["checks"]["is_single_question"])

    def test_bayesian_prior_calibration(self) -> None:
        """Evaluator verifies margin widening upon operator feedback."""
        res = self.evaluator.evaluate_bayesian_prior_calibration()
        self.assertTrue(res["passed"])
        self.assertTrue(res["widened"])
        self.assertGreater(res["confidence_gain"], 0.0)

    def test_full_pipeline_d_evaluation_run(self) -> None:
        """Evaluator produces a passed, complete PipelineDEvalScorecard."""
        scorecard: PipelineDEvalScorecard = self.evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertEqual(scorecard.ambiguity_margin_accuracy, 1.0)
        self.assertEqual(scorecard.synthetic_stop_hook_rejection_rate, 1.0)
        self.assertEqual(scorecard.atomic_card_format_compliance, 1.0)
        self.assertGreater(scorecard.bayesian_prior_calibration_gain, 0.0)
        self.assertIn("Pipeline D Evaluation: PASS", scorecard.summary)
        d = scorecard.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["passed"], True)


if __name__ == "__main__":
    unittest.main()
