import unittest
from unittest.mock import AsyncMock, MagicMock

from config.credentials import _guard_client_completions


class MockResponse:
    def __init__(self, _id: str):
        self.id = _id
        self.prompt_cache_diagnostics = None


class TestAsyncPromptCacheInterceptor(unittest.IsolatedAsyncioTestCase):
    async def test_async_interceptor_derives_prompt_cache_key(self):
        async_client = MagicMock()
        mock_orig_create = AsyncMock(return_value=MockResponse("resp-async-123"))
        async_client.chat.completions.create = mock_orig_create

        guarded = _guard_client_completions(async_client, "gpt-5.6-luna")
        resp = await guarded.chat.completions.create(
            cache_namespace="test_async_workflow",
            messages=[
                {"role": "system", "content": "You are an async system agent."},
                {"role": "user", "content": "hello async"},
            ],
        )
        self.assertEqual(resp.id, "resp-async-123")

        call_kwargs = mock_orig_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:test_async_workflow:"))
        self.assertNotIn("cache_namespace", call_kwargs)

    async def test_async_interceptor_preserves_explicit_key(self):
        async_client = MagicMock()
        mock_orig_create = AsyncMock(return_value=MockResponse("resp-async-456"))
        async_client.chat.completions.create = mock_orig_create

        guarded = _guard_client_completions(async_client, "gpt-5.6-luna")
        resp = await guarded.chat.completions.create(
            prompt_cache_key="nee:explicit_async:key:123456",
            messages=[{"role": "user", "content": "hello"}],
        )
        self.assertEqual(resp.id, "resp-async-456")

        call_kwargs = mock_orig_create.call_args[1]
        self.assertEqual(call_kwargs.get("prompt_cache_key"), "nee:explicit_async:key:123456")


if __name__ == "__main__":
    unittest.main()
