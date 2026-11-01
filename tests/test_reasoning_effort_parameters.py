#!/usr/bin/env python3
"""
tests/test_reasoning_effort_parameters.py

Unit test suite for reasoning model parameter governance and effort evaluation.
Validates model `gpt-5.6-terra` across all seven reasoning efforts:
  none, minimal, low, medium, high, xhigh, max.
"""
import unittest

from config.credentials import (
    GOVERNED_MODELS,
    REASONING_MODEL_PREFIXES,
    sanitize_model_call_kwargs,
)
from dual_pipeline.model_registry import (
    ModelRegistry,
    ModelTier,
)
from dual_pipeline.token_optimization.schemas import (
    DEFAULT_TERRA_PRICING,
    ReasoningEffortTier,
    get_model_pricing,
)
from evals.evaluate_reasoning_effort_parameters import (
    ReasoningEffortParameterEvaluator,
)


class TestReasoningEffortParameters(unittest.TestCase):
    def setUp(self):
        self.model = "gpt-5.6-terra"

    def test_governed_models_and_reasoning_prefixes(self):
        """Verifies gpt-5.6-terra is in GOVERNED_MODELS and REASONING_MODEL_PREFIXES."""
        self.assertIn(self.model, GOVERNED_MODELS)
        self.assertTrue(any(self.model.startswith(pfx) for pfx in REASONING_MODEL_PREFIXES))

    def test_sanitize_temperature_preservation_on_none(self):
        """Verifies temperature is preserved when reasoning_effort='none'."""
        raw = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "reasoning_effort": "none",
            "temperature": 0.7,
            "top_p": 0.95,
        }
        sanitized = sanitize_model_call_kwargs(self.model, raw)
        self.assertEqual(sanitized["reasoning_effort"], "none")
        self.assertIn("temperature", sanitized)
        self.assertEqual(sanitized["temperature"], 0.7)

    def test_sanitize_parameter_immunity_on_active_reasoning(self):
        """Verifies temperature and top_p are stripped across active reasoning efforts."""
        active_efforts = ["low", "medium", "high", "xhigh"]
        for eff in active_efforts:
            raw = {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "reasoning_effort": eff,
                "temperature": 0.7,
                "top_p": 0.95,
                "presence_penalty": 0.5,
                "frequency_penalty": 0.5,
            }
            sanitized = sanitize_model_call_kwargs(self.model, raw)
            self.assertEqual(sanitized["reasoning_effort"], eff)
            self.assertNotIn("temperature", sanitized)
            self.assertNotIn("top_p", sanitized)
            self.assertNotIn("presence_penalty", sanitized)
            self.assertNotIn("frequency_penalty", sanitized)

    def test_wire_normalization_for_minimal_and_max(self):
        """Verifies minimal -> low and max/extreme -> xhigh normalization."""
        # Minimal normalization
        raw_min = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "reasoning_effort": "minimal",
            "temperature": 0.5,
        }
        san_min = sanitize_model_call_kwargs(self.model, raw_min)
        self.assertEqual(san_min["reasoning_effort"], "low")
        self.assertNotIn("temperature", san_min)

        # Max normalization
        raw_max = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "reasoning_effort": "max",
            "temperature": 0.5,
        }
        san_max = sanitize_model_call_kwargs(self.model, raw_max)
        self.assertEqual(san_max["reasoning_effort"], "xhigh")
        self.assertNotIn("temperature", san_max)

    def test_reasoning_effort_tier_enum_completeness(self):
        """Verifies ReasoningEffortTier enum contains all 7 effort options."""
        expected_values = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        actual_values = {e.value for e in ReasoningEffortTier}
        self.assertTrue(expected_values.issubset(actual_values))

    def test_token_pricing_for_terra(self):
        """Verifies get_model_pricing returns DEFAULT_TERRA_PRICING for gpt-5.6-terra."""
        pricing = get_model_pricing("gpt-5.6-terra")
        self.assertEqual(pricing.model_id, "gpt-5.6-terra")
        self.assertEqual(pricing, DEFAULT_TERRA_PRICING)

    def test_model_registry_fallbacks_contain_terra(self):
        """Verifies allowed_fallbacks across reasoning tiers include gpt-5.6-terra."""
        for tier in (ModelTier.FRONTIER_HIGH_REASONING, ModelTier.FRONTIER_REASONING, ModelTier.DIALECTICAL_JUDGE):
            spec = ModelRegistry.get_tier_spec(tier)
            self.assertIn("gpt-5.6-terra", spec.get("allowed_fallbacks", []))

    def test_reasoning_evaluator_all_efforts_mock_execution(self):
        """Verifies ReasoningEffortParameterEvaluator evaluates all 7 efforts with PASS outcome."""
        evaluator = ReasoningEffortParameterEvaluator(model_name=self.model, allow_mock=True)
        receipt = evaluator.run_all()
        self.assertEqual(receipt["terminal_outcome"], "PASS")
        self.assertEqual(receipt["total_efforts_evaluated"], 7)
        self.assertEqual(receipt["efforts_passed"], 7)
        self.assertEqual(len(receipt["results"]), 7)


if __name__ == "__main__":
    unittest.main()
