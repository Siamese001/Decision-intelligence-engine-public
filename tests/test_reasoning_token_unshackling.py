#!/usr/bin/env python3
"""
tests/test_reasoning_token_unshackling.py

Regression test suite validating unconstrained reasoning tokens, parameter immunity,
and model kwargs sanitization across frontier reasoning models.
Conforms strictly to Invariant 7 (Model-Neutral Artifact Naming).
"""
from __future__ import annotations

import unittest
from unittest import mock

from config.credentials import sanitize_model_call_kwargs
from dual_pipeline.model_registry import ModelTier, get_tier_spec
from dual_pipeline.prompts.config import (
    CANONICAL_PROMPT_CONFIG,
)
from dual_pipeline.token_optimization.schemas import TokenBudget, TokenUsageMetric


class TestReasoningTokenUnshackling(unittest.TestCase):
    """Verifies completion token expansion and parameter immunity for deep reasoning."""

    def test_prompt_config_expanded_token_ceilings(self) -> None:
        """Verifies judge and graph output token configurations allow deep test-time compute."""
        self.assertGreaterEqual(CANONICAL_PROMPT_CONFIG.judge.max_completion_tokens, 64000)
        self.assertGreaterEqual(CANONICAL_PROMPT_CONFIG.output3.max_tokens, 80000)

    def test_token_budget_unshackled_headroom(self) -> None:
        """Verifies TokenBudget accommodates deep reasoning without false-positive breaches."""
        budget = TokenBudget()
        self.assertGreaterEqual(budget.max_input_tokens, 500_000)
        self.assertGreaterEqual(budget.max_output_tokens, 100_000)
        self.assertGreaterEqual(budget.max_total_tokens, 600_000)

        # 85,000 output tokens should be completely valid within budget
        usage = TokenUsageMetric(
            input_tokens=150_000,
            output_tokens=85_000,
            total_tokens=235_000,
            cost_usd=8.50,
        )
        exceeded, reason = budget.check_exceeded(usage)
        self.assertFalse(exceeded)
        self.assertIsNone(reason)

    def test_parameter_immunity_and_effort_canonicalization(self) -> None:
        """Verifies parameter immunity strips forbidden kwargs and canonicalizes effort."""
        raw_kwargs = {
            "temperature": 0.8,
            "top_p": 0.95,
            "presence_penalty": 0.5,
            "frequency_penalty": 0.5,
            "reasoning_effort": "max",
            "max_tokens": 100000,
        }
        sanitized = sanitize_model_call_kwargs("gpt-5.6-sol", raw_kwargs)

        self.assertNotIn("temperature", sanitized)
        self.assertNotIn("top_p", sanitized)
        self.assertNotIn("presence_penalty", sanitized)
        self.assertNotIn("frequency_penalty", sanitized)
        self.assertNotIn("max_tokens", sanitized)
        self.assertEqual(sanitized.get("max_completion_tokens"), 100000)
        self.assertEqual(sanitized.get("reasoning_effort"), "xhigh")

    def test_registry_timeout_expansion_for_deep_compute(self) -> None:
        """Verifies tier specifications provide adequate timeouts for deep reasoning."""
        judge_spec = get_tier_spec(ModelTier.DIALECTICAL_JUDGE)
        self.assertGreaterEqual(judge_spec["timeout"], 300.0)

        frontier_high_spec = get_tier_spec(ModelTier.FRONTIER_HIGH_REASONING)
        self.assertGreaterEqual(frontier_high_spec["timeout"], 300.0)

        frontier_spec = get_tier_spec(ModelTier.FRONTIER_REASONING)
        self.assertGreaterEqual(frontier_spec["timeout"], 300.0)

    def test_calibrate_reasoning_effort_tiers(self) -> None:
        """Verifies deep synthesis and adversarial tasks are calibrated to HIGH / XHIGH effort."""
        from dual_pipeline.token_optimization.agent import TokenOptimizationAgent
        from dual_pipeline.token_optimization.schemas import ReasoningEffortTier

        agent = TokenOptimizationAgent()
        self.assertEqual(agent.calibrate_reasoning_effort("recap"), ReasoningEffortTier.HIGH)
        self.assertEqual(agent.calibrate_reasoning_effort("graph_extract"), ReasoningEffortTier.HIGH)
        self.assertEqual(agent.calibrate_reasoning_effort("stage02"), ReasoningEffortTier.HIGH)
        self.assertEqual(agent.calibrate_reasoning_effort("stage03"), ReasoningEffortTier.HIGH)
        self.assertEqual(agent.calibrate_reasoning_effort("adversarial"), ReasoningEffortTier.XHIGH)

    def test_synthesizer_retries_preserve_reasoning_effort(self) -> None:
        """Verifies synthesizers preserve reasoning effort on retries without demotion."""
        from config.credentials import OpenAICredentials
        from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer
        mock_client = mock.MagicMock()
        stub_creds = OpenAICredentials(
            api_key="sk-test",
            is_mock=False,
            model="gpt-6-luna",
            reasoning_effort="high",
            masked_key="[TEST_STUB]",
            source="test_injection",
        )

        synth = LLMTranscriptSynthesizer(
            model="gpt-6-luna",
            reasoning_effort="high",
            _client=mock_client,
            _creds=stub_creds,
        )
        with mock.patch.object(synth, "_create_completion_with_effort") as mock_create:
            mock_choice = mock.MagicMock()
            mock_choice.message.content = "Normal output"
            mock_choice.finish_reason = "stop"
            mock_resp = mock.MagicMock()
            mock_resp.choices = [mock_choice]
            mock_create.return_value = mock_resp

            synth._retry_forensic([{"role": "user", "content": "test"}], "high", 500)
            mock_create.assert_called_with(
                target_effort="high",
                operation_name="L2-Stage01 Forensic Normalization (Retry)",
                deadline_seconds=600.0,
                model="gpt-6-luna",
                messages=mock.ANY,
                max_completion_tokens=64000,
            )


if __name__ == "__main__":
    unittest.main()
