"""Unit tests for dual_pipeline.prompt_cache SSOT."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dual_pipeline.prompt_cache import (
    DEFAULT_CACHE_RETENTION,
    PROVIDER_MIN_PREFIX_TOKENS,
    anthropic_cache_breakpoint,
    derive_cache_key,
    normalize_cached_tokens,
)


class TestPromptCacheSSOT(unittest.TestCase):
    def test_provider_min_tokens_constants(self) -> None:
        self.assertEqual(PROVIDER_MIN_PREFIX_TOKENS["openai"], 1024)
        self.assertEqual(PROVIDER_MIN_PREFIX_TOKENS["anthropic"], 1024)
        self.assertEqual(PROVIDER_MIN_PREFIX_TOKENS["gemini"], 1024)
        self.assertEqual(DEFAULT_CACHE_RETENTION, "in_memory")

    def test_derive_cache_key_determinism(self) -> None:
        k1 = derive_cache_key(
            workflow="l2_decision",
            model="gpt-5.6-luna",
            static_prefix="You are a strategic decision engine.",
            template_version="v1",
        )
        k2 = derive_cache_key(
            workflow="l2_decision",
            model="gpt-5.6-luna",
            static_prefix="You are a strategic decision engine.",
            template_version="v1",
        )
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("nee:l2_decision:gpt-5.6-luna:"))
        self.assertEqual(len(k1.split(":")[-1]), 16)

    def test_derive_cache_key_sanitization(self) -> None:
        k = derive_cache_key(
            workflow="Query / Router! ",
            model="Gemini 3.8 Flash",
            static_prefix="Static Prefix",
            template_version="v2",
        )
        self.assertTrue(k.startswith("nee:query___router_:gemini_3.8_flash:"))

    def test_derive_cache_key_different_inputs_diverge(self) -> None:
        k1 = derive_cache_key(
            workflow="workflow_a",
            model="gpt-5.6-luna",
            static_prefix="Prefix",
        )
        k2 = derive_cache_key(
            workflow="workflow_b",
            model="gpt-5.6-luna",
            static_prefix="Prefix",
        )
        k3 = derive_cache_key(
            workflow="workflow_a",
            model="gpt-6-sol",
            static_prefix="Prefix",
        )
        k4 = derive_cache_key(
            workflow="workflow_a",
            model="gpt-5.6-luna",
            static_prefix="Different Prefix",
        )
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertNotEqual(k1, k4)

    def test_derive_cache_key_security_hygiene(self) -> None:
        # Attempting to inject UUID or run ID into workflow
        with self.assertRaises(ValueError):
            derive_cache_key(
                workflow="run_12345678-abcd-1234-abcd-123456789abc",
                model="gpt-5.6-luna",
                static_prefix="prefix",
            )

        # Attempting to inject ISO timestamp
        with self.assertRaises(ValueError):
            derive_cache_key(
                workflow="run_2026-10-03T11:00:00",
                model="gpt-5.6-luna",
                static_prefix="prefix",
            )

        # Attempting to inject salary or compensation
        with self.assertRaises(ValueError):
            derive_cache_key(
                workflow="salary_review",
                model="gpt-5.6-luna",
                static_prefix="prefix",
            )

    def test_normalize_cached_tokens_openai_responses(self) -> None:
        # Dict format
        res_dict = {
            "usage": {
                "input_tokens_details": {"cached_tokens": 1024},
                "output_tokens": 50,
            }
        }
        self.assertEqual(normalize_cached_tokens(res_dict), 1024)

        # Object format
        res_obj = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens_details=SimpleNamespace(cached_tokens=2048)
            )
        )
        self.assertEqual(normalize_cached_tokens(res_obj), 2048)

    def test_normalize_cached_tokens_openai_chat(self) -> None:
        res_dict = {
            "usage": {
                "prompt_tokens_details": {"cached_tokens": 512},
                "total_tokens": 600,
            }
        }
        self.assertEqual(normalize_cached_tokens(res_dict), 512)

    def test_normalize_cached_tokens_gemini(self) -> None:
        # usageMetadata with camelCase
        res_camel = {"usageMetadata": {"cachedContentTokenCount": 4096}}
        self.assertEqual(normalize_cached_tokens(res_camel, provider="gemini"), 4096)

        # snake_case
        res_snake = {"usage_metadata": {"cached_content_token_count": 2048}}
        self.assertEqual(normalize_cached_tokens(res_snake, provider="gemini"), 2048)

        # Object format
        res_obj = SimpleNamespace(
            usage_metadata=SimpleNamespace(cached_content_token_count=1024)
        )
        self.assertEqual(normalize_cached_tokens(res_obj, provider="gemini"), 1024)

    def test_normalize_cached_tokens_anthropic(self) -> None:
        res_dict = {"usage": {"cache_read_input_tokens": 1280}}
        self.assertEqual(normalize_cached_tokens(res_dict, provider="anthropic"), 1280)

    def test_normalize_cached_tokens_fallback_and_edge_cases(self) -> None:
        self.assertEqual(normalize_cached_tokens(None), 0)
        self.assertEqual(normalize_cached_tokens({}), 0)
        self.assertEqual(normalize_cached_tokens({"usage": {}}), 0)
        # Boolean values must not be converted to 1
        self.assertEqual(normalize_cached_tokens({"usage": {"cached_tokens": True}}), 0)
        # Negative values clamped to 0
        self.assertEqual(normalize_cached_tokens({"usage": {"cached_tokens": -50}}), 0)
        # Invalid strings
        self.assertEqual(normalize_cached_tokens({"usage": {"cached_tokens": "invalid"}}), 0)
        # Valid string integers parsed
        self.assertEqual(normalize_cached_tokens({"usage": {"cached_tokens": "768"}}), 768)

    def test_anthropic_cache_breakpoint(self) -> None:
        bp = anthropic_cache_breakpoint("Contract text", "ephemeral")
        self.assertEqual(bp["type"], "text")
        self.assertEqual(bp["text"], "Contract text")
        self.assertEqual(bp["cache_control"]["type"], "ephemeral")


if __name__ == "__main__":
    unittest.main()
