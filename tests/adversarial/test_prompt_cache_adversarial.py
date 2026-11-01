"""Adversarial and edge-case testing for prompt caching SSOT and key derivation."""

from __future__ import annotations

import concurrent.futures
import unittest

from dual_pipeline.prompt_cache import (
    anthropic_cache_breakpoint,
    derive_cache_key,
    normalize_cached_tokens,
)


class TestPromptCacheAdversarial(unittest.TestCase):
    def test_key_derivation_adversarial_characters(self) -> None:
        """Ensure control characters, spaces, and weird symbols are sanitized into a clean key."""
        dirty_workflow = "  \n\t--Test / Workflow $#@! 99 \r\n  "
        dirty_model = "gpt-5.6 / sol : high (fast) "
        key = derive_cache_key(
            workflow=dirty_workflow,
            model=dirty_model,
            static_prefix="Normal Prefix",
        )
        # Should sanitize into valid chars only
        self.assertRegex(key, r"^nee:[a-zA-Z0-9_-]+:[a-zA-Z0-9._-]+:[0-9a-f]{16}$")
        self.assertNotIn(" ", key)
        self.assertNotIn("\n", key)
        self.assertNotIn("$", key)

    def test_key_derivation_massive_prefix_boundary(self) -> None:
        """Ensure derive_cache_key handles massive (1MB) static prefixes efficiently without overflow."""
        massive_prefix = "A" * 1_000_000
        key = derive_cache_key(
            workflow="stress_test",
            model="gemini-3.8-flash",
            static_prefix=massive_prefix,
        )
        self.assertTrue(key.startswith("nee:stress_test:gemini-3.8-flash:"))
        self.assertEqual(len(key.split(":")[-1]), 16)

    def test_key_derivation_concurrency_race_condition(self) -> None:
        """Verify thread safety when 20 concurrent workers derive keys simultaneously."""
        def _worker(worker_id: int) -> str:
            return derive_cache_key(
                workflow="concurrent_workflow",
                model="gpt-5.6-luna",
                static_prefix=f"Prefix for worker group {worker_id % 3}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_worker, i) for i in range(40)]
            results = [f.result() for f in futures]

        # There should be exactly 3 distinct keys across all 40 calls
        unique_keys = set(results)
        self.assertEqual(len(unique_keys), 3)

    def test_key_derivation_blocks_sensitive_leakage(self) -> None:
        """Ensure attempts to inject PII or compensation in keys raise ValueError."""
        leaks = [
            "user_uuid_12345678-abcd-1234-abcd-123456789abc",
            "run_2026-10-03 14:30:00",
            "rate_card_senior_partner",
            "compensation_bracket_tier1",
            "bonus_amount_$500,000",
        ]
        for leak in leaks:
            with self.subTest(leak=leak), self.assertRaises(ValueError):
                derive_cache_key(
                    workflow=leak,
                    model="gpt-5.6-luna",
                    static_prefix="static",
                )

    def test_normalize_cached_tokens_adversarial_types(self) -> None:
        """Ensure normalizer handles malformed, adversarial, and boundary types gracefully."""
        # Generator
        gen = (x for x in [1, 2, 3])
        self.assertEqual(normalize_cached_tokens(gen), 0)

        # Extreme values & float types
        res_float = {"usage": {"cached_tokens": 1024.99}}
        self.assertEqual(normalize_cached_tokens(res_float), 1024)

        # Extremely large token numbers
        res_huge = {"usage": {"cached_tokens": 10**12}}
        self.assertEqual(normalize_cached_tokens(res_huge), 10**12)

        # Dictionary with circular reference
        circular_dict: dict = {}
        circular_dict["self"] = circular_dict
        self.assertEqual(normalize_cached_tokens(circular_dict), 0)

        # None / False / Empty string inside nested dict
        self.assertEqual(normalize_cached_tokens({"usage": {"input_tokens_details": None}}), 0)
        self.assertEqual(normalize_cached_tokens({"usage": {"input_tokens_details": {"cached_tokens": False}}}), 0)
        self.assertEqual(normalize_cached_tokens({"usage": {"input_tokens_details": {"cached_tokens": ""}}}), 0)

    def test_anthropic_breakpoint_immutability(self) -> None:
        """Verify breakpoint dictionary structure is isolated and immutable."""
        bp1 = anthropic_cache_breakpoint("Prompt text 1")
        bp2 = anthropic_cache_breakpoint("Prompt text 2")
        self.assertNotEqual(bp1["text"], bp2["text"])
        bp1["cache_control"]["type"] = "modified"
        self.assertEqual(bp2["cache_control"]["type"], "ephemeral")

    def test_cross_namespace_isolation_with_identical_prompts(self) -> None:
        """Verify distinct workflows with identical prompt text produce distinct keys, preventing cross-pipeline poisoning."""
        identical_prompt = "Analyze this system architecture and extract commitments."
        key_tech = derive_cache_key(
            workflow="tech_arch_agent",
            model="gpt-5.6-luna",
            static_prefix=identical_prompt,
        )
        key_commitments = derive_cache_key(
            workflow="strategic_commitments_agent",
            model="gpt-5.6-luna",
            static_prefix=identical_prompt,
        )
        self.assertNotEqual(key_tech, key_commitments)
        self.assertIn("tech_arch_agent", key_tech)
        self.assertIn("strategic_commitments_agent", key_commitments)

    def test_empty_static_prefix_boundary_handling(self) -> None:
        """Verify empty or whitespace static prefixes derive safe keys without crashing."""
        for empty_val in ("", "   ", "\n\t\r"):
            with self.subTest(empty_val=repr(empty_val)):
                key = derive_cache_key(
                    workflow="empty_prefix_test",
                    model="gpt-5.6-luna",
                    static_prefix=empty_val,
                )
                self.assertTrue(key.startswith("nee:empty_prefix_test:gpt-5.6-luna:"))
                self.assertEqual(len(key.split(":")[-1]), 16)

    def test_consecutive_call_cache_hit_simulation(self) -> None:
        """Simulate two consecutive guarded client completions: Turn 1 miss -> Turn 2 hit."""
        from unittest.mock import MagicMock
        from config.credentials import _guard_client_completions

        call_records = []

        def mock_create(**kwargs):
            call_records.append(kwargs)
            turn_idx = len(call_records)
            cached = 0 if turn_idx == 1 else 512
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content=f"Response {turn_idx}"))]
            mock_resp.usage = MagicMock(
                prompt_tokens=600,
                completion_tokens=50,
                input_tokens_details=None,
                prompt_tokens_details=MagicMock(cached_tokens=cached),
            )
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        guarded = _guard_client_completions(mock_client, "gpt-5.6-luna")
        static_sys = "You are a specialized legal and architecture reviewer."

        # Turn 1: Miss
        r1 = guarded.chat.completions.create(
            cache_namespace="consecutive_test",
            messages=[
                {"role": "system", "content": static_sys},
                {"role": "user", "content": "Question 1"},
            ],
        )
        self.assertEqual(normalize_cached_tokens(r1), 0)

        # Turn 2: Hit (different user question, identical system prompt)
        r2 = guarded.chat.completions.create(
            cache_namespace="consecutive_test",
            messages=[
                {"role": "system", "content": static_sys},
                {"role": "user", "content": "Question 2"},
            ],
        )
        self.assertEqual(normalize_cached_tokens(r2), 512)

        # Verify both calls derived the EXACT SAME prompt_cache_key
        self.assertEqual(len(call_records), 2)
        self.assertEqual(call_records[0]["prompt_cache_key"], call_records[1]["prompt_cache_key"])


if __name__ == "__main__":
    unittest.main()
