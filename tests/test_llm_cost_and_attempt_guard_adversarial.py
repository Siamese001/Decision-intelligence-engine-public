"""tests/test_llm_cost_and_attempt_guard_adversarial.py

Adversarial and edge-case test suite for Wave 2 LLM cost tracking, budget guards, and attempt clamping.
Verifies that:
1. RunCostTracker raises BudgetExhaustionError when budget ceiling ($15.00 default) is exceeded.
2. Synchronous interactive calls are not incorrectly discounted under batch pricing.
3. Attempt clamping enforces a hard maximum of 3 attempts even if caller requests excessive retries.
4. Reasoning effort fallback strips batch kwarg and tracks cost without TypeError.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.cost_tracker import (
    BudgetExhaustionError,
    RunCostTracker,
    reset_trackers,
)
from dual_pipeline.transcript_pipeline_core.context_builder import (
    initialize_remediation_state_machine,
)


class LLMCostAndAttemptGuardAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_trackers()

    def tearDown(self) -> None:
        reset_trackers()

    def test_budget_exhaustion_raises_fail_fast(self) -> None:
        """Exceeding max_cost_usd ceiling immediately raises BudgetExhaustionError."""
        tracker = RunCostTracker(max_cost_usd=5.0)
        # Record a large frontier model call that exceeds $5.00
        # gpt-6-sol output tokens are priced at $10.00 / 1M tokens ($0.00001 / token)
        # 1,000,000 output tokens = $10.00 > $5.00
        with self.assertRaises(BudgetExhaustionError) as ctx:
            tracker.record_call(
                model="gpt-6-sol",
                prompt_tokens=10000,
                completion_tokens=1000000,
                batch=False,
            )
        self.assertIn("Cumulative LLM expenditure", str(ctx.exception))

    def test_synchronous_call_is_not_billed_with_batch_discount(self) -> None:
        """Synchronous calls must be billed at standard non-batch rates."""
        tracker = RunCostTracker(max_cost_usd=50.0)
        cost_sync = tracker.record_call(
            model="gpt-6-astra",
            prompt_tokens=100000,
            completion_tokens=50000,
            batch=False,
        )
        tracker_batch = RunCostTracker(max_cost_usd=50.0)
        cost_batch = tracker_batch.record_call(
            model="gpt-6-astra",
            prompt_tokens=100000,
            completion_tokens=50000,
            batch=True,
        )
        # Non-batch cost must be strictly greater than batch cost (typically 2x)
        self.assertGreater(cost_sync, cost_batch)
        self.assertAlmostEqual(cost_sync, cost_batch * 2.0, places=2)

    def test_attempt_clamping_caps_at_three_attempts(self) -> None:
        """Excessive max_judge_retries values (e.g. 10) must be clamped to at most 3 attempts."""
        metadata: dict = {"meeting_name": "Test_Session"}
        sm = initialize_remediation_state_machine(
            metadata=metadata,
            output_dir="/tmp/test_output",
            max_judge_retries=10,
            is_pre_resolved_input=False,
            is_notes_mode=False,
            transcript_text="",
        )
        self.assertEqual(sm.max_attempts, 3)

    def test_reasoning_effort_fallback_execution(self) -> None:
        """When xhigh effort fails, fallback executes with effort=high and strips batch parameter."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.usage.prompt_tokens = 500
        mock_resp.usage.completion_tokens = 200
        mock_resp.usage.prompt_tokens_details = None
        mock_resp.usage.completion_tokens_details = None
        mock_client.chat.completions.create.side_effect = [
            Exception("Unsupported value 'xhigh' for reasoning_effort"),
            mock_resp,
        ]

        from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer
        synth = LLMTranscriptSynthesizer(model="gpt-6-astra", reasoning_effort="high", _client=mock_client)
        with patch("dual_pipeline.cost_tracker.get_cost_tracker") as mock_get_cost:
            tracker_mock = MagicMock()
            mock_get_cost.return_value = tracker_mock
            resp = synth._create_completion_with_effort(
                target_effort="xhigh",
                model="gpt-6-astra",
                messages=[{"role": "user", "content": "test"}],
                batch=True,  # Test that batch kwarg is safely handled
            )
            self.assertEqual(resp, mock_resp)
            # Verify second create call was called with reasoning_effort='high' and no 'batch' kwarg
            self.assertEqual(mock_client.chat.completions.create.call_count, 2)
            second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
            self.assertEqual(second_call_kwargs["reasoning_effort"], "high")
            self.assertNotIn("batch", second_call_kwargs)


if __name__ == "__main__":
    unittest.main()
