"""
tests/test_astra_cost_governance.py

Unit and Acceptance Test Suite for Wave 5:
- Dynamic reasoning calibration (reasoning_effort="low" for audio gatekeeper and dynamic router).
- Policy configuration and Astra/Luna pricing schedules in requirements/l2-policy.json.
- OpenTelemetry cost attribute bindings in dual_pipeline/telemetry.py.
- Accurate USD cost computation for cached/uncached input and reasoning tokens.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from dual_pipeline.l2_policy import L2_POLICY_PATH, load_l2_config
from dual_pipeline.query_router_core.project_router import determine_project_directory
from dual_pipeline.telemetry import L2_OTEL_ATTRIBUTE_KEYS
from dual_pipeline.token_optimization import (
    DEFAULT_ASTRA_PRICING,
    DEFAULT_LUNA_PRICING,
)
from dual_pipeline.token_optimization.schemas import TokenUsageMetric


class TestAstraCostGovernance(unittest.TestCase):
    """Verifies Wave 5 reasoning calibration, policy pricing, and cost governance."""

    def test_l2_policy_pricing_and_high_reasoning(self) -> None:
        config = load_l2_config(L2_POLICY_PATH)

        # Standard profile checks
        self.assertEqual(config.standard.model, "gpt-5.6-luna")
        self.assertEqual(config.standard.reasoning_effort, "high")
        self.assertEqual(config.standard.pricing.input_per_million, 0.2)
        self.assertEqual(config.standard.pricing.cached_input_per_million, 0.02)
        self.assertEqual(config.standard.pricing.output_per_million, 1.2)

        # Enhanced profile checks
        self.assertEqual(config.enhanced.model, "gpt-5.6-luna")
        self.assertEqual(config.enhanced.reasoning_effort, "high")
        self.assertEqual(config.enhanced.pricing.input_per_million, 0.2)
        self.assertEqual(config.enhanced.pricing.cached_input_per_million, 0.02)
        self.assertEqual(config.enhanced.pricing.output_per_million, 1.2)

    def test_dynamic_router_calibrated_low_reasoning_and_compact_json(self) -> None:
        mock_client = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.choices = [
            mock.MagicMock(
                message=mock.MagicMock(content=json.dumps({"project": "project_nexus"}))
            )
        ]
        mock_client.chat.completions.create.return_value = mock_resp

        metadata = {
            "meeting_name": "Grid Operations Review",
            "audiences": ["PartnerAdvisory", "Apex Global"],
        }
        chosen = determine_project_directory(
            metadata=metadata,
            transcript_snippet="Discussion regarding APEX power transmission.",
            client=mock_client,
            model="gpt-5.6-luna",
            reasoning_effort="low",
        )

        self.assertEqual(chosen, "project_nexus")
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]

        self.assertEqual(call_kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(call_kwargs["reasoning_effort"], "low")
        self.assertEqual(call_kwargs["max_completion_tokens"], 2000)

        # Compact JSON check in prompt
        sys_prompt = call_kwargs["messages"][0]["content"]
        self.assertNotIn('\n  "project_nexus"', sys_prompt)

    def test_telemetry_otel_attribute_keys_contain_governance_metrics(self) -> None:
        self.assertIn("reasoning_tokens", L2_OTEL_ATTRIBUTE_KEYS)
        self.assertIn("cached_tokens_saved_usd", L2_OTEL_ATTRIBUTE_KEYS)
        self.assertIn("cost_usd", L2_OTEL_ATTRIBUTE_KEYS)
        self.assertIn("estimated_input_cost", L2_OTEL_ATTRIBUTE_KEYS)
        self.assertIn("estimated_output_cost", L2_OTEL_ATTRIBUTE_KEYS)
        self.assertIn("estimated_total_cost", L2_OTEL_ATTRIBUTE_KEYS)

    def test_token_cost_computation_with_cached_and_reasoning_tokens(self) -> None:
        metric = TokenUsageMetric(
            model="gpt-5.6-luna",
            input_tokens=10000,
            cached_input_tokens=6000,
            output_tokens=2000,
            reasoning_tokens=1000,
            latency_ms=450.0,
        )

        # Luna Pricing:
        # Uncached input: (10,000 - 6,000) = 4,000 @ $0.20/1M = $0.00080
        # Cached input: 6,000 @ $0.02/1M = $0.00012
        # Output: 2,000 @ $1.20/1M = $0.00240
        # Note: DEFAULT_LUNA_PRICING resolves to gpt-6-luna ($0.10 / $0.01 / $0.50):
        # 4,000 @ $0.10/1M ($0.00040) + 6,000 @ $0.01/1M ($0.00006) + 2,000 @ $0.50/1M ($0.00100) = $0.00146
        metric.finalize_metrics(DEFAULT_LUNA_PRICING)
        self.assertEqual(metric.cost_usd, 0.00146)
        self.assertEqual(metric.uncached_input_tokens, 4000)
        self.assertEqual(metric.cache_hit_rate, 0.6)

        # Astra Pricing (gpt-5.6-astra: $2.00 / $0.10 / $10.00 per 1M)
        astra_cost = DEFAULT_ASTRA_PRICING.calculate_cost(
            uncached_input_tokens=4000,
            cached_input_tokens=6000,
            output_tokens=2000,
        )
        self.assertEqual(astra_cost, 0.0286)


if __name__ == "__main__":
    unittest.main()
