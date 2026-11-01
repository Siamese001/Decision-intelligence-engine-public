"""End-to-end tests for prompt caching telemetry normalization across providers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from dual_pipeline.otel_engine import LLMTraceRecorder
from dual_pipeline.x1_quality_core.models import QualityUsage


class TestPromptCacheTelemetryE2E(unittest.TestCase):
    def test_otel_span_records_cached_tokens_openai_chat(self) -> None:
        mock_span = MagicMock()
        recorder = LLMTraceRecorder(span=mock_span)

        mock_response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1500,
                completion_tokens=200,
                total_tokens=1700,
                prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
            )
        )
        recorder.record_response(mock_response)
        mock_span.set_attribute.assert_any_call("gen_ai.usage.cached_tokens", 1024)

    def test_otel_span_records_cached_tokens_openai_responses(self) -> None:
        mock_span = MagicMock()
        recorder = LLMTraceRecorder(span=mock_span)

        mock_response = {
            "usage": {
                "input_tokens": 3000,
                "output_tokens": 400,
                "input_tokens_details": {"cached_tokens": 2048},
            }
        }
        recorder.record_response(mock_response)
        mock_span.set_attribute.assert_any_call("gen_ai.usage.cached_tokens", 2048)

    def test_otel_span_records_cached_tokens_gemini(self) -> None:
        mock_span = MagicMock()
        recorder = LLMTraceRecorder(span=mock_span)

        mock_response = {
            "usageMetadata": {
                "promptTokenCount": 5000,
                "candidatesTokenCount": 600,
                "cachedContentTokenCount": 4096,
            }
        }
        recorder.record_response(mock_response)
        mock_span.set_attribute.assert_any_call("gen_ai.usage.cached_tokens", 4096)

    def test_quality_usage_cached_tokens_field(self) -> None:
        usage = QualityUsage(
            input_tokens=2000,
            cached_input_tokens=1500,
            output_tokens=300,
            total_tokens=2300,
        )
        self.assertEqual(usage.input_tokens, 2000)
        self.assertEqual(usage.cached_input_tokens, 1500)
        self.assertEqual(usage.output_tokens, 300)

    def test_l2_healing_result_cached_tokens_field(self) -> None:
        from dual_pipeline.l2_healing_contracts import L2HealingResult

        res = L2HealingResult(
            status="HEALED",
            healed_artifact={"decision": "approved"},
            patch_diff="Applied surgical fix",
            tokens_used=1200,
            cached_tokens_used=1024,
            latency_ms=45.2,
        )
        self.assertEqual(res.cached_tokens_used, 1024)
        d = res.to_dict()
        self.assertEqual(d["cached_tokens_used"], 1024)


if __name__ == "__main__":
    unittest.main()
