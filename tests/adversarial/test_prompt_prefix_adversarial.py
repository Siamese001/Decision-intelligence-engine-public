"""
tests/adversarial/test_prompt_prefix_adversarial.py

Adversarial edge-case tests for Agent 2 PromptPrefixAligner:
Covering boundary payloads, malformed unicode/newlines, concurrent alignment,
overflow/token exhaustion, prompt jitter defense, and the LENS production incident regression.
"""
from __future__ import annotations

import concurrent.futures
import unittest
from typing import Any, Dict

from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner


class PromptPrefixAdversarialTests(unittest.TestCase):
    """Adversarial test suite for prompt prefix alignment and reasoning economizer."""

    def test_01_boundary_payload_empty_strings_and_nulls(self) -> None:
        """Category: Boundary payload - empty prompt, empty blocks, zero-length text."""
        # Empty system prompt and blocks
        p_empty = PromptPrefixAligner.align_static_prefix("", [])
        self.assertIn("PROMPT_CACHE_BOUNDARY_ALIGNMENT", p_empty)

        # Hundred empty blocks are cleanly pruned
        many_empty = ["" for _ in range(100)]
        p_clean = PromptPrefixAligner.align_static_prefix("Base prompt", many_empty)
        self.assertEqual(p_clean, PromptPrefixAligner.align_static_prefix("Base prompt", []))

        # None metadata defaults safely
        b_empty = PromptPrefixAligner.calculate_reasoning_token_budget("", metadata=None)
        self.assertEqual(b_empty["budget_tokens"], 2048)
        self.assertEqual(b_empty["reasoning_effort"], "low")

    def test_02_malformed_input_control_characters_and_mixed_newlines(self) -> None:
        """Category: Malformed input - carriage returns, trailing tabs, non-standard whitespace."""
        block1 = "Rule C-16: Invariant\r\n\tDetailed requirement\r\n\r\n"
        block2 = "Rule C-16: Invariant\n    Detailed requirement\n"

        p1 = PromptPrefixAligner.normalize_text_block(block1)
        p2 = PromptPrefixAligner.normalize_text_block(block2)

        # Both must have trailing whitespace and carriage returns stripped
        self.assertNotIn("\r", p1)
        self.assertFalse(p1.endswith(" "))
        self.assertFalse(p2.endswith(" "))

    def test_03_concurrency_and_race_multi_threaded_alignment(self) -> None:
        """Category: Concurrency & race - parallel threads executing prefix alignment and budgeting."""
        system_prompt = "Canonical Knowledge Graph Extraction Prompt."
        blocks = ["<gov>Rule 1</gov>", "<gov>Rule 2</gov>"]
        expected_hash = PromptPrefixAligner.compute_prefix_hash(
            PromptPrefixAligner.align_static_prefix(system_prompt, blocks)
        )

        def worker(idx: int) -> str:
            res = PromptPrefixAligner.align_static_prefix(system_prompt, blocks)
            _budget = PromptPrefixAligner.calculate_reasoning_token_budget(f"Text {idx} " * 200)
            return PromptPrefixAligner.compute_prefix_hash(res)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            hashes = [f.result() for f in futures]

        self.assertTrue(all(h == expected_hash for h in hashes), "Multi-threaded alignment must be 100% deterministic.")

    def test_04_overflow_and_exhaustion_massive_token_counts(self) -> None:
        """Category: Overflow & exhaustion - massive transcript inputs up to 100k words."""
        massive_text = ("turn dialogue phrase text " * 1000) * 100  # 400,000 words
        budget = PromptPrefixAligner.calculate_reasoning_token_budget(massive_text)

        # Must cleanly cap at 8192 tokens without overflow
        self.assertEqual(budget["budget_tokens"], 8192)
        self.assertEqual(budget["reasoning_effort"], "high")
        self.assertEqual(budget["tier"], "DEEP")
        self.assertGreater(budget["complexity_score"], 10.0)

    def test_05_invariant_bypass_attempt_prompt_jitter_defense(self) -> None:
        """Category: Invariant bypass attempt - non-deterministic whitespace and block ordering jitter."""
        sys = "System prompt"
        # Caller attempts to break prompt caching by adding randomized whitespace and trailing spaces
        jittered_blocks = [
            "<block_1>  Content   </block_1>   \n\n",
            "\n  <block_2>Different indentation</block_2>  \n",
        ]
        clean_blocks = [
            "<block_1>  Content   </block_1>",
            "<block_2>Different indentation</block_2>",
        ]

        p_jitter = PromptPrefixAligner.align_static_prefix(sys, jittered_blocks)
        p_clean = PromptPrefixAligner.align_static_prefix(sys, clean_blocks)

        hit_rate = PromptPrefixAligner.compute_cache_hit_rate(p_jitter, p_clean)
        self.assertEqual(hit_rate, 1.0, "PromptPrefixAligner must eliminate whitespace jitter to achieve 100% cache identity.")

    def test_06_exact_production_regression_lens_incident_unbounded_reasoning(self) -> None:
        """Category: Exact production regression - LENS Oct 7 unhedged 222k reasoning token expenditure."""
        # In LENS session: Stage 03 was called without reasoning bounds, spending 222k reasoning tokens across attempts
        lens_short_meeting = (
            "**Attendee 1**: SOW deliverables discussion.\n"
            "**Attendee 2**: Rate card agreed at $250/hr.\n"
            "**Attendee 1**: Finalized."
        )

        budget = PromptPrefixAligner.calculate_reasoning_token_budget(lens_short_meeting)

        # For a short 3-turn meeting, budget must be tightly bounded to 2048 low-effort tokens
        self.assertEqual(budget["budget_tokens"], 2048)
        self.assertEqual(budget["reasoning_effort"], "low")
        self.assertEqual(budget["tier"], "MINIMAL")


if __name__ == "__main__":
    unittest.main()
