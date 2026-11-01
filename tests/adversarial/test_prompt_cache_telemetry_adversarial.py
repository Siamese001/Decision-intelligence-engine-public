"""Adversarial and edge-case testing for prompt caching telemetry normalization."""

from __future__ import annotations

import concurrent.futures
import unittest
from unittest.mock import MagicMock

from dual_pipeline.otel_engine import LLMTraceRecorder


class TestPromptCacheTelemetryAdversarial(unittest.TestCase):
    def test_record_response_malformed_usage_objects(self) -> None:
        """Ensure record_response never raises an exception even with adversarial payloads."""
        mock_span = MagicMock()
        recorder = LLMTraceRecorder(span=mock_span)

        malformed_payloads = [
            None,
            "raw string instead of dict",
            [],
            {"usage": None},
            {"usage": "not a dict"},
            {"usage": {"prompt_tokens_details": None}},
            {"usage": {"prompt_tokens_details": {"cached_tokens": False}}},
            {"usage": {"prompt_tokens_details": {"cached_tokens": -999}}},
            {"usage": {"prompt_tokens_details": {"cached_tokens": "invalid_number"}}},
            {"usage": {"prompt_tokens_details": {"cached_tokens": float("nan")}}},
        ]

        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                # Must not raise
                recorder.record_response(payload)

    def test_record_response_concurrency_stress(self) -> None:
        """Ensure concurrent recordings on independent spans execute safely."""
        def _record_worker(idx: int) -> None:
            mock_span = MagicMock()
            recorder = LLMTraceRecorder(span=mock_span)
            resp = {
                "usage": {
                    "prompt_tokens": 1000,
                    "prompt_tokens_details": {"cached_tokens": 500 + idx},
                }
            }
            recorder.record_response(resp)
            mock_span.set_attribute.assert_any_call("gen_ai.usage.cached_tokens", 500 + idx)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_record_worker, i) for i in range(25)]
            for f in futures:
                f.result()


if __name__ == "__main__":
    unittest.main()
