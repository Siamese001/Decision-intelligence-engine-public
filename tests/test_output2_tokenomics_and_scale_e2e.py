"""
tests/test_output2_tokenomics_and_scale_e2e.py

End-to-end and adversarial test suite for Wave 6:
GPT-6 Astra Tokenomics, Anti-Truncation Headroom & Dynamic Word Scaling.
Verifies:
1. Output2Config & JudgeExecutionConfig token bounds and ceilings.
2. Marathon meeting (154m) word count scaling >= 1,800 core words.
3. Anti-truncation continuation circuit breaker on finish_reason == "length".
4. Profiler preservation of up to 60,000 tokens without truncation.
5. Seam stitching edge cases (line overlap, punctuation, whitespace).
"""
import unittest
from unittest.mock import MagicMock

from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
from dual_pipeline.prompts.contracts.output2_contract import OUTPUT2_CONTRACT
from dual_pipeline.token_optimization.profiler import TokenProfiler
from dual_pipeline.transcript_qa_core.gate2 import _check_word_count_bounds, run_qa_gate_2
from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer


class TestOutput2TokenomicsAndScaleE2E(unittest.TestCase):
    """E2E and adversarial verification of Output 2 tokenomics and anti-truncation scaling."""

    def test_output2_config_and_contract_bounds(self) -> None:
        """Verify expanded token limits and word count ceilings for Output 2 and judges."""
        cfg = CANONICAL_PROMPT_CONFIG
        self.assertEqual(cfg.output2.max_tokens, 60000, "Output 2 max_tokens must be 60,000")
        self.assertEqual(cfg.output2.max_word_count, 5000, "Output 2 max_word_count must be 5,000")
        self.assertEqual(cfg.judge.max_input_tokens, 150000, "Judge max_input_tokens must be 150,000")
        self.assertEqual(OUTPUT2_CONTRACT.max_word_count, 5000, "OUTPUT2_CONTRACT max_word_count must be 5,000")

    def test_marathon_meeting_word_count_scaling(self) -> None:
        """Verify that a 154-minute marathon meeting with >= 1,800 core words passes Gate 2."""
        # 154-minute session: floor is 1000, ceiling is 4000 (or 4600 with diagrams)
        pass_1850, err_1850 = _check_word_count_bounds(
            word_count=1850,
            meeting_dur=154,
            has_diagrams=False,
            total_word_count=4200,
        )
        self.assertTrue(pass_1850, f"1850 words should pass for 154m meeting: {err_1850}")

        # Verify lower bound rejection for superficial recap on marathon meeting
        superficial_pass, superficial_err = _check_word_count_bounds(
            word_count=450,
            meeting_dur=154,
            has_diagrams=False,
            total_word_count=1200,
        )
        self.assertFalse(superficial_pass, "450 words must fail floor for 154m marathon session")
        self.assertTrue(any("too light" in e for e in superficial_err))

        # Verify upper bound acceptance up to 4000 words
        deep_pass, deep_err = _check_word_count_bounds(
            word_count=3800,
            meeting_dur=154,
            has_diagrams=False,
            total_word_count=6500,
        )
        self.assertTrue(deep_pass, f"3800 words must be accepted under 4000w ceiling: {deep_err}")

    def test_stitch_continuation_deduplication(self) -> None:
        """Verify _stitch_continuation cleanly eliminates duplicate overlapping lines."""
        prefix = "## Key Findings\n- Item 1: System contention observed.\n- Item 2: Cache miss rate high."
        continuation = "- Item 2: Cache miss rate high.\n- Item 3: Resolved by tuning TTL.\n\n# Appendix\n### Alice\n- Perspective."

        stitched = LLMTranscriptSynthesizer._stitch_continuation(prefix, continuation)
        self.assertIn("- Item 1: System contention observed.", stitched)
        self.assertIn("- Item 3: Resolved by tuning TTL.", stitched)
        # Should not duplicate Item 2
        self.assertEqual(stitched.count("- Item 2: Cache miss rate high."), 1)

    def test_stitch_continuation_whitespace_and_punctuation(self) -> None:
        """Verify _stitch_continuation handles mid-word and trailing punctuation cleanly."""
        # Case A: Prefix ends with period and newline
        p1 = "We confirmed the rollout date was set to 09/25/2026."
        c1 = "The next steps are outlined below."
        s1 = LLMTranscriptSynthesizer._stitch_continuation(p1, c1)
        self.assertEqual(s1, "We confirmed the rollout date was set to 09/25/2026.\nThe next steps are outlined below.")

        # Case B: Prefix cut off mid-word / mid-sentence without punctuation
        p2 = "The primary architectural risk stems from SQLite busy"
        c2 = "timeouts during high-concurrency write transactions."
        s2 = LLMTranscriptSynthesizer._stitch_continuation(p2, c2)
        self.assertEqual(
            s2,
            "The primary architectural risk stems from SQLite busy timeouts during high-concurrency write transactions.",
        )

    def test_anti_truncation_continuation_circuit_breaker(self) -> None:
        """Verify retry engages continuation prompting when finish_reason is length."""
        synth = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth.model = "gpt-5.6-sol"
        synth.reasoning_effort = "high"

        # Mock _create_completion_with_effort
        # First call truncated: finish_reason="length"
        # Second call returns remainder: finish_reason="stop"
        mock_choice_1 = MagicMock()
        mock_choice_1.message.content = "## Meeting Objective\nCore objective established.\n\n## Key Findings\n- Finding 1"
        mock_choice_1.finish_reason = "length"
        resp_1 = MagicMock(choices=[mock_choice_1])

        mock_choice_2 = MagicMock()
        mock_choice_2.message.content = "- Finding 2: Latency under 10ms.\n\n# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n### Bob\n- Details."
        mock_choice_2.finish_reason = "stop"
        resp_2 = MagicMock(choices=[mock_choice_2])

        synth._create_completion_with_effort = MagicMock(side_effect=[resp_1, resp_2])
        synth.cache_optimizer = MagicMock()
        synth.cache_optimizer.build_cacheable_messages.return_value = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}],
            None,
        )

        result = synth.synthesize_recap("sys", "user")

        # Verify two API calls occurred (initial + continuation retry)
        self.assertEqual(synth._create_completion_with_effort.call_count, 2)

        # Verify second call received continuation instruction with partial content
        second_call_kwargs = synth._create_completion_with_effort.call_args_list[1][1]
        retry_messages = second_call_kwargs.get("messages", [])
        self.assertTrue(any(m.get("role") == "assistant" for m in retry_messages))
        self.assertTrue(
            any("truncated mid-stream" in m.get("content", "") for m in retry_messages if m.get("role") == "user")
        )

        # Verify stitched output contains both parts
        self.assertIn("## Meeting Objective", result)
        self.assertIn("Finding 1", result)
        self.assertIn("Finding 2", result)
        self.assertIn("# Appendix: Detailed Stakeholder Perspectives", result)

    def test_bpe_profiler_preserves_60k_tokens(self) -> None:
        """Verify TokenProfiler retains documents up to 60k tokens without truncation."""
        profiler = TokenProfiler()
        # Generate text of ~48,000 tokens (~422,000 chars)
        repeated_sentence = "Operational intelligence requires deterministic execution and rigorous gate validation. "
        large_text = repeated_sentence * 4800

        # With 60k limit, large_text should not be truncated
        bounded_60k = profiler.truncate_to_token_limit(
            large_text,
            max_tokens=CANONICAL_PROMPT_CONFIG.output2.max_tokens,
            model="gpt-5.6-sol",
        )
        self.assertEqual(len(bounded_60k), len(large_text), "Document within 60k tokens must not be clipped")

        # Under the legacy 40k limit, it would be clipped
        bounded_40k = profiler.truncate_to_token_limit(
            large_text,
            max_tokens=40000,
            model="gpt-5.6-sol",
        )
        self.assertLess(len(bounded_40k), len(large_text), "Document exceeding 40k tokens must be clipped under 40k")

    def test_two_phase_appendix_continuation_under_gpt6_astra(self) -> None:
        """Verify gpt-6-luna triggers two-phase continuation if finish_reason=='stop' but Appendix missing."""
        synth = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
        synth.model = "gpt-6-luna"
        synth.reasoning_effort = "high"

        # First call finishes with stop, but omits the Appendix for a 45m meeting
        mock_choice_1 = MagicMock()
        mock_choice_1.message.content = (
            "# Subject: Test Meeting -- Executive Strategic & Technical Brief | 09/25/2026\n\n"
            "**Date:** 09/25/2026\n"
            "**Time:** 11:00 AM - 11:45 AM ET\n"
            "**Duration:** ~45m\n"
            "**Attendees:**\n"
            "PartnerAdvisory:\n- Alice (Lead)\n\n"
            "# Meeting Objective\nCore objective.\n\n"
            "# Key Discussion Points & Findings\n- Finding 1\n\n"
            "# Action & Governance Ledger\n| # | Type | Item | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Task | Alice | 09/30/2026 | [00:10] | Confirmed |\n"
        )
        mock_choice_1.finish_reason = "stop"
        resp_1 = MagicMock(choices=[mock_choice_1])

        # Second call returns the missing Appendix
        mock_choice_2 = MagicMock()
        mock_choice_2.message.content = (
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            "# Alice — PartnerAdvisory, Lead\n"
            "- **Core Architecture Perspective**: Alice emphasized clean-core principles and resilient persistence across all integration points."
        )
        mock_choice_2.finish_reason = "stop"
        resp_2 = MagicMock(choices=[mock_choice_2])

        synth._create_completion_with_effort = MagicMock(side_effect=[resp_1, resp_2])
        synth.cache_optimizer = MagicMock()
        synth.cache_optimizer.build_cacheable_messages.return_value = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}],
            None,
        )

        result = synth.synthesize_recap("sys", "user")

        # Must have invoked the API twice (initial generation + appendix continuation)
        self.assertEqual(synth._create_completion_with_effort.call_count, 2)
        second_call_kwargs = synth._create_completion_with_effort.call_args_list[1][1]
        self.assertEqual(second_call_kwargs.get("operation_name"), "L2-Stage02 Appendix Continuation")
        self.assertIn("# Appendix: Detailed Stakeholder Perspectives", result)
        self.assertIn("Alice emphasized clean-core principles", result)

    def test_gate2_1_appendix_enforcement_for_meetings_ge_15m(self) -> None:
        """Verify Gate 2.1 strictly fails when Appendix is omitted on duration >= 15m, and passes when present."""
        recap_without_appendix = (
            "# Subject: Test Sync -- Executive Strategic & Technical Brief | 09/25/2026\n\n"
            "**Date:** 09/25/2026\n"
            "**Time:** 10:00 AM - 10:45 AM ET\n"
            "**Duration:** ~45 minutes\n"
            "**Attendees:**\n"
            "PartnerAdvisory:\n- Bob (Architect)\n\n"
            "# Meeting Objective\nEvaluating system resilience and architecture.\n\n"
            "# Key Discussion Points & Findings\n- **Bob validated persistence**: Bob verified SQLite serialization [05:10].\n\n"
            "# Action & Governance Ledger\nNo formal actions were established in this session; open initiatives remain unresolved questions.\n"
        )
        res_fail = run_qa_gate_2(recap_without_appendix, meta={"duration_minutes": 45})
        self.assertFalse(res_fail.get("gates", {}).get("Gate_2_1_Parsed_Structural_Completeness", {}).get("passed", True))
        self.assertTrue(any("missing required section: 'Appendix" in f for f in res_fail.get("failures", [])))

        # When Appendix is added, Gate 2.1 passes
        recap_with_appendix = (
            recap_without_appendix
            + "\n# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            + "# Bob — PartnerAdvisory, Architect\n"
            + "- **Resilience Architecture Stance**: Bob established that zero-contention write serialization is required."
        )
        res_pass = run_qa_gate_2(recap_with_appendix, meta={"duration_minutes": 45})
        self.assertTrue(res_pass.get("gates", {}).get("Gate_2_1_Parsed_Structural_Completeness", {}).get("passed", False))


if __name__ == "__main__":
    unittest.main()

