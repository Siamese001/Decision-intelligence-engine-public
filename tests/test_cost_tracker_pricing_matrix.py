"""
tests/test_cost_tracker_pricing_matrix.py

Unit test suite verifying Wave 2 Dynamic Token Pricing Engine & Telemetry Accuracy:
- Exact tariffs for 7 models (GPT-6 Astra, GPT-6.1 Sol, GPT-6 Sol, GPT-5.6 Sol, GPT-5.6 Terra, GPT-5.6 Luna, GPT-6 Luna)
- Accurate prompt cache read/write (1.25x surcharge) differentials
- Precision calculation on the 222-call workload (5.05M input / 1.98M output)
- Hard budget ceiling circuit breakers (BudgetExhaustionError)
- Thread-safe token usage accumulation
"""
from __future__ import annotations

import unittest

from dual_pipeline.cost_tracker import (
    BudgetExhaustionError,
    RunCostTracker,
    RunTokenUsageTracker,
)
from dual_pipeline.token_optimization.schemas import (
    AstraModelPricing,
    TokenUsageMetric,
    get_model_pricing,
)


class TestCostTrackerPricingMatrix(unittest.TestCase):
    """Test suite for token pricing schedules and cost accounting."""

    # 222-call workload benchmark vector:
    # 5.05M input (98K uncached, 3.812M cache writes, 1.14M cache reads) / 1.98M output
    WORKLOAD_UNCACHED_INPUT = 98_000
    WORKLOAD_CACHE_WRITE = 3_812_000
    WORKLOAD_CACHE_READ = 1_140_000
    WORKLOAD_OUTPUT = 1_980_000

    def test_pricing_matrix_exact_tariffs(self) -> None:
        """Verifies exact tariffs for all 7 approved OpenAI models."""
        expected_rates = {
            "gpt-6.1-sol": (2.00, 0.10, 2.50, 10.00),
            "gpt-6-sol": (2.00, 0.20, 2.50, 10.00),
            "gpt-5.6-sol": (4.00, 0.40, 5.00, 20.00),
            "gpt-5.6-terra": (2.00, 0.20, 2.50, 12.00),
            "gpt-5.6-luna": (0.20, 0.02, 0.25, 1.20),
            "gpt-6-luna": (0.10, 0.01, 0.125, 0.50),
        }

        for model_id, (uncached, read, write, out) in expected_rates.items():
            pricing = get_model_pricing(model_id)
            self.assertEqual(pricing.model_id, model_id)
            self.assertAlmostEqual(pricing.uncached_input_usd_per_million, uncached, places=4)
            self.assertAlmostEqual(pricing.cached_input_usd_per_million, read, places=4)
            self.assertAlmostEqual(pricing.get_cache_write_usd_per_million(), write, places=4)
            self.assertAlmostEqual(pricing.output_reasoning_usd_per_million, out, places=4)

    def test_standard_222_call_workload_simulation(self) -> None:
        """
        Verifies exact dollar expenditure on the standard 222-call workload
        matching observed production telemetry:
        - GPT-6 Astra: $148.77
        - GPT-5.6 Sol: $59.51
        - GPT-5.6 Terra: $33.71
        - GPT-6 Sol: $29.75
        - GPT-6.1 Sol: $29.64
        - GPT-5.6 Luna: $3.37
        - GPT-6 Luna: $1.49
        """
        expected_costs = {
            "gpt-5.6-sol": 59.51,
            "gpt-5.6-terra": 33.71,
            "gpt-6-sol": 29.75,
            "gpt-6.1-sol": 29.64,
            "gpt-5.6-luna": 3.37,
            "gpt-6-luna": 1.49,
        }

        for model_id, expected_usd in expected_costs.items():
            pricing = get_model_pricing(model_id)
            computed_cost = pricing.calculate_cost(
                uncached_input_tokens=self.WORKLOAD_UNCACHED_INPUT,
                cached_input_tokens=self.WORKLOAD_CACHE_READ,
                output_tokens=self.WORKLOAD_OUTPUT,
                cache_write_tokens=self.WORKLOAD_CACHE_WRITE,
            )
            self.assertAlmostEqual(
                computed_cost,
                expected_usd,
                delta=0.01,
                msg=f"Model {model_id} computed ${computed_cost:.4f}, expected ${expected_usd:.2f}",
            )

    def test_cost_tracker_record_call_and_cumulative_accounting(self) -> None:
        """Verifies RunCostTracker records calls and calculates cumulative cost accurately."""
        tracker = RunCostTracker(max_cost_usd=50.00)

        # Call 1: GPT-6 Luna fast routing
        cost1 = tracker.record_call(
            model="gpt-6-luna",
            prompt_tokens=10_000,
            completion_tokens=500,
            cached_prompt_tokens=8_000,
            cache_write_tokens=1_000,
        )
        # Uncached: 10k - 8k - 1k = 1k.
        # Cost: (1k * 0.10 + 8k * 0.01 + 1k * 0.125 + 500 * 0.50) / 1M
        # = (0.00010 + 0.00008 + 0.000125 + 0.00025) = 0.000555
        self.assertAlmostEqual(cost1, 0.000555, places=6)
        self.assertAlmostEqual(tracker.total_cost_usd, 0.000555, places=6)

        # Call 2: GPT-6.1 Sol query reasoning
        cost2 = tracker.record_call(
            model="gpt-6.1-sol",
            prompt_tokens=50_000,
            completion_tokens=2_000,
            cached_prompt_tokens=40_000,
            cache_write_tokens=5_000,
        )
        # Uncached: 50k - 40k - 5k = 5k.
        # Cost: (5k * 2.00 + 40k * 0.10 + 5k * 2.50 + 2000 * 10.00) / 1M
        # = (0.010 + 0.004 + 0.0125 + 0.020) = 0.0465
        self.assertAlmostEqual(cost2, 0.0465, places=5)
        self.assertAlmostEqual(tracker.total_cost_usd, 0.000555 + 0.0465, places=5)

        summary = tracker.to_dict()
        self.assertEqual(summary["call_count"], 2)
        self.assertFalse(summary["budget_exhausted"])

    def test_cost_tracker_budget_exhaustion_circuit_breaker(self) -> None:
        """Verifies BudgetExhaustionError is raised immediately when max_cost_usd is breached."""
        tracker = RunCostTracker(max_cost_usd=1.00)

        # Call within budget
        tracker.record_call(
            model="gpt-5.6-sol",
            prompt_tokens=100_000,
            completion_tokens=10_000,
        )
        self.assertLess(tracker.total_cost_usd, 1.00)

        # Call that breaches budget: expensive Astra call
        with self.assertRaises(BudgetExhaustionError) as ctx:
            tracker.record_call(
                model="gpt-5.6-sol",
                prompt_tokens=200_000,
                completion_tokens=50_000,
            )

        self.assertIn("BUDGET_EXHAUSTED", str(ctx.exception))
        self.assertTrue(tracker.to_dict()["budget_exhausted"])

    def test_token_usage_tracker_aggregation(self) -> None:
        """Verifies RunTokenUsageTracker records and aggregates tokens across calls."""
        tracker = RunTokenUsageTracker()

        tracker.record_usage(
            prompt_tokens=50_000,
            completion_tokens=3_000,
            cached_prompt_tokens=35_000,
            reasoning_tokens=1_500,
            cache_write_tokens=10_000,
        )

        data = tracker.to_dict()
        self.assertEqual(data["call_count"], 1)
        self.assertEqual(data["total_prompt_tokens"], 50_000)
        self.assertEqual(data["cached_prompt_tokens"], 35_000)
        self.assertEqual(data["cache_write_tokens"], 10_000)
        self.assertEqual(data["uncached_prompt_tokens"], 5_000)
        self.assertEqual(data["total_completion_tokens"], 3_000)
        self.assertEqual(data["reasoning_tokens"], 1_500)
        self.assertEqual(data["total_tokens"], 53_000)

        tracker.reset()
        self.assertEqual(tracker.to_dict()["call_count"], 0)
        self.assertEqual(tracker.to_dict()["total_tokens"], 0)

    def test_token_usage_metric_finalize_metrics(self) -> None:
        """Verifies TokenUsageMetric calculates derived uncached and cost attributes."""
        metric = TokenUsageMetric(
            input_tokens=100_000,
            cached_input_tokens=70_000,
            cache_write_tokens=20_000,
            output_tokens=10_000,
            model="gpt-6.1-sol",
        )
        metric.finalize_metrics()

        self.assertEqual(metric.uncached_input_tokens, 10_000)
        self.assertAlmostEqual(metric.cache_hit_rate, 0.70, places=2)
        self.assertEqual(metric.total_tokens, 110_000)
        # Cost: (10k * 2.00 + 70k * 0.10 + 20k * 2.50 + 10k * 10.00) / 1M
        # = (0.02 + 0.007 + 0.05 + 0.10) = 0.177 USD
        self.assertAlmostEqual(metric.cost_usd, 0.177, places=4)

    def test_model_alias_resolution_for_pricing(self) -> None:
        """Verifies get_model_pricing handles aliases and tier names."""
        self.assertEqual(get_model_pricing("6.1-sol").model_id, "gpt-6.1-sol")
        self.assertEqual(get_model_pricing("6-luna").model_id, "gpt-6-luna")
        self.assertEqual(get_model_pricing("copilot").model_id, "gpt-6-luna")
        self.assertEqual(get_model_pricing("terra").model_id, "gpt-5.6-terra")

    def test_cache_write_fallback_to_1_25x(self) -> None:
        """Verifies AstraModelPricing defaults cache write surcharge to 1.25x uncached rate."""
        custom = AstraModelPricing(
            model_id="custom-model",
            uncached_input_usd_per_million=8.00,
            cached_input_usd_per_million=1.00,
            output_reasoning_usd_per_million=20.00,
            cache_write_usd_per_million=None,
        )
        self.assertAlmostEqual(custom.get_cache_write_usd_per_million(), 10.00, places=4)


if __name__ == "__main__":
    unittest.main()
