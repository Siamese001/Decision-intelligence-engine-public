"""End-to-end integration tests for prompt cache chokepoints and client routing consistency."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from config.credentials import _guard_client_completions
from dual_pipeline.manifest_runner.remediation_healer import heal_output_3_projection


class MockCompletionResponse:
    def __init__(self, content: str, model: str = "gpt-5.6-luna", cached_tokens: int = 256):
        self.id = "mock-resp-id-99"
        self.model = model
        self.choices = [
            MagicMock(
                message=MagicMock(content=content),
                finish_reason="stop",
            )
        ]
        self.usage = MagicMock(
            prompt_tokens=500,
            completion_tokens=100,
            prompt_tokens_details=MagicMock(cached_tokens=cached_tokens),
            completion_tokens_details=MagicMock(reasoning_tokens=50),
        )


class TestPromptCacheChokepointsE2E(unittest.IsolatedAsyncioTestCase):
    def test_e2e_sync_guarded_client_full_cycle(self):
        """Verify sync client through _guard_client_completions derives key and strips namespace."""
        client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse('{"status": "ok"}'))
        client.chat.completions.create = mock_create

        guarded = _guard_client_completions(client, "gpt-5.6-luna")
        resp = guarded.chat.completions.create(
            cache_namespace="forensic_qa",
            messages=[
                {"role": "system", "content": "You are a forensic meeting validator."},
                {"role": "user", "content": "Verify turn 12."},
            ],
        )

        self.assertEqual(resp.id, "mock-resp-id-99")
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:forensic_qa:gpt-5.6-luna:"))
        self.assertNotIn("cache_namespace", call_kwargs)

    async def test_e2e_async_guarded_client_full_cycle(self):
        """Verify async client through _guard_client_completions derives key asynchronously."""
        async_client = MagicMock()
        async_create = AsyncMock(return_value=MockCompletionResponse('{"status": "async_ok"}'))
        async_client.chat.completions.create = async_create

        guarded = _guard_client_completions(async_client, "gpt-5.6-luna")
        resp = await guarded.chat.completions.create(
            cache_namespace="async_retrieval_judge",
            messages=[
                {"role": "system", "content": "You are a retrieval evaluator."},
                {"role": "user", "content": "Evaluate candidate 1."},
            ],
        )

        self.assertEqual(resp.id, "mock-resp-id-99")
        call_kwargs = async_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:async_retrieval_judge:gpt-5.6-luna:"))
        self.assertNotIn("cache_namespace", call_kwargs)

    def test_e2e_remediation_healer_cache_namespace(self):
        """Verify heal_output_3_projection passes cache_namespace to guarded client."""
        client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse(
            '{"entities": [{"id": "person:jane_doe", "type": "person", "attributes": {"canonical_name": "Jane Doe"}}], "relations": [], "evidence": []}'
        ))
        client.chat.completions.create = mock_create

        guarded = _guard_client_completions(client, "gpt-5.6-luna")
        res = heal_output_3_projection(
            f3_text='{"entities": [], "relations": [], "evidence": []}',
            remediation_directive="Add person:jane_doe entity",
            client=guarded,
        )

        self.assertIn("entities", res)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:remediation_healer_f3:"))

    def test_e2e_synthesizer_effort_fallback_no_batch_leak(self):
        """Verify fallback from xhigh to high cleanly removes batch from API call kwargs."""
        from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer

        raw_client = MagicMock()
        call_history = []
        def mock_create(**kwargs):
            call_history.append(dict(kwargs))
            if len(call_history) == 1:
                raise ValueError("reasoning_effort 'xhigh' is not supported for this model")
            return MockCompletionResponse("Forensic normalized output text")

        raw_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_client, "gpt-5.6-sol")

        synth = LLMTranscriptSynthesizer(
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            _client=guarded_client,
        )

        synth._create_completion_with_effort(
            target_effort="xhigh",
            operation_name="Stage01 Forensic Normalization",
            messages=[
                {"role": "system", "content": "You are a forensic normalizer."},
                {"role": "user", "content": "Clean transcript text."},
            ],
            model="gpt-5.6-sol",
            batch=True,
        )

        self.assertEqual(len(call_history), 2)
        # Attempt 1: primary attempt (batch popped by kwargs_for_api)
        self.assertNotIn("batch", call_history[0])
        self.assertEqual(call_history[0].get("reasoning_effort"), "xhigh")

        # Attempt 2: fallback attempt (must NOT leak batch kwarg)
        self.assertNotIn("batch", call_history[1])
        self.assertEqual(call_history[1].get("reasoning_effort"), "high")


if __name__ == "__main__":
    unittest.main()
