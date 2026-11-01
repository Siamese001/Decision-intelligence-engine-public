import unittest
from unittest.mock import MagicMock

from config.credentials import _guard_client_completions


class TestPromptCacheInterceptor(unittest.TestCase):
    def test_interceptor_derives_prompt_cache_key_from_namespace(self):
        client = MagicMock()
        orig_create = MagicMock()
        client.chat.completions.create = orig_create

        mock_response = MagicMock()
        mock_response.id = "resp-123"
        orig_create.return_value = mock_response

        guarded = _guard_client_completions(client, "gpt-5.6-luna")

        r1 = guarded.chat.completions.create(
            cache_namespace="stage01_forensic",
            messages=[
                {"role": "system", "content": "You are a forensic normalizer."},
                {"role": "user", "content": "Process transcript."},
            ],
        )
        self.assertEqual(r1.id, "resp-123")

        call_kwargs = orig_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:stage01_forensic:"))
        self.assertNotIn("cache_namespace", call_kwargs)

    def test_interceptor_preserves_explicit_prompt_cache_key(self):
        client = MagicMock()
        orig_create = MagicMock()
        client.chat.completions.create = orig_create

        mock_response = MagicMock()
        orig_create.return_value = mock_response

        guarded = _guard_client_completions(client, "gpt-5.6-luna")

        guarded.chat.completions.create(
            prompt_cache_key="nee:custom_workflow:v1:abc12345",
            messages=[{"role": "user", "content": "hello"}],
        )

        call_kwargs = orig_create.call_args[1]
        self.assertEqual(call_kwargs.get("prompt_cache_key"), "nee:custom_workflow:v1:abc12345")

    def test_interceptor_strips_prompt_cache_options(self):
        client = MagicMock()
        orig_create = MagicMock()
        client.chat.completions.create = orig_create

        mock_response = MagicMock()
        orig_create.return_value = mock_response

        guarded = _guard_client_completions(client, "gpt-5.6-luna")

        # Passing legacy prompt_cache_options (Responses API only) must be safely stripped
        guarded.chat.completions.create(
            prompt_cache_options={"comparison_response_id": "resp-legacy-123"},
            messages=[{"role": "user", "content": "hello"}],
        )

        call_kwargs = orig_create.call_args[1]
        self.assertNotIn("prompt_cache_options", call_kwargs)

    def test_interceptor_canonicalizes_unapproved_model(self):
        client = MagicMock()
        orig_create = MagicMock()
        client.chat.completions.create = orig_create

        mock_response = MagicMock()
        orig_create.return_value = mock_response

        guarded = _guard_client_completions(client, "gpt-5.6-luna")

        guarded.chat.completions.create(
            model="unapproved-model-v99",
            messages=[{"role": "user", "content": "hello"}],
        )

        call_kwargs = orig_create.call_args[1]
        self.assertEqual(call_kwargs["model"], "gpt-5.6-luna")


if __name__ == "__main__":
    unittest.main()
