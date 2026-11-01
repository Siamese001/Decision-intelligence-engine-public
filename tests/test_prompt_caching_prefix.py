"""
tests/test_prompt_caching_prefix.py

Unit test suite verifying Wave 2 Prompt Prefix Stabilization & OpenAI Prompt Caching Engine
for gpt-5.6-luna (reasoning_effort="high") and gpt-5.6-luna.
"""
from __future__ import annotations

import unittest
from unittest import mock

from dual_pipeline.token_optimization import (
    PromptCacheOptimizer,
    TokenProfiler,
)
from dual_pipeline.transcript_stages import LLMTranscriptSynthesizer


class TestPromptCachingPrefix(unittest.TestCase):
    def setUp(self) -> None:
        self.profiler = TokenProfiler(default_model="gpt-5.6-luna")
        self.cache_optimizer = PromptCacheOptimizer(profiler=self.profiler)

    def test_stabilize_system_prefix_segregates_dynamic_elements(self) -> None:
        prompt_with_dynamic = (
            "You are a Principal Forensic Analyst for Apex Global Enterprises.\n"
            "Current Date/Time: 2026-09-12T21:00:00Z\n"
            "Execution Run ID: run_abc_123\n"
            "Follow all governance guidelines for ASR normalization."
        )
        static_prefix, dynamic_tail, tokens, is_cacheable = self.cache_optimizer.stabilize_system_prefix(
            prompt_with_dynamic, min_prefix_tokens=5
        )

        # Dynamic timestamps/run IDs must be removed from the static prefix
        self.assertNotIn("2026-09-12T21:00:00Z", static_prefix)
        self.assertNotIn("run_abc_123", static_prefix)
        self.assertIn("Current Date/Time", dynamic_tail)
        self.assertIn("run_abc_123", dynamic_tail)
        self.assertIn("Principal Forensic Analyst", static_prefix)

    def test_prefix_hash_invariance(self) -> None:
        rubric = "You are an executive meeting synthesizer. Adhere to W2 semantic spine. " * 80
        hash1 = self.cache_optimizer.compute_prefix_hash(rubric)

        # Minor trailing whitespace or linefeed differences must normalize to identical hash
        rubric_with_spaces = rubric + "   \n\n"
        hash2 = self.cache_optimizer.compute_prefix_hash(rubric_with_spaces)

        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)

    def test_build_cacheable_messages_structure(self) -> None:
        system_rubric = "System rubric instructions for executive recap synthesis. " * 50
        user_input = "<raw_transcript>Speaker 1: Reviewing Q3 cloud budget.</raw_transcript>"

        messages, components = self.cache_optimizer.build_cacheable_messages(system_rubric, user_input)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("System rubric instructions", messages[0]["content"])
        self.assertIn("Speaker 1: Reviewing Q3 cloud budget.", messages[1]["content"])
        self.assertGreater(components.estimated_prefix_tokens, 200)

    def test_estimate_cache_savings_calculation(self) -> None:
        # 100k input tokens, 75k cached under Luna pricing ($5/1M uncached, $2.50/1M cached)
        from dual_pipeline.token_optimization.schemas import AstraModelPricing
        test_pricing = AstraModelPricing(
            model_id="test-model",
            uncached_input_usd_per_million=5.00,
            cached_input_usd_per_million=2.50,
            output_reasoning_usd_per_million=10.00
        )
        savings = self.cache_optimizer.estimate_cache_savings(
            total_input_tokens=100_000,
            cached_tokens=75_000,
            pricing=test_pricing,
        )

        self.assertEqual(savings["cached_tokens"], 75_000)
        self.assertEqual(savings["uncached_tokens"], 25_000)
        self.assertEqual(savings["cache_hit_ratio"], 0.75)
        # Cost without caching: 100k * $5 / 1M = $0.50
        # Cost with caching: (25k * $5 / 1M) + (75k * $2.50 / 1M) = $0.125 + $0.1875 = $0.3125
        # Saved: $0.1875 (37.5% savings on input)
        self.assertAlmostEqual(savings["cost_without_caching_usd"], 0.50, places=4)
        self.assertAlmostEqual(savings["cost_with_caching_usd"], 0.3125, places=4)
        self.assertAlmostEqual(savings["saved_usd"], 0.1875, places=4)
        self.assertEqual(savings["savings_ratio"], 0.375)

    def test_synthesizer_gpt56_luna_high_prefix_stabilization(self) -> None:
        mock_client = mock.MagicMock()
        mock_response = mock.MagicMock()
        mock_response.choices = [
            mock.MagicMock(
                message=mock.MagicMock(content="# Executive Recap\n\nDate: 09/12/2026\n\nContent..."),
                finish_reason="stop",
            )
        ]
        mock_response.usage = mock.MagicMock(
            prompt_tokens=15000,
            completion_tokens=1500,
            total_tokens=16500,
            prompt_tokens_details=mock.MagicMock(cached_tokens=12000),
            completion_tokens_details=mock.MagicMock(reasoning_tokens=800),
        )
        mock_client.chat.completions.create.return_value = mock_response

        from dual_pipeline.credentials import OpenAICredentials
        test_creds = OpenAICredentials(
            api_key="sk-mock-test",
            is_mock=False,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            masked_key="sk-mock...test",
            source="test_injection",
        )

        synth = LLMTranscriptSynthesizer(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            _client=mock_client,
            _creds=test_creds,
        )
        self.assertEqual(synth.model, "gpt-5.6-luna")
        self.assertEqual(synth.reasoning_effort, "high")

        system_prompt = "You are an executive recap synthesizer. " * 80
        user_content = "Transcript dialogue turns here."

        out = synth.synthesize_recap(system_prompt, user_content)
        self.assertIn("Executive Recap", out)

        # Check call kwargs dispatched to OpenAI
        mock_client.chat.completions.create.assert_called_once()
        kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["reasoning_effort"], "high")
        self.assertEqual(kwargs["max_completion_tokens"], 24000)

        # Messages must be cache-stabilized into 2 messages (system and user)
        sent_messages = kwargs["messages"]
        self.assertEqual(len(sent_messages), 2)
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertEqual(sent_messages[1]["role"], "user")


if __name__ == "__main__":
    unittest.main()
