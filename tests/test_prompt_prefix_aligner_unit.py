"""
tests/test_prompt_prefix_aligner_unit.py

Unit tests for Agent 2 PromptPrefixAligner and Dynamic Reasoning Economizer.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner


class PromptPrefixAlignerUnitTests(unittest.TestCase):
    """Unit test suite verifying prompt prefix stability, cache hit calculation, and reasoning token budgeting."""

    def test_01_normalize_text_block(self) -> None:
        raw = "  Line 1   \n\n\n  Line 2   \n"
        norm = PromptPrefixAligner.normalize_text_block(raw)
        self.assertEqual(norm, "Line 1\n\n\n  Line 2")

    def test_02_align_static_prefix_byte_identity(self) -> None:
        sys_prompt = "You are a specialized Knowledge Graph extraction agent."
        blocks = [
            "<governance>\nRule C-16 Invariant active\n</governance>",
            "<certification>\nGate F Certified\n</certification>",
        ]
        p1 = PromptPrefixAligner.align_static_prefix(sys_prompt, blocks, stage_tag="stage03_graph")
        p2 = PromptPrefixAligner.align_static_prefix(sys_prompt, blocks, stage_tag="stage03_graph")

        self.assertEqual(p1, p2, "Static prefixes must be 100% byte-identical across calls.")
        h1 = PromptPrefixAligner.compute_prefix_hash(p1)
        h2 = PromptPrefixAligner.compute_prefix_hash(p2)
        self.assertEqual(h1, h2)
        self.assertIn("PROMPT_CACHE_BOUNDARY_ALIGNMENT", p1)

    def test_03_compute_cache_hit_rate(self) -> None:
        prefix = "Shared static prefix instructions with exactly 100 characters padding." + ("A" * 900)
        # Small dynamic variation at the very end
        variant = prefix[:950] + ("B" * 50)

        hit_rate = PromptPrefixAligner.compute_cache_hit_rate(prefix, variant)
        self.assertGreaterEqual(hit_rate, 0.90, "Cache hit rate must meet or exceed 90%.")

        # Completely disjoint prefixes
        zero_hit = PromptPrefixAligner.compute_cache_hit_rate("AAAA", "BBBB")
        self.assertEqual(zero_hit, 0.0)

    def test_04_calculate_reasoning_token_budget_tiers(self) -> None:
        # 1. Minimal tier (< 1500 words, < 25 turns)
        short_text = "Speaker 1: Quick update.\nSpeaker 2: Looks good."
        b_low = PromptPrefixAligner.calculate_reasoning_token_budget(short_text)
        self.assertEqual(b_low["budget_tokens"], 2048)
        self.assertEqual(b_low["reasoning_effort"], "low")
        self.assertEqual(b_low["tier"], "MINIMAL")

        # 2. Standard tier (1500 - 5000 words)
        medium_text = ("**Speaker 1**: Technical discussion topic.\n" * 40) + ("word " * 2000)
        b_med = PromptPrefixAligner.calculate_reasoning_token_budget(medium_text)
        self.assertEqual(b_med["budget_tokens"], 4096)
        self.assertEqual(b_med["reasoning_effort"], "medium")
        self.assertEqual(b_med["tier"], "STANDARD")

        # 3. Deep tier (> 5000 words)
        long_text = ("**Speaker 1**: Deep dive architectural review.\n" * 90) + ("token " * 5500)
        b_high = PromptPrefixAligner.calculate_reasoning_token_budget(long_text)
        self.assertEqual(b_high["budget_tokens"], 8192)
        self.assertEqual(b_high["reasoning_effort"], "high")
        self.assertEqual(b_high["tier"], "DEEP")

    def test_05_calculate_reasoning_token_budget_operator_override(self) -> None:
        meta = {"reasoning_budget_override": 1024, "reasoning_effort": "low"}
        b_custom = PromptPrefixAligner.calculate_reasoning_token_budget("Small text", metadata=meta)
        self.assertEqual(b_custom["budget_tokens"], 1024)
        self.assertEqual(b_custom["reasoning_effort"], "low")
        self.assertEqual(b_custom["tier"], "CUSTOM_OVERRIDE")

    def test_06_apply_reasoning_budget_to_provider(self) -> None:
        mock_provider = MagicMock()
        budget_info = {"budget_tokens": 4096, "reasoning_effort": "medium"}

        PromptPrefixAligner.apply_reasoning_budget_to_provider(mock_provider, budget_info)
        mock_provider.set_reasoning_budget.assert_called_once_with(4096)


if __name__ == "__main__":
    unittest.main()
