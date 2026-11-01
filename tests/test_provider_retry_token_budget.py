"""Unit tests for token budget enforcement across all provider retry paths.

Verifies:
1. Provider retries enforce existing token budget via RunTokenUsageTracker.intercept_token_call.
2. Provider retries fail closed immediately with PipelineTokenBudgetExhaustedError when budget is exhausted.
3. No fallback or cascade to unbudgeted models occurs on budget exhaustion.
4. No receipts are written or persisted when failing closed.
"""
from __future__ import annotations

import glob
import os
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.cost_tracker import RunTokenUsageTracker, get_token_tracker
from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError


class TestProviderRetryTokenBudget(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = get_token_tracker()
        self.tracker.reset()
        self.tracker.max_tokens = None

    def tearDown(self) -> None:
        self.tracker.reset()
        self.tracker.max_tokens = None

    def test_completion_call_retry_token_budget_exhaustion(self) -> None:
        """Verifies OpenAI completion retry fails closed on token budget exhaustion."""
        from dual_pipeline.transcript_stages_core.completion_call import create_completion_with_effort

        # Initial call fails with transient HTTP 500
        mock_provider = MagicMock()
        mock_client = MagicMock()
        mock_provider.client = mock_client
        mock_provider._document_execution_receipt = {}

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt fails with transient error
                raise RuntimeError("500 Internal Server Error")
            # Should never reach second attempt if budget exhausted
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content="recovered"), finish_reason="stop")]
            return mock_resp

        mock_client.chat.completions.create.side_effect = side_effect

        # Set budget such that initial call passes but retry is exhausted
        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with patch("config.request_deadline.cooperative_sleep"):
            with self.assertRaises(PipelineTokenBudgetExhaustedError):
                create_completion_with_effort(
                    mock_provider,
                    target_effort="medium",
                    operation_name="Test Completion Call",
                    deadline_seconds=10.0,
                    messages=[{"role": "user", "content": "A" * 100}],
                    model="gpt-5.6-luna",
                )

        # Verified: only initial call was dispatched; retry was blocked before dispatch
        self.assertEqual(call_count, 1)

    def test_completion_call_effort_fallback_token_budget_exhaustion(self) -> None:
        """Verifies effort fallback retry fails closed when token budget is exhausted."""
        from dual_pipeline.transcript_stages_core.completion_call import create_completion_with_effort

        mock_provider = MagicMock()
        mock_client = MagicMock()
        mock_provider.client = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("Unsupported reasoning_effort xhigh")

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with self.assertRaises(PipelineTokenBudgetExhaustedError):
            create_completion_with_effort(
                mock_provider,
                target_effort="xhigh",
                operation_name="Test Effort Fallback",
                deadline_seconds=10.0,
                messages=[{"role": "user", "content": "A" * 100}],
                model="gpt-5.6-luna",
            )

    def test_gemini_retry_token_budget_exhaustion(self) -> None:
        """Verifies Google Gemini provider retry fails closed on token budget exhaustion."""
        import urllib.error
        from dual_pipeline.transcript_stages_core.external_synthesizer import call_gemini_generate_content

        call_count = 0

        def fake_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # HTTP 429 transient rate limit
                raise urllib.error.HTTPError("http://fake", 429, "Rate limited", {}, None)
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}'
            return mock_cm

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with patch("config.credentials.CredentialManager.get_credential", return_value="fake_key"):
            with patch("dual_pipeline.transcript_stages_core.external_synthesizer.urllib.request.urlopen", side_effect=fake_urlopen):
                with patch("config.request_deadline.cooperative_sleep"):
                    with self.assertRaises(PipelineTokenBudgetExhaustedError):
                        call_gemini_generate_content(
                            model="gemini-3.8-flash",
                            system_prompt="Test system",
                            user_content="A" * 100,
                            deadline_seconds=10.0,
                        )

        # First call ran, retry failed closed before second urlopen
        self.assertEqual(call_count, 1)

    def test_synthesizer_truncation_retry_forensic_token_budget_exhaustion(self) -> None:
        """Verifies _retry_forensic fails closed when token budget is exhausted."""
        from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer

        mock_client = MagicMock()
        synthesizer = LLMTranscriptSynthesizer(
            model="gpt-5.6-luna",
            d01_model="gpt-5.6-luna",
            d02_model="gpt-5.6-luna",
            d03_model="gpt-5.6-luna",
            _client=mock_client,
        )

        first_resp = MagicMock()
        first_resp.choices = [MagicMock(message=MagicMock(content="Part 1"), finish_reason="length")]

        synthesizer._create_completion_with_effort = MagicMock(return_value=first_resp)

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with self.assertRaises(PipelineTokenBudgetExhaustedError):
            synthesizer.synthesize_forensic("System prompt", "User content")

    def test_synthesizer_truncation_retry_recap_token_budget_exhaustion(self) -> None:
        """Verifies _retry_recap fails closed when token budget is exhausted."""
        from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer

        mock_client = MagicMock()
        synthesizer = LLMTranscriptSynthesizer(
            model="gpt-5.6-luna",
            d01_model="gpt-5.6-luna",
            d02_model="gpt-5.6-luna",
            d03_model="gpt-5.6-luna",
            _client=mock_client,
        )

        first_resp = MagicMock()
        first_resp.choices = [MagicMock(message=MagicMock(content="Part 1 narrative"), finish_reason="length")]

        synthesizer._create_completion_with_effort = MagicMock(return_value=first_resp)

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with self.assertRaises(PipelineTokenBudgetExhaustedError):
            synthesizer.synthesize_recap("System prompt", "User content")

    def test_recap_modular_dag_word_floor_retry_token_budget_exhaustion(self) -> None:
        """Verifies Pass 2A word floor auto-retry fails closed when token budget is exhausted."""
        from dual_pipeline.transcript_stages_core.recap_modular_dag import execute_pass_2a

        # First call produces only 10 words (below min_words 20)
        short_output = "Too short narrative that will fail word floor validation check here."

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", return_value=short_output):
            with self.assertRaises(PipelineTokenBudgetExhaustedError):
                execute_pass_2a(
                    model="gemini-3.8-flash",
                    user_content="Transcript content" * 20,
                    min_words=50,
                    max_retries=2,
                )

    def test_recap_judge_retry_token_budget_exhaustion_no_gemini_failover(self) -> None:
        """Verifies Recap Judge fails closed on retry budget exhaustion and does NOT fail over to Gemini."""
        from dual_pipeline.llm_judge_core.judges.recap_transport import generate_client_recap

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError("Transient network failure")
        mock_recorder = MagicMock()

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with patch("config.request_deadline.cooperative_sleep"):
            with patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content") as mock_gemini:
                with self.assertRaises(PipelineTokenBudgetExhaustedError):
                    generate_client_recap(
                        client=mock_client,
                        model="gpt-5.6-luna",
                        reasoning_effort="high",
                        system_prompt="System instructions",
                        user_content="User input content" * 10,
                        recorder=mock_recorder,
                    )
                # Fail-closed invariant: must NEVER fall over to Gemini
                mock_gemini.assert_not_called()

    def test_graph_judge_retry_token_budget_exhaustion_no_gemini_failover(self) -> None:
        """Verifies Graph Judge fails closed on retry budget exhaustion and does NOT fail over to Gemini."""
        from dual_pipeline.llm_judge_core.judges.graph_transport import generate_client_graph

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError("Transient network failure")
        mock_recorder = MagicMock()

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with patch("config.request_deadline.cooperative_sleep"):
            with patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content") as mock_gemini:
                with self.assertRaises(PipelineTokenBudgetExhaustedError):
                    generate_client_graph(
                        client=mock_client,
                        model="gpt-5.6-luna",
                        reasoning_effort="high",
                        system_prompt="System instructions",
                        user_content="User input content" * 10,
                        recorder=mock_recorder,
                        max_completion_tokens=4000,
                        assign_verdict=MagicMock(),
                        transient=lambda e: True,
                    )
                # Fail-closed invariant: must NEVER fall over to Gemini
                mock_gemini.assert_not_called()

    def test_ingestion_reasoning_schema_repair_retry_token_budget_exhaustion(self) -> None:
        """Verifies Ingestion Reasoning schema repair retry fails closed on token budget exhaustion."""
        from pathlib import Path
        from dual_pipeline.ingestion_reasoning_core.execution_runner import execute_provider_loop
        from dual_pipeline.ingestion_reasoning_core.models import (
            IngestionProviderRequest,
            IngestionProviderResponse,
        )
        from dual_pipeline.ingestion_reasoning_core.policy import load_ingestion_reasoning_policy

        mock_provider = MagicMock()
        # Primary call succeeds with invalid output requiring schema repair
        mock_provider.generate.return_value = IngestionProviderResponse(
            output="INVALID_SCHEMA_RESPONSE",
            response_digest="dig1",
            requested_model="gpt-5.6-luna",
            delivered_model="gpt-5.6-luna",
            input_tokens=100,
            output_tokens=50,
        )

        policy_path = Path(__file__).resolve().parent.parent / "requirements" / "ingestion-reasoning-policy.json"
        policy = load_ingestion_reasoning_policy(policy_path)
        model_policy = policy.model_for_tier("normal")
        primary_req = IngestionProviderRequest(purpose="PRIMARY", packet={"candidates": []}, output_schema={"type": "object"})

        self.tracker.max_tokens = 50
        self.tracker.total_tokens = 45

        with self.assertRaises(PipelineTokenBudgetExhaustedError):
            execute_provider_loop(
                policy=policy,
                provider=mock_provider,
                primary_request=primary_req,
                schema={"type": "object"},
                model_policy=model_policy,
                packet={"candidates": []},
                bundle={"source_id": "test_src", "candidates": []},
                resolutions=[],
                comparison={},
                use_subpacket=False,
                easy_cands=[],
                result={"provider_call_count": 0, "primary_call_count": 0, "repair_call_count": 0},
                recorder=MagicMock(),
                run_dir=Path("/tmp"),
            )

    def test_no_receipts_emitted_on_budget_exhaustion(self) -> None:
        """Verifies that no receipt files are created when failing closed due to token budget exhaustion."""
        receipt_patterns = ["reports/*_receipt.json", ".receipts/*.json"]
        pre_files = set()
        for p in receipt_patterns:
            pre_files.update(glob.glob(p))

        self.tracker.max_tokens = 10
        self.tracker.total_tokens = 10

        with self.assertRaises(PipelineTokenBudgetExhaustedError):
            self.tracker.intercept_token_call(1)

        post_files = set()
        for p in receipt_patterns:
            post_files.update(glob.glob(p))

        self.assertEqual(pre_files, post_files, "No new receipt files should be created on token budget exhaustion.")


if __name__ == "__main__":
    unittest.main()
