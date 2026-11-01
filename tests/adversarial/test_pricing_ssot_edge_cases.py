"""
tests/adversarial/test_pricing_ssot_edge_cases.py

Adversarial test suite for Model Pricing SSOT, Fallback Boundaries, and Batch Flag Integrity (Wave 1: Tier 0).
Proves:
1. Malformed and aliased model strings resolve deterministically without crashing.
2. Unknown model IDs resolve to DEFAULT_SOL_PRICING instead of expensive Astra pricing.
3. Explicit is_batch=True is never masked or overwritten by falsy kwargs.
4. Synthesizer fallback sanitizes kwargs_for_api so forbidden kwargs do not reach the client.
5. Signal triage returns clean string content across varied completion formats.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dual_pipeline.cost_tracker import RunCostTracker
from dual_pipeline.token_optimization.schemas import (
    DEFAULT_SOL_PRICING,
    get_model_pricing,
)
from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer


class TestPricingSSOTEdgeCases(unittest.TestCase):
    def test_adversarial_malformed_model_string_resolution(self):
        """Verify weird casings, whitespace, and substrings resolve cleanly to correct pricing."""
        test_cases = [
            ("  GEMINI-3.8-FLASH  ", 0.10, 0.40),
            ("gemini_3.8_flash", 0.10, 0.40),
            ("gemini-3.5-flash-lite", 0.075, 0.30),
            ("GEMINI-2.5-PRO", 1.25, 5.00),
            ("gpt-6-luna", 0.10, 0.50),
            ("  gpt-5.6-luna  ", 0.20, 1.20),
            ("gpt-6.1-sol", 2.00, 10.00),
            ("gpt-6-astra", 10.00, 50.00),
        ]
        for model_name, expected_in, expected_out in test_cases:
            pricing = get_model_pricing(model_name)
            self.assertAlmostEqual(
                pricing.uncached_input_usd_per_million,
                expected_in,
                places=3,
                msg=f"Failed input rate for {model_name}",
            )
            self.assertAlmostEqual(
                pricing.output_reasoning_usd_per_million,
                expected_out,
                places=3,
                msg=f"Failed output rate for {model_name}",
            )

    def test_adversarial_unknown_model_fallback_safeguard(self):
        """Unknown or rogue model names must fall back to standard SOL pricing, not frontier ASTRA."""
        unknown_models = [
            "rogue-ai-v99",
            "unknown-claude-fork",
            "completely_unrecognized_vendor_model",
            "",
            "None",
        ]
        for m in unknown_models:
            pricing = get_model_pricing(m)
            self.assertEqual(pricing.uncached_input_usd_per_million, DEFAULT_SOL_PRICING.uncached_input_usd_per_million)
            self.assertEqual(pricing.output_reasoning_usd_per_million, DEFAULT_SOL_PRICING.output_reasoning_usd_per_million)

    def test_adversarial_batch_flag_tampering_defense(self):
        """Verify that kwargs={'batch': False} does NOT override explicit is_batch=True."""
        tracker = RunCostTracker(max_cost_usd=100.0)
        cost = tracker.record_call(
            model="gpt-6.1-sol",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            is_batch=True,
            batch=False,  # Conflict test: kwargs should not disable explicit is_batch=True
        )
        # 50% discount on $2.00 is $1.00
        self.assertAlmostEqual(cost, 1.00, places=2)
        self.assertTrue(tracker.call_history[0]["batch"])

    def test_adversarial_synthesizer_fallback_kwargs_sanitization(self):
        """Verify that when reasoning_effort fallback triggers, internal kwargs like batch are stripped."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.prompt_tokens_details = None
        mock_response.usage.completion_tokens_details = None
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Forensic normalized text"
        mock_response.choices[0].finish_reason = "stop"

        # First call fails with reasoning_effort rejection, second call succeeds
        first_call_error = Exception("Invalid reasoning_effort 'max' for model")
        mock_client.chat.completions.create.side_effect = [first_call_error, mock_response]

        synth = LLMTranscriptSynthesizer(_client=mock_client)
        # Dispatch completion with a batch kwarg
        resp = synth._create_completion_with_effort(
            target_effort="max",
            operation_name="Adversarial Fallback Test",
            deadline_seconds=10.0,
            model="gpt-6-astra",
            messages=[{"role": "user", "content": "test"}],
            batch=True,
        )
        self.assertEqual(resp, mock_response)
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)
        # Check that batch was popped from kwargs_for_api on both calls
        for call_args in mock_client.chat.completions.create.call_args_list:
            self.assertNotIn("batch", call_args.kwargs)

    def test_adversarial_signal_triage_string_integrity(self):
        """Verify synthesize_signal_triage extracts string without raising on Mock ChatCompletion."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.usage.prompt_tokens = 50
        mock_response.usage.completion_tokens = 10
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "PROCEED"

        mock_client.chat.completions.create.return_value = mock_response
        synth = LLMTranscriptSynthesizer(_client=mock_client)

        result = synth.synthesize_signal_triage("Alice: Let's discuss architecture boundaries.")
        self.assertIsInstance(result, str)
        self.assertEqual(result, "PROCEED")
        self.assertNotIn("NO_SIGNAL", result.upper())


if __name__ == "__main__":
    unittest.main()
