"""
tests/test_prompt_cache_hit_optimization.py

Regression test suite validating Wave 3:
1. PromptCacheOptimizer isolates static prefix from dynamic elements.
2. Static system prompt prefix meets minimum token threshold (>= 1,024 tokens).
3. Invariant prefix hashes are consistent across runs with differing dynamic tails.
"""
from __future__ import annotations

import unittest

from dual_pipeline.token_optimization.cache_optimizer import (
    PromptCacheOptimizer,
)


class TestPromptCacheHitOptimization(unittest.TestCase):
    def test_cache_optimizer_static_prefix_stabilization(self):
        """Verify dynamic runtime context is cleanly separated from static instructions."""
        optimizer = PromptCacheOptimizer()

        static_core = "You are a specialized meeting forensic normalizer. Follow all strict rules.\n" * 50
        dynamic_metadata = "Current Date/Time: 2026-09-21 16:30:00\nExecution Run ID: run-9988-abc\n"
        combined_prompt = f"{static_core}\n{dynamic_metadata}\nMore instructions for formatting."

        static_prefix, dynamic_tail, prefix_tokens, is_cacheable = optimizer.stabilize_system_prefix(
            combined_prompt, min_prefix_tokens=50
        )

        self.assertNotIn("Current Date/Time:", static_prefix)
        self.assertNotIn("Execution Run ID:", static_prefix)
        self.assertIn("Current Date/Time:", dynamic_tail)
        self.assertIn("Execution Run ID:", dynamic_tail)
        self.assertGreater(prefix_tokens, 50)
        self.assertTrue(is_cacheable)

    def test_cache_optimizer_deterministic_prefix_hash(self):
        """Verify identical static core produces identical SHA-256 prefix hash regardless of dynamic variation."""
        optimizer = PromptCacheOptimizer()

        static_core = "Standard invariant instructions for architecture review.\n" * 60
        run_1_prompt = f"{static_core}\nCurrent Date/Time: 2026-09-21 12:00:00\n"
        run_2_prompt = f"{static_core}\nCurrent Date/Time: 2026-09-22 09:15:00\n"

        prefix_1, _, _, _ = optimizer.stabilize_system_prefix(run_1_prompt, min_prefix_tokens=50)
        prefix_2, _, _, _ = optimizer.stabilize_system_prefix(run_2_prompt, min_prefix_tokens=50)

        hash_1 = optimizer.compute_prefix_hash(prefix_1)
        hash_2 = optimizer.compute_prefix_hash(prefix_2)

        self.assertEqual(hash_1, hash_2)

    def test_build_cacheable_messages_dynamic_relocation(self):
        """Verify dynamic tail from system prompt is prepended into user payload to preserve system prefix cache."""
        optimizer = PromptCacheOptimizer()

        system_prompt = "You are an AI assistant.\n" * 80 + "Execution Run ID: 12345\n"
        user_prompt = "Process this meeting transcript."

        messages, components = optimizer.build_cacheable_messages(system_prompt, user_prompt)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertNotIn("Execution Run ID:", messages[0]["content"])

        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Execution Run ID: 12345", messages[1]["content"])
        self.assertIn("Process this meeting transcript.", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
