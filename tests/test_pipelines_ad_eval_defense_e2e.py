"""Wave 5: Pipelines A–D End-to-End Adversarial Defense & Suitability Suite.

Validates the full integrated defensive chain across Pipelines A -> B -> C -> D
and certifies that the evaluation harness answers the enterprise business case:
1. Pipeline A: Acoustic noise floor resilience, ungrounded caller locking, and chunk streaming SLA.
2. Pipeline B: C-13 passive prompt injection defense, C-19 Career Leverage Firewall isolation, and DLQ quarantine.
3. Pipeline C: Quad-engine retrieval, generational VectorCache invalidation, Law 6 quarantine, and latency SLA.
4. Pipeline D: HITL 25% ambiguity margin gating, synthetic stop hook defense, and Bayesian prior calibration.
5. Unified CLI: Seamless execution via run_agentic_evaluation_suite with RFC-8259 receipt sealing.
6. Adherence to 400-line module budget (Invariant 1) and unidirectional L0-L6 architecture.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.evals.pipelines import (
    PipelineAEvaluator,
    PipelineBEvaluator,
    PipelineCEvaluator,
    PipelineDEvaluator,
)
from tools.run_agentic_eval_harness import run_agentic_evaluation_suite


class TestPipelinesADEvalDefenseE2E(unittest.TestCase):
    """End-to-end multi-pipeline adversarial defense certification suite."""

    def test_pipeline_a_defense_suitability(self) -> None:
        """Verify Pipeline A answers its business case: zero hallucinated speakers under noise."""
        evaluator = PipelineAEvaluator(enrolled_speakers=["Alice Smith", "Drew Oakley"])
        scorecard = evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertGreaterEqual(scorecard.noise_floor_resilience_score, 0.75)
        self.assertEqual(scorecard.speaker_locking_accuracy, 1.0)
        self.assertEqual(scorecard.enrolled_speaker_preservation, 1.0)
        self.assertTrue(scorecard.chunk_latency_sla_passed)

    def test_pipeline_b_defense_suitability(self) -> None:
        """Verify Pipeline B answers its business case: zero advisory leaks and resilient extraction."""
        evaluator = PipelineBEvaluator()
        scorecard = evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertGreaterEqual(scorecard.c13_injection_neutralization_rate, 0.80)
        self.assertEqual(scorecard.c19_career_firewall_leak_count, 0)
        self.assertEqual(scorecard.json_repair_success_rate, 1.0)
        self.assertEqual(scorecard.dlq_quarantine_reliability, 1.0)

    def test_pipeline_c_defense_suitability(self) -> None:
        """Verify Pipeline C answers its business case: sub-second retrieval without stale cache drift."""
        evaluator = PipelineCEvaluator()
        scorecard = evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertTrue(scorecard.vector_cache_invalidation_verified)
        self.assertTrue(scorecard.latency_sla_passed)
        self.assertTrue(scorecard.law6_quarantine_enforced)
        self.assertTrue(scorecard.law2_namespace_isolation_enforced)

    def test_pipeline_d_defense_suitability(self) -> None:
        """Verify Pipeline D answers its business case: optimal decision velocity without stop hook bypass."""
        evaluator = PipelineDEvaluator()
        scorecard = evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertEqual(scorecard.ambiguity_margin_accuracy, 1.0)
        self.assertEqual(scorecard.synthetic_stop_hook_rejection_rate, 1.0)
        self.assertEqual(scorecard.atomic_card_format_compliance, 1.0)
        self.assertGreater(scorecard.bayesian_prior_calibration_gain, 0.0)

    def test_unified_cli_pipeline_harness_integration(self) -> None:
        """Verify unified CLI executes full pyramid + all pipeline evaluations and emits valid receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "test_e2e_receipt.json"
            res = run_agentic_evaluation_suite(
                output_receipt_path=receipt_path,
                update_ledger=False,
                pipeline="all",
            )
            self.assertEqual(res["terminal_verdict"], "PASS")
            self.assertIn("pipeline_results", res)
            p_res = res["pipeline_results"]
            self.assertIn("pipeline_a", p_res)
            self.assertIn("pipeline_b", p_res)
            self.assertIn("pipeline_c", p_res)
            self.assertIn("pipeline_d", p_res)
            self.assertTrue(p_res["pipeline_a"]["passed"])
            self.assertTrue(p_res["pipeline_b"]["passed"])
            self.assertTrue(p_res["pipeline_c"]["passed"])
            self.assertTrue(p_res["pipeline_d"]["passed"])
            self.assertTrue(receipt_path.exists())
            self.assertIn("receipt_digest", res)


if __name__ == "__main__":
    unittest.main()
