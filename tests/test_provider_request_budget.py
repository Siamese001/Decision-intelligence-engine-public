#!/usr/bin/env python3
"""Unit tests for exact provider request measurement and read/write telemetry."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from dual_pipeline.provider_request import (
    aggregate_token_usage,
    build_responses_request,
    measure_responses_request,
    provider_request_budget_verdict,
)


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int


class ProviderRequestBudgetTests(unittest.TestCase):
    def _body(self) -> dict:
        return build_responses_request(
            model="gpt-5.6-terra",
            system_instruction="Use only sealed evidence.",
            user_payload={"question": "What changed?", "evidence": ["record-1"]},
            reasoning_effort="medium",
            schema_name="test_output",
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"answer": {"type": "string"}},
            },
            maximum_output_tokens=512,
        )

    def test_exact_request_measurement_is_deterministic(self) -> None:
        first = measure_responses_request(self._body())
        second = measure_responses_request(self._body())
        self.assertEqual(first, second)
        self.assertEqual(64, len(first["request_payload_digest"]))
        self.assertGreater(first["request_serialized_chars"], first["request_input_chars"])
        self.assertGreater(first["request_schema_chars"], 0)

    def test_budget_boundary_allows_equal_and_blocks_larger(self) -> None:
        metrics = measure_responses_request(self._body())
        exact = metrics["request_serialized_chars"]
        self.assertEqual(
            "ALLOW",
            provider_request_budget_verdict(
                request_metrics=metrics,
                maximum_provider_request_chars=exact,
            )["status"],
        )
        self.assertEqual(
            "BLOCK",
            provider_request_budget_verdict(
                request_metrics=metrics,
                maximum_provider_request_chars=exact - 1,
            )["status"],
        )

    def test_usage_aggregation_exposes_read_write_ratio_and_cache_share(self) -> None:
        usage = aggregate_token_usage(
            [
                Usage(100, 90, 10, 110),
                Usage(50, 30, 5, 55),
            ]
        )
        self.assertEqual(150, usage["input_tokens"])
        self.assertEqual(120, usage["cached_input_tokens"])
        self.assertEqual(30, usage["uncached_input_tokens"])
        self.assertEqual(15, usage["output_tokens"])
        self.assertEqual(10.0, usage["input_output_ratio"])
        self.assertEqual(0.8, usage["cached_input_share"])


if __name__ == "__main__":
    unittest.main()
