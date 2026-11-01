"""
tests/test_wave1_gpt56_luna_medium.py

Acceptance test suite for Wave 1: Model Alignment, Credential Resolution,
and Token Optimization under OpenAI gpt-5.6-luna (reasoning_effort="medium").
"""
from __future__ import annotations

import unittest

from dual_pipeline.credentials import (
    GOVERNED_MODELS,
    resolve_openai_credentials,
)
from dual_pipeline.llm_judge import (
    DecomposedMultiJudge,
)
from dual_pipeline.token_optimization import (
    ReasoningEffortTier,
    TokenBudget,
    TokenOptimizationAgent,
    TokenUsageMetric,
    get_model_pricing,
)
from evals.evaluate_multi_judge_calibration import (
    MultiJudgeCalibrationEvaluator,
)


class TestWave1Gpt56LunaMediumAcceptance(unittest.TestCase):
    """Wave 1 Acceptance Suite under gpt-5.6-luna (reasoning_effort='medium')."""

    def setUp(self) -> None:
        self.model = "gpt-5.6-luna"
        self.reasoning_effort = "medium"

    def test_01_credentials_resolution_and_model_binding(self) -> None:
        """Verify gpt-5.6-luna is in governed models and resolves cleanly."""
        self.assertIn(self.model, GOVERNED_MODELS)
        creds = resolve_openai_credentials(
            allow_mock=True,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
        )
        self.assertEqual(creds.model, self.model)
        self.assertEqual(creds.reasoning_effort, self.reasoning_effort)
        if creds.has_live_key:
            self.assertTrue(creds.masked_key.startswith("sk-proj-") or creds.masked_key.startswith("sk-"))
        else:
            self.assertEqual(creds.masked_key, "[MOCK_MODE]")

    def test_02_token_pricing_and_budget_for_luna_medium(self) -> None:
        """Verify pricing schedule, token usage metric, and budget checks for gpt-5.6-luna."""
        pricing = get_model_pricing(self.model)
        self.assertEqual(pricing.model_id, "gpt-5.6-luna")
        self.assertEqual(pricing.uncached_input_usd_per_million, 0.20)
        self.assertEqual(pricing.cached_input_usd_per_million, 0.02)
        self.assertEqual(pricing.output_reasoning_usd_per_million, 1.20)

        # 80k input (50k cached, 30k uncached) + 8k output reasoning
        metric = TokenUsageMetric(
            input_tokens=80_000,
            cached_input_tokens=50_000,
            output_tokens=8_000,
            model=self.model,
        )
        metric.finalize_metrics(pricing)
        self.assertEqual(metric.uncached_input_tokens, 30_000)
        self.assertEqual(metric.cache_hit_rate, 0.625)
        # Cost: (30k * 0.20/1M) + (50k * 0.02/1M) + (8k * 1.20/1M) = 0.006 + 0.001 + 0.0096 = 0.0166
        self.assertAlmostEqual(metric.cost_usd, 0.0166, places=4)

        budget = TokenBudget(max_cost_usd=1.00, max_total_tokens=100_000)
        exceeded, reason = budget.check_exceeded(metric)
        self.assertFalse(exceeded)
        self.assertIsNone(reason)

    def test_03_decomposed_multi_judge_propagation_and_reconciliation(self) -> None:
        """Verify DecomposedMultiJudge binds gpt-5.6-luna-medium and evaluates cleanly."""
        judge = DecomposedMultiJudge(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            mock=True,
        )
        self.assertEqual(judge.model, self.model)
        self.assertEqual(judge.reasoning_effort, self.reasoning_effort)
        self.assertIsNotNone(judge.judge_forensic.model)
        self.assertEqual(judge.judge_forensic.reasoning_effort, self.reasoning_effort)
        self.assertIsNotNone(judge.judge_recap.model)
        self.assertEqual(judge.judge_recap.reasoning_effort, self.reasoning_effort)
        self.assertIsNotNone(judge.judge_graph.model)
        self.assertEqual(judge.judge_graph.reasoning_effort, self.reasoning_effort)
        self.assertTrue(judge.judge_forensic.use_local_model)
        self.assertTrue(judge.judge_recap.use_local_model)

        f1 = (
            "# APEX_TRANS Insights Hub -- Verbatim Transcript\n\n"
            "**Date:** 09/10/2026\n"
            "[00:00:25] **Alex Rivera**: Verbatim dialogue verified.\n"
        )
        f2 = (
            "# Subject: Executive Recap | 09/10/2026\n\n"
            "**Date:** 09/10/2026\n"
            "## Meeting Objective\nVerification objective.\n"
            "## Next Steps & Action Items\n"
            "| # | Action Item | Owner | Target Date | Priority | Epistemic Status |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | Architecture review | Alex Rivera | 09/11/2026 | HIGH | Grounded |\n"
        )
        f3 = {
            "schema_version": "5.7",
            "meeting_id": "meet-apex_trans-09102026",
            "title": "APEX_TRANS Insights Hub",
            "canonical_topic": "Architecture Review",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "observed_at": "09/10/2026",
            "evidence_index": {
                "ev-01": {
                    "speaker": "person:mitchell-simmons",
                    "source_classification": "technical",
                    "text": "Architecture review statement.",
                    "exact_text": "Verbatim dialogue verified.",
                    "locator": {"kind": "timestamp", "value": "00:00:25"},
                }
            },
            "graph_mutations": {"entity_upserts": [], "assertions": []},
            "action_updates": [],
        }

        import json
        eval_res = judge.evaluate_parallel(
            output_1_text=f1,
            output_2_text=f2,
            output_3_text=f3 if isinstance(f3, str) else json.dumps(f3),
            raw_transcript=f1,
            metadata={"date": "09/10/2026"},
        )
        self.assertEqual(eval_res["rubric_version"], "2.0")
        self.assertEqual(eval_res["evaluator_model"], self.model)
        self.assertEqual(eval_res["reasoning_effort"], self.reasoning_effort)

    def test_04_full_5_gate_calibration_suite_passes(self) -> None:
        """Run complete 5-gate MultiJudgeCalibrationEvaluator under gpt-5.6-luna (medium)."""
        evaluator = MultiJudgeCalibrationEvaluator(
            target_model=self.model,
            target_reasoning_effort=self.reasoning_effort,
            use_mock=True,
        )
        result = evaluator.run_all()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["target_model"], self.model)
        self.assertEqual(result["reasoning_effort"], self.reasoning_effort)

        gates = result["gates"]
        self.assertTrue(gates["1. golden_dataset_conformance"])
        self.assertTrue(gates["2. cohen_kappa_calibration"])
        self.assertTrue(gates["3. non_compensating_veto_enforcement"])
        self.assertTrue(gates["4. rubric_v2_model_binding"])
        self.assertTrue(gates["5. rule_c19_leakage_veto"])

        metrics = result["metrics"]
        self.assertGreaterEqual(metrics["cohens_kappa"], 0.82)
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["sensitivity"], 1.0)
        self.assertEqual(metrics["specificity"], 1.0)

    def test_05_token_optimization_agent_lifecycle(self) -> None:
        """Verify TokenOptimizationAgent optimizes prompts under gpt-5.6-luna."""
        agent = TokenOptimizationAgent(
            default_model=self.model,
            default_reasoning_effort=self.reasoning_effort,
        )
        self.assertEqual(agent.default_model, self.model)
        self.assertEqual(agent.default_reasoning_effort, self.reasoning_effort)
        self.assertEqual(agent.pricing.model_id, self.model)

        system_rubric = "You are an executive analyst for Apex Global Enterprises. " * 150
        user_transcript = "Alex Rivera: Reviewing transmission telemetry."

        directive, components = agent.optimize_request(
            system_prompt=system_rubric,
            user_content=user_transcript,
            task_type="stage01_forensic",
            model=self.model,
        )
        self.assertEqual(directive.recommended_reasoning_effort, ReasoningEffortTier.MEDIUM)
        self.assertTrue(components.is_cacheable_prefix)
        self.assertGreater(components.estimated_prefix_tokens, 1000)


if __name__ == "__main__":
    unittest.main()
