"""
tests/test_bounded_generation_deadline.py

Unit and adversarial tests verifying:
1. One monotonic deadline across initial generation, backoff, and retries (no fresh clocks).
2. Worker threads in D02 ThreadPoolExecutor inherit the active request deadline ContextVar.
3. Provider selection is strictly preserved without cross-provider fallback on failure.
4. Zero receipt files are emitted or written to disk.
"""
from __future__ import annotations

import glob
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config.request_deadline import (
    _CURRENT,
    Deadline,
    RequestDeadlineExceeded,
    check_deadline,
    deadline_scope,
    remaining_timeout,
)
from dual_pipeline.token_optimization import PromptCacheOptimizer
from dual_pipeline.transcript_stages_core.call_ledger import D02CallLedger
from dual_pipeline.transcript_stages_core.completion_call import create_completion_with_effort
from dual_pipeline.transcript_stages_core.recap_modular_dag import execute_d02_3pass_dag
from dual_pipeline.transcript_stages_core.synthesizers import (
    DEFAULT_RETRY_DEADLINE_SEC,
    DEFAULT_SYNTHESIS_DEADLINE_SEC,
    LLMTranscriptSynthesizer,
)


class BoundedGenerationDeadlineTests(unittest.TestCase):
    """Verifies monotonic single-deadline budgets and strict provider preservation."""

    def test_backoff_exhausts_monotonic_deadline_without_fresh_clock(self) -> None:
        """Transient error backoff consumes the parent deadline; attempt 2 cannot start if budget expires."""
        mock_provider = MagicMock()
        mock_provider.model = "gpt-6-luna"
        mock_client = MagicMock()
        mock_client.with_options.return_value = mock_client
        mock_provider.client = mock_client

        mock_create = mock_client.chat.completions.create
        mock_create.side_effect = Exception("503 Service Unavailable")

        # Give a 1.0s deadline. Backoff is (2**0)*3 = 3.0s.
        # Attempt 1 executes, and cooperative_sleep consumes the remaining budget and aborts.
        with self.assertRaises((TimeoutError, RequestDeadlineExceeded)):
            create_completion_with_effort(
                provider=mock_provider,
                target_effort="high",
                operation_name="Test Monotonic Backoff",
                deadline_seconds=1.0,
                model="gpt-6-luna",
                messages=[{"role": "user", "content": "test"}],
            )

        # Attempt 1 failed with 503, backoff consumed remaining deadline. Attempt 2 never executed.
        self.assertEqual(mock_create.call_count, 1)

    def test_retry_forensic_shares_parent_deadline(self) -> None:
        """Forensic token exhaustion retry cannot purchase a new clock if parent deadline expired."""
        mock_client = MagicMock()

        # Call 1: returns length truncation
        response_exhausted = MagicMock()
        response_exhausted.choices = [MagicMock()]
        response_exhausted.choices[0].message.content = "Truncated text..."
        response_exhausted.choices[0].finish_reason = "length"

        call_times = []

        def slow_first_completion(*args, **kwargs):
            call_times.append(time.monotonic())
            # Consume 0.12s out of a 0.20s budget
            time.sleep(0.12)
            return response_exhausted

        mock_client.chat.completions.create.side_effect = slow_first_completion

        synth = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth.client = mock_client
        synth.model = "gpt-6-luna"
        synth.d01_model = "gpt-6-luna"
        synth.reasoning_effort = "high"
        synth.cache_optimizer = PromptCacheOptimizer()

        # Under a 0.15s deadline scope, Call 1 finishes at ~0.12s.
        # Before retry Call 2 can execute, sleeping 0.05s pushes past the 0.15s parent deadline.
        # check_deadline() in _retry_forensic must raise RequestDeadlineExceeded and block Call 2.
        with self.assertRaises((TimeoutError, RequestDeadlineExceeded)):
            with deadline_scope(0.15):
                # Hook _retry_forensic to simulate elapsed time exceeding parent deadline
                original_retry = synth._retry_forensic

                def delayed_retry(*args, **kwargs):
                    time.sleep(0.05)
                    return original_retry(*args, **kwargs)

                synth._retry_forensic = delayed_retry
                synth.synthesize_forensic(
                    system_prompt="Stage 01 system",
                    user_content="<raw_transcript>User dialogue</raw_transcript>",
                )

        # Call 1 executed, but Call 2 was blocked by check_deadline() in _retry_forensic
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    def test_threadpool_workers_inherit_active_deadline(self) -> None:
        """ThreadPoolExecutor workers in D02 modular DAG inherit the parent ContextVar deadline."""
        observed_deadlines: dict[str, float] = {}

        def mock_pass_2a(model, user_content, min_words, max_retries, deadline_sec, ledger, **kwargs):
            current = _CURRENT.get()
            if current is not None:
                observed_deadlines["2A"] = current.remaining()
            return "word " * 850

        def mock_pass_2b(model, user_content, deadline_sec, ledger, **kwargs):
            current = _CURRENT.get()
            if current is not None:
                observed_deadlines["2B"] = current.remaining()
            return "| Action | Owner |\n|---|---|\n| Task | Dan |"

        def mock_pass_2c(model, user_content, min_words, max_retries, deadline_sec, ledger, **kwargs):
            current = _CURRENT.get()
            if current is not None:
                observed_deadlines["2C"] = current.remaining()
            return "word " * 650

        with (
            patch("dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2a", side_effect=mock_pass_2a),
            patch("dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2b", side_effect=mock_pass_2b),
            patch("dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2c", side_effect=mock_pass_2c),
            deadline_scope(10.0),
        ):
            execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt="Test sys",
                user_content="Test user content",
                enforce_coherence=False,
            )

        # Verify all 3 workers inherited an active deadline <= parent budget of 10.0s
        self.assertIn("2A", observed_deadlines)
        self.assertIn("2B", observed_deadlines)
        self.assertIn("2C", observed_deadlines)
        self.assertLessEqual(observed_deadlines["2A"], 10.0)
        self.assertLessEqual(observed_deadlines["2B"], 10.0)
        self.assertLessEqual(observed_deadlines["2C"], 10.0)

    def test_recap_word_floor_retry_shares_pass_deadline(self) -> None:
        """Word floor retry in Pass 2A shares the single pass deadline clock without reset."""
        from dual_pipeline.transcript_stages_core.recap_modular_dag import execute_pass_2a

        call_count = 0

        def slow_short_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            time.sleep(0.16)
            return "word " * 100  # Below 800 words floor

        with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", side_effect=slow_short_call):
            with self.assertRaises((TimeoutError, RequestDeadlineExceeded, Exception)):
                with deadline_scope(0.15):
                    execute_pass_2a(
                        model="gemini-3.8-flash",
                        user_content="Test snippet",
                        min_words=800,
                        max_retries=2,
                        deadline_seconds=0.15,
                    )

        # Attempt 1 took ~0.16s (> 0.15s deadline), check_deadline() aborted before attempt 2 could succeed
        self.assertEqual(call_count, 1)

    def test_stage03_provider_selection_strictly_preserved(self) -> None:
        """Stage 03 graph synthesis strictly preserves provider selection without cross-provider fallback."""
        mock_client = MagicMock()
        mock_create = mock_client.chat.completions.create
        mock_create.side_effect = Exception("429 rate limit exceeded")

        synth_openai = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth_openai.client = mock_client
        synth_openai.model = "gpt-6-luna"
        synth_openai.d03_model = "gpt-6-luna"
        synth_openai.reasoning_effort = "high"
        synth_openai.cache_optimizer = PromptCacheOptimizer()

        with patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content") as mock_gemini:
            with self.assertRaises(RuntimeError) as ctx:
                synth_openai.synthesize_graph(
                    system_prompt="Stage 03 schema",
                    user_content="<raw_transcript>User dialogue</raw_transcript>",
                )
            self.assertIn("LLM graph extraction failed", str(ctx.exception))
            # Gemini MUST NOT be called as fallback
            mock_gemini.assert_not_called()

        # Gemini model failure does not call OpenAI
        synth_gemini = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth_gemini.client = mock_client
        synth_gemini.model = "gemini-3.8-flash"
        synth_gemini.d03_model = "gemini-3.8-flash"
        synth_gemini.reasoning_effort = "high"
        synth_gemini.cache_optimizer = PromptCacheOptimizer()

        with patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content", side_effect=RuntimeError("Gemini 503")):
            with self.assertRaises(RuntimeError) as ctx:
                synth_gemini.synthesize_graph(
                    system_prompt="Stage 03 schema",
                    user_content="<raw_transcript>User dialogue</raw_transcript>",
                )
            self.assertIn("Gemini 503", str(ctx.exception))
            mock_create.assert_called_once()  # Only from synth_openai earlier, not from synth_gemini

    def test_provider_selection_strictly_preserved_on_failure(self) -> None:
        """Policy/model-assigned providers must never execute cross-provider fallback or swap."""
        # 1. OpenAI failure must NEVER failover to Gemini
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("429 insufficient_quota: rate limit exceeded")

        synth_openai = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth_openai.client = mock_client
        synth_openai.model = "gpt-6-luna"
        synth_openai.d01_model = "gpt-6-luna"
        synth_openai.reasoning_effort = "high"
        synth_openai.cache_optimizer = PromptCacheOptimizer()

        with patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content") as mock_gemini:
            with self.assertRaises(RuntimeError) as ctx:
                synth_openai.synthesize_forensic(
                    system_prompt="Stage 01 system",
                    user_content="<raw_transcript>User dialogue</raw_transcript>",
                )
            self.assertIn("OpenAI quota/spend limit encountered", str(ctx.exception))
            # Gemini MUST NOT be called as fallback
            mock_gemini.assert_not_called()

        # 2. Gemini failure must NEVER failover to OpenAI
        mock_openai_client = MagicMock()
        synth_gemini = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth_gemini.client = mock_openai_client
        synth_gemini.model = "gemini-3.8-flash"
        synth_gemini.d01_model = "gemini-3.8-flash"
        synth_gemini.reasoning_effort = "high"
        synth_gemini.cache_optimizer = PromptCacheOptimizer()

        with patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content", side_effect=RuntimeError("Gemini service down")):
            with self.assertRaises(RuntimeError) as ctx:
                synth_gemini.synthesize_forensic(
                    system_prompt="Stage 01 system",
                    user_content="<raw_transcript>User dialogue</raw_transcript>",
                )
            self.assertIn("Gemini service down", str(ctx.exception))
            # OpenAI completion MUST NOT be called as fallback
            mock_openai_client.chat.completions.create.assert_not_called()

    def test_no_receipts_emitted_during_synthesis(self) -> None:
        """Synthesis passes must not generate or write receipt files to disk."""
        reports_dir = Path("reports")
        initial_reports = set(reports_dir.glob("**/*")) if reports_dir.exists() else set()

        mock_client = MagicMock()
        response_success = MagicMock()
        response_success.choices = [MagicMock()]
        response_success.choices[0].message.content = "# Forensic Transcript\n\n**Alice:** Verified."
        response_success.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = response_success

        synth = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth.client = mock_client
        synth.model = "gpt-6-luna"
        synth.d01_model = "gpt-6-luna"
        synth.reasoning_effort = "high"
        synth.cache_optimizer = PromptCacheOptimizer()

        synth.synthesize_forensic(
            system_prompt="Stage 01 prompt",
            user_content="<raw_transcript>Alice: Verified.</raw_transcript>",
        )

        after_reports = set(reports_dir.glob("**/*")) if reports_dir.exists() else set()
        new_files = after_reports - initial_reports
        self.assertEqual(len(new_files), 0, f"Unexpected receipt files created: {new_files}")


if __name__ == "__main__":
    unittest.main()
