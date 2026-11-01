"""
tests/adversarial/test_judge_tier_routing_adversarial.py

Adversarial and boundary test suite for Wave 2: Judge Tier Downgrade for Backtests.
Covers:
  1. Unknown, corrupt, or empty judge tier inputs (fail-safe fallback to production).
  2. Case-insensitivity and whitespace injection.
  3. Precedence collision between explicit --judge-model and --judge-tier.
  4. Effort suffix overrides on model strings (e.g. gpt-6-luna-high).
  5. Short-circuit QA failure metadata preservation.
  6. Zero-division and boundary cases in concordance calculations.
"""
from __future__ import annotations

import argparse
import unittest

from dual_pipeline.transcript_qa_core.evaluator import (
    _resolve_judge_tier_and_model,
    evaluate_judge_and_release,
)
from tools.calibrate_judge_tiers import TierEvaluationResult, compute_concordance


class TestJudgeTierRoutingAdversarial(unittest.TestCase):
    """Adversarial and boundary test cases for judge tier routing."""

    def test_adversarial_unknown_or_corrupt_tier_string(self) -> None:
        for corrupt_val in ["unknown_tier", "super_fast", "", "   ", None, "1234"]:
            with self.subTest(tier=corrupt_val):
                args = argparse.Namespace(judge_tier=corrupt_val, judge_model=None, reasoning_effort=None)
                tier, model, effort = _resolve_judge_tier_and_model(meta={}, model=args.judge_model, reasoning_effort=args.reasoning_effort, judge_tier=args.judge_tier, kwargs={})
                self.assertEqual(model, "gpt-6-luna")
                self.assertEqual(effort, "max")

    def test_adversarial_case_insensitivity_and_whitespace(self) -> None:
        cases = [
            ("  FAST  ", "fast", "gpt-6-luna", "low"),
            ("Standard\n", "standard", "gpt-6.1-sol", "high"),
            ("PRODUCTION", "production", "gpt-6-luna", "max"),
        ]
        for raw_val, expected_tier, expected_model, expected_effort in cases:
            with self.subTest(raw=raw_val):
                args = argparse.Namespace(judge_tier=raw_val, judge_model=None, reasoning_effort=None)
                tier, model, effort = _resolve_judge_tier_and_model(meta={}, model=args.judge_model, reasoning_effort=args.reasoning_effort, judge_tier=args.judge_tier, kwargs={})
                self.assertEqual(tier, expected_tier)
                self.assertEqual(model, expected_model)
                self.assertEqual(effort, expected_effort)

    def test_adversarial_explicit_model_precedence_over_tier(self) -> None:
        # User specified --judge-tier fast AND explicit --judge-model gpt-5.6-luna
        args = argparse.Namespace(
            judge_tier="fast",
            judge_model="gpt-5.6-luna",
            reasoning_effort=None,
        )
        tier, model, effort = _resolve_judge_tier_and_model(meta={}, model=args.judge_model, reasoning_effort=args.reasoning_effort, judge_tier=args.judge_tier, kwargs={})
        self.assertEqual(tier, "fast")
        self.assertEqual(model, "gpt-5.6-luna")
        self.assertEqual(effort, "low")

    def test_adversarial_effort_suffix_override(self) -> None:
        args = argparse.Namespace(
            judge_tier="fast",
            judge_model="gpt-6-luna-high",
            reasoning_effort=None,
        )
        tier, model, effort = _resolve_judge_tier_and_model(meta={}, model=args.judge_model, reasoning_effort=args.reasoning_effort, judge_tier=args.judge_tier, kwargs={})
        self.assertEqual(tier, "fast")
        self.assertEqual(model, "gpt-6-luna-high")

    def test_adversarial_qa_failure_short_circuit_preserves_annotations(self) -> None:
        # Create failing artifacts (empty)
        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text="",
            f2_text="",
            f3_text="{}",
            source_text="",
            metadata={"judge_tier": "fast"},
            mock=False,
            auto_heal=False,
        )

        self.assertFalse(qa_rep.get("passed", True))
        self.assertTrue(judge_rep.get("short_circuit", False))
        self.assertEqual(judge_rep.get("judge_tier"), "fast")
        self.assertEqual(judge_rep.get("judge_model"), "gpt-6-luna")
        self.assertEqual(final_dec.get("judge_tier"), "fast")
        self.assertEqual(final_dec.get("judge_model"), "gpt-6-luna")

    def test_adversarial_concordance_zero_cost_edge_case(self) -> None:
        prod = TierEvaluationResult(
            tier="production", model="gpt-6-luna", effort="max",
            output_1_score=98, output_1_verdict="PASS",
            output_2_score=96, output_2_verdict="PASS",
            output_3_score=98, output_3_verdict="PASS",
            reconciliation_status="PASS", overall_status="PASS",
            estimated_cost=0.0, input_tokens=0, output_tokens=0,
        )
        cand = TierEvaluationResult(
            tier="fast", model="gpt-6-luna", effort="low",
            output_1_score=98, output_1_verdict="PASS",
            output_2_score=96, output_2_verdict="PASS",
            output_3_score=98, output_3_verdict="PASS",
            reconciliation_status="PASS", overall_status="PASS",
            estimated_cost=0.0, input_tokens=0, output_tokens=0,
        )
        res = compute_concordance(prod, cand)
        self.assertEqual(res["cost_savings_pct"], 0.0)
        self.assertEqual(res["mean_score_concordance_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
