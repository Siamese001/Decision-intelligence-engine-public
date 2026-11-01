"""Adversarial and boundary test suite for prompt caching chokepoints and interceptor defenses."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from config.credentials import _guard_client_completions


class MockCompletionResponse:
    def __init__(self, content: str, model: str = "gpt-5.6-luna"):
        self.id = "mock-adv-resp-1"
        self.model = model
        self.choices = [
            MagicMock(
                message=MagicMock(content=content),
                finish_reason="stop",
            )
        ]
        self.usage = MagicMock(prompt_tokens=100, completion_tokens=20)


class TestPromptCacheChokepointsAdversarial(unittest.IsolatedAsyncioTestCase):
    def test_adv_boundary_cache_namespace_special_chars(self):
        """Boundary Test: Malformed namespace with punctuation, spaces, unicode must not crash."""
        client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse('{"status": "ok"}'))
        client.chat.completions.create = mock_create

        guarded = _guard_client_completions(client, "gpt-5.6-luna")

        # Complex boundary namespace: punctuation, unicode, mixed case
        nasty_ns = "  Judge::Stage01!! @Review/2026🚀  "
        guarded.chat.completions.create(
            cache_namespace=nasty_ns,
            messages=[
                {"role": "system", "content": "You are a judge."},
                {"role": "user", "content": "Review this."},
            ],
        )

        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        # Should sanitize special characters cleanly
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:"))
        self.assertNotIn("🚀", call_kwargs["prompt_cache_key"])
        self.assertNotIn(" ", call_kwargs["prompt_cache_key"])

    def test_adv_invariant_bypass_stripped_options_and_model(self):
        """Invariant Bypass Test: Injecting prompt_cache_options and unapproved model is neutralized."""
        client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse('{"status": "ok"}'))
        client.chat.completions.create = mock_create

        guarded = _guard_client_completions(client, "gpt-5.6-luna")

        guarded.chat.completions.create(
            model="claude-3-5-sonnet-rogue-bypass",
            prompt_cache_options={"comparison_response_id": "malicious-cross-tenant-id"},
            temperature=0.9,
            messages=[{"role": "user", "content": "bypass"}],
        )

        call_kwargs = mock_create.call_args[1]
        # Invariant 1: Unapproved model remapped to default approved model
        self.assertEqual(call_kwargs["model"], "gpt-5.6-luna")
        # Invariant 2: Legacy prompt_cache_options stripped
        self.assertNotIn("prompt_cache_options", call_kwargs)
        # Invariant 3: Temperature stripped for reasoning model
        self.assertNotIn("temperature", call_kwargs)

    async def test_adv_concurrency_race_isolated_kwargs(self):
        """Concurrency Test: High-concurrency async calls must maintain kwargs isolation."""
        async_client = MagicMock()
        captured_kwargs = []

        async def mock_async_create(*args, **kwargs):
            # Capture a shallow copy
            captured_kwargs.append(dict(kwargs))
            await asyncio.sleep(0.01)
            return MockCompletionResponse(f"resp-{kwargs.get('prompt_cache_key', 'none')}")

        async_client.chat.completions.create = mock_async_create
        guarded = _guard_client_completions(async_client, "gpt-5.6-luna")

        async def invoke_agent(agent_idx: int):
            return await guarded.chat.completions.create(
                cache_namespace=f"worker_agent_{agent_idx}",
                messages=[
                    {"role": "system", "content": f"System prompt for worker {agent_idx}"},
                    {"role": "user", "content": f"Task {agent_idx}"},
                ],
            )

        # Launch 20 concurrent invocations
        results = await asyncio.gather(*(invoke_agent(i) for i in range(20)))

        self.assertEqual(len(results), 20)
        self.assertEqual(len(captured_kwargs), 20)

        # Verify each invocation got its own unique, deterministic prompt_cache_key
        keys = [kw.get("prompt_cache_key") for kw in captured_kwargs]
        self.assertEqual(len(keys), 20)
        self.assertEqual(len(set(keys)), 20, "All 20 concurrent invocations must have unique prompt_cache_keys")

    def test_adv_security_pattern_rejection_in_key(self):
        """Security Hygiene Test: Sensitive indicators in cache_namespace fail safe without crash."""
        client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse('{"status": "ok"}'))
        client.chat.completions.create = mock_create

        guarded = _guard_client_completions(client, "gpt-5.6-luna")

        # Attempt to inject UUID / salary / run ID in namespace
        forbidden_ns = "salary_compensation_run-12345678-abcd"
        # Should gracefully fail derivation without raising unhandled exception
        resp = guarded.chat.completions.create(
            cache_namespace=forbidden_ns,
            messages=[
                {"role": "system", "content": "You are an assistant."},
                {"role": "user", "content": "Hello."},
            ],
        )

        self.assertIsNotNone(resp)
        call_kwargs = mock_create.call_args[1]
        # Since forbidden pattern failed derivation, prompt_cache_key should either be absent or sanitized
        if "prompt_cache_key" in call_kwargs:
            self.assertNotIn("salary", call_kwargs["prompt_cache_key"])


if __name__ == "__main__":
    unittest.main()
