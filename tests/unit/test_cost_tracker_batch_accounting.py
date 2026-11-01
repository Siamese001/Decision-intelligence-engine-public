"""Unit tests for cost tracker and batch accounting correctness (Wave 1: Tier 0)."""

import unittest

from dual_pipeline.cost_tracker import RunCostTracker
from dual_pipeline.model_registry import ModelTier, prepare_call_kwargs
from dual_pipeline.token_optimization.schemas import get_model_pricing


class TestCostTrackerBatchAccounting(unittest.TestCase):
    def test_cost_tracker_sync_call_full_rate(self):
        """Verify that synchronous calls default to is_batch=False and charge 1.0x."""
        tracker = RunCostTracker(max_cost_usd=100.0)
        cost = tracker.record_call(
            model="gpt-6-astra",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            is_batch=False,
        )
        # gpt-6-astra uncached input is $10.00 / 1M tokens
        self.assertAlmostEqual(cost, 10.00, places=2)
        history = tracker.call_history
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["batch"])

    def test_cost_tracker_batch_call_discount(self):
        """Verify that explicit is_batch=True applies the 50% discount."""
        tracker = RunCostTracker(max_cost_usd=100.0)
        cost = tracker.record_call(
            model="gpt-6-astra",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            is_batch=True,
        )
        # Batch discount gives 50% off -> $5.00
        self.assertAlmostEqual(cost, 5.00, places=2)
        history = tracker.call_history
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["batch"])

    def test_cost_tracker_batch_in_kwargs(self):
        """Verify that passing batch=True in kwargs is also honored."""
        tracker = RunCostTracker(max_cost_usd=100.0)
        cost = tracker.record_call(
            model="gpt-6.1-sol",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            batch=True,
        )
        # gpt-6.1-sol input is $2.00 / 1M tokens, 50% discount is $1.00
        self.assertAlmostEqual(cost, 1.00, places=2)
        history = tracker.call_history
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["batch"])

    def test_prepare_call_kwargs_does_not_inject_batch(self):
        """Verify prepare_call_kwargs does NOT falsely tag calls with batch=True."""
        kwargs = prepare_call_kwargs(ModelTier.FRONTIER_HIGH_REASONING, {"messages": []})
        self.assertNotIn("batch", kwargs, "prepare_call_kwargs must not inject batch=True automatically")

    def test_gemini_pricing_entries(self):
        """Verify Gemini models are registered in the pricing table with expected rates."""
        flash_pricing = get_model_pricing("gemini-3.8-flash")
        self.assertAlmostEqual(flash_pricing.uncached_input_usd_per_million, 0.10, places=3)
        self.assertAlmostEqual(flash_pricing.output_reasoning_usd_per_million, 0.40, places=3)

        lite_pricing = get_model_pricing("gemini-3.5-flash-lite")
        self.assertAlmostEqual(lite_pricing.uncached_input_usd_per_million, 0.075, places=3)

        pro_pricing = get_model_pricing("gemini-2.5-pro")
        self.assertAlmostEqual(pro_pricing.uncached_input_usd_per_million, 1.25, places=3)


if __name__ == "__main__":
    unittest.main()
