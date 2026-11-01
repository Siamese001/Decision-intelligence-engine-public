"""tests/end_to_end/test_wave2_telemetry_accounting_e2e.py

End-to-end test suite for Wave 2 LLM telemetry and comprehensive judge cost accounting.
Verifies that:
1. LLM Judge invocations (graph, forensic, recap, leverage) record prompt/completion tokens and cost.
2. Call history is persisted in RunCostTracker.to_dict() and contains model and cost details.
3. Gemini synthesis call tracking properly records usage and metrics from usageMetadata.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.cost_tracker import (
    get_cost_tracker,
    get_token_tracker,
    reset_trackers,
)
from dual_pipeline.llm_judge_core.telemetry import record_judge_llm_usage


class Wave2TelemetryAccountingE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        reset_trackers()

    def tearDown(self) -> None:
        reset_trackers()

    def test_judge_telemetry_recording_e2e(self) -> None:
        """Every judge invocation correctly records tokens, cost, and updates call_history."""
        tracker = get_cost_tracker(max_cost_usd=25.0)
        token_tracker = get_token_tracker()

        mock_resp = MagicMock()
        mock_resp.usage.prompt_tokens = 4500
        mock_resp.usage.completion_tokens = 1200
        mock_resp.usage.prompt_tokens_details.cached_tokens = 2000
        mock_resp.usage.prompt_tokens_details.cache_creation_input_tokens = 0
        mock_resp.usage.completion_tokens_details.reasoning_tokens = 800

        record_judge_llm_usage(
            resp=mock_resp,
            model="gpt-6-astra",
            duration_seconds=3.5,
            operation_name="L4-Judge Graph Extraction Evaluation",
        )

        cost_dict = tracker.to_dict()
        token_dict = token_tracker.to_dict()

        self.assertEqual(cost_dict["call_count"], 1)
        self.assertGreater(cost_dict["total_cost_usd"], 0.0)
        self.assertEqual(len(cost_dict["call_history"]), 1)
        self.assertEqual(cost_dict["call_history"][0]["model"], "gpt-6-astra")
        self.assertEqual(cost_dict["call_history"][0]["prompt_tokens"], 4500)
        self.assertEqual(cost_dict["call_history"][0]["completion_tokens"], 1200)

        self.assertEqual(token_dict["total_prompt_tokens"], 4500)
        self.assertEqual(token_dict["total_completion_tokens"], 1200)
        self.assertEqual(token_dict["cached_prompt_tokens"], 2000)

    def test_gemini_usage_metadata_recording_e2e(self) -> None:
        """Gemini responses with usageMetadata properly record tokens and cost."""
        tracker = get_cost_tracker(max_cost_usd=25.0)
        token_tracker = get_token_tracker()

        # Simulate Gemini response payload with usageMetadata
        resp_data = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "{\"key\": \"val\"}"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 8500,
                "candidatesTokenCount": 2100,
                "cachedContentTokenCount": 4000,
                "totalTokenCount": 10600,
            },
        }

        # Mock urllib.request to return resp_data
        import json
        mock_http_resp = MagicMock()
        mock_http_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_http_resp.__enter__.return_value = mock_http_resp

        with (
            patch("urllib.request.urlopen", return_value=mock_http_resp),
            patch("dual_pipeline.credentials.CredentialManager.get_credential", return_value="fake_api_key"),
        ):
            from dual_pipeline.transcript_stages_core.external_synthesizer import (
                call_gemini_generate_content,
            )
            text = call_gemini_generate_content(
                model="gemini-2.5-pro",
                system_prompt="You are a helpful assistant",
                user_content="Analyze this transcript segment",
                deadline_seconds=30.0,
            )
            self.assertIn("key", text)

        cost_dict = tracker.to_dict()
        token_dict = token_tracker.to_dict()

        self.assertEqual(cost_dict["call_count"], 1)
        self.assertEqual(cost_dict["call_history"][0]["model"], "gemini-2.5-pro")
        self.assertEqual(cost_dict["call_history"][0]["prompt_tokens"], 8500)
        self.assertEqual(cost_dict["call_history"][0]["completion_tokens"], 2100)
        self.assertEqual(token_dict["total_prompt_tokens"], 8500)
        self.assertEqual(token_dict["total_completion_tokens"], 2100)


if __name__ == "__main__":
    unittest.main()
