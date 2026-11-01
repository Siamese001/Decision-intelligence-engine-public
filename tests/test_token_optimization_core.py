"""
tests/test_token_optimization_core.py

Unit test suite verifying Wave 1 Token Optimization Core Engine,
BPE TokenProfiler, TokenBudget, and TokenOptimizationAgent under Unified Agent Architecture.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.token_optimization import (
    AstraModelPricing,
    OptimizationDirective,
    ReasoningEffortTier,
    TokenBudget,
    TokenOptimizationAgent,
    TokenProfiler,
    TokenUsageMetric,
)
from dual_pipeline.unified_agent_architecture import (
    validate_delegation_contract,
)


class TestTokenOptimizationCore(unittest.TestCase):
    def setUp(self) -> None:
        self.profiler = TokenProfiler(default_model="gpt-5.6-luna")
        self.agent = TokenOptimizationAgent(
            default_model="gpt-5.6-luna",
            default_reasoning_effort="high",
        )

    def test_profiler_count_tokens_gpt56_luna(self) -> None:
        text = "Strategic discovery meeting with Apex Global Enterprises leadership regarding LENS."
        tokens = self.profiler.count_tokens(text, model="gpt-5.6-luna")
        self.assertGreater(tokens, 5)
        self.assertLess(tokens, 30)

    def test_agent_gpt56_luna_high_configuration(self) -> None:
        self.assertEqual(self.agent.default_model, "gpt-5.6-luna")
        self.assertEqual(self.agent.default_reasoning_effort, "high")
        self.assertEqual(self.agent.pricing.model_id, "gpt-5.6-luna")

    def test_profiler_count_tokens_gpt56_luna(self) -> None:
        text = "Strategic discovery meeting with Apex Global Enterprises leadership regarding LENS."
        tokens = self.profiler.count_tokens(text, model="gpt-5.6-luna")
        self.assertGreater(tokens, 5)
        self.assertLess(tokens, 30)

    def test_profiler_message_tokens_framing(self) -> None:
        messages = [
            {"role": "system", "content": "You are an executive assistant."},
            {"role": "user", "content": "Hello world."},
        ]
        msg_tokens = self.profiler.count_message_tokens(messages, model="gpt-5.6-luna")
        raw_tokens = self.profiler.count_tokens("You are an executive assistant.") + self.profiler.count_tokens("Hello world.")
        # Message tokens must account for framing overhead (> raw tokens)
        self.assertGreater(msg_tokens, raw_tokens)

    def test_profiler_compact_json(self) -> None:
        sample_data = {
            "meeting_id": "meet_12345",
            "participants": ["Alice", "Bob", "Charlie"],
            "metrics": {"score": 95.5, "latency": 120},
        }
        indented = json.dumps(sample_data, indent=2)
        compacted = self.profiler.compact_json(sample_data)
        self.assertNotIn("  ", compacted)
        self.assertLess(len(compacted), len(indented))
        self.assertEqual(json.loads(compacted), sample_data)

    def test_profiler_strip_redundant_whitespace(self) -> None:
        bloated = "Line 1\n\n\n\nLine 2   \n\n\nLine 3"
        cleaned = self.profiler.strip_redundant_whitespace(bloated)
        self.assertEqual(cleaned, "Line 1\n\nLine 2\n\nLine 3")

    def test_profiler_truncate_to_token_limit(self) -> None:
        long_text = "This is a long sentence repeated multiple times. " * 50
        truncated = self.profiler.truncate_to_token_limit(long_text, max_tokens=20)
        token_count = self.profiler.count_tokens(truncated)
        self.assertLessEqual(token_count, 22)  # Within margin

    def test_usage_metric_and_astra_pricing(self) -> None:
        pricing = AstraModelPricing(
            model_id="gpt-5.6-luna",
            uncached_input_usd_per_million=5.00,
            cached_input_usd_per_million=2.50,
            output_reasoning_usd_per_million=25.00,
        )
        metric = TokenUsageMetric(
            input_tokens=100_000,
            cached_input_tokens=60_000,
            output_tokens=10_000,
            model="gpt-5.6-luna",
        )
        metric.finalize_metrics(pricing)

        self.assertEqual(metric.uncached_input_tokens, 40_000)
        self.assertEqual(metric.cache_hit_rate, 0.6)
        # Expected cost: (40k * 5.00 / 1M) + (60k * 2.50 / 1M) + (10k * 25.00 / 1M)
        # = 0.20 + 0.15 + 0.25 = 0.60
        self.assertAlmostEqual(metric.cost_usd, 0.60, places=4)

    def test_token_budget_enforcement(self) -> None:
        budget = TokenBudget(
            max_input_tokens=50_000,
            max_output_tokens=10_000,
            max_total_tokens=60_000,
            max_cost_usd=2.00,
        )
        normal_usage = TokenUsageMetric(
            input_tokens=40_000,
            output_tokens=5_000,
            total_tokens=45_000,
            cost_usd=0.50,
        )
        exceeded, reason = budget.check_exceeded(normal_usage)
        self.assertFalse(exceeded)
        self.assertIsNone(reason)

        over_usage = TokenUsageMetric(
            input_tokens=60_000,
            output_tokens=5_000,
            total_tokens=65_000,
            cost_usd=0.80,
        )
        exceeded, reason = budget.check_exceeded(over_usage)
        self.assertTrue(exceeded)
        self.assertIn("INPUT_TOKEN_LIMIT_EXCEEDED", reason)

    def test_agent_unified_architecture_compliance(self) -> None:
        self.assertEqual(self.agent.agent_definition.role_name, "TokenOptimizationAgent")
        self.assertEqual(len(self.agent.subagents), 5)
        self.assertEqual(len(self.agent.skills), 6)
        self.assertEqual(len(self.agent.rules), 5)
        self.assertEqual(len(self.agent.hooks), 2)

        # Verify all sub-agents have assigned skills and boolean lightweight_autonomy
        for _sub_id, sub in self.agent.subagents.items():
            self.assertGreater(len(sub.assigned_skills), 0)
            self.assertIsInstance(sub.lightweight_autonomy, bool)
            self.assertTrue(sub.lightweight_autonomy)

    def test_agent_delegation_contract_creation(self) -> None:
        contract = self.agent.create_delegation_contract(
            subagent_id="subagent:pipeline-audit:1.0.0",
            objective="Profile token consumption of forensic normalizer pipeline.",
            scope_in=["dual_pipeline/transcript_stages_core/synthesizers.py"],
            scope_out=["graph_writer"],
            allowed_tools=["TokenProfilerSkill"],
            deliverable="Token audit report with character-to-token ratio.",
            definition_of_done=["BPE measurement complete", "Cacheability certified"],
            budget={"max_turns": 3, "timeout_seconds": 30},
        )
        valid, errors = validate_delegation_contract(contract)
        self.assertTrue(valid, f"Contract validation failed: {errors}")

    def test_agent_optimize_request_7_step_lifecycle(self) -> None:
        # Prompt large enough to test prefix caching (>1024 tokens)
        system_rubric = "You are a forensic analyst for Apex Global Enterprises. " * 150
        user_transcript = "Speaker 1: Reviewing AWS migration timeline and milestones."

        directive, components = self.agent.optimize_request(
            system_prompt=system_rubric,
            user_content=user_transcript,
            task_type="stage01_forensic",
            model="gpt-5.6-luna",
        )

        self.assertIsInstance(directive, OptimizationDirective)
        self.assertEqual(directive.recommended_reasoning_effort, ReasoningEffortTier.MEDIUM)
        self.assertTrue(components.is_cacheable_prefix)
        self.assertGreater(components.estimated_prefix_tokens, 1000)

        # Build messages for OpenAI API call
        messages = components.build_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")


if __name__ == "__main__":
    unittest.main()
