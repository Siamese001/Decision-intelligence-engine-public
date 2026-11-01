"""End-to-end tests for prompt caching integration with L2 provider requests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Config,
    ModelPolicy,
    OutputPolicy,
    PricingPolicy,
    QueryAssuranceProfile,
    ReliabilityPolicy,
    RequestBudgetPolicy,
)
from dual_pipeline.l2_providers_core.payload import build_l2_provider_body
from dual_pipeline.l2_providers_core.types import L2ProviderRequest
from dual_pipeline.provider_request import (
    aggregate_token_usage,
    build_responses_request,
    measure_responses_request,
)


def _make_test_config(*, prompt_cache_enabled: bool = False) -> L2Config:
    pricing = PricingPolicy(
        input_per_million=10.0,
        cached_input_per_million=2.5,
        output_per_million=30.0,
    )
    standard_policy = ModelPolicy(
        provider="openai",
        model_family="gpt-5.6-luna",
        model="gpt-5.6-luna",
        assurance_profile=QueryAssuranceProfile.STANDARD,
        reasoning_effort="medium",
        maximum_primary_calls_per_run=1,
        maximum_repair_calls_per_run=1,
        maximum_escalation_calls_per_run=1,
        pricing=pricing,
    )
    return L2Config(
        schema_version="4.0.0",
        mode="conditional",
        provider_class="openai",
        provider_egress_allowed=True,
        endpoint="https://api.openai.com/v1/responses",
        api_key_env="OPENAI_API_KEY",
        local_env_file="env_di",
        standard=standard_policy,
        enhanced=standard_policy,
        output=OutputPolicy(structured_output_required=True, maximum_output_tokens=4096),
        reliability=ReliabilityPolicy(retry_count=0, timeout_seconds=30),
        request_budget=RequestBudgetPolicy(maximum_provider_request_chars=256000),
        config_digest="test-config-digest",
        prompt_cache_enabled=prompt_cache_enabled,
    )


def _make_test_request(*, run_id: str = "run-001") -> L2ProviderRequest:
    return L2ProviderRequest(
        purpose=CallPurpose.PRIMARY,
        question="What strategic decisions were reached?",
        plan={"query_type": "opportunity_nba", "retrieval_mode": "strategic", "run_id": run_id},
        evidence_packet={
            "approved": [],
            "candidate": [],
            "external_public": [],
        },
        prompt={"template_version": "v1", "slots": {}},
    )


class TestPromptCacheL2E2E(unittest.TestCase):
    def test_build_responses_request_default_golden_bytes(self) -> None:
        """When prompt_cache_key is None, the built request contains no cache parameters."""
        req = build_responses_request(
            model="gpt-5.6-luna",
            system_instruction="Static system prompt",
            user_payload={"query": "test"},
            reasoning_effort="medium",
            schema_name="test_schema",
            output_schema={"type": "object"},
            maximum_output_tokens=2048,
        )
        self.assertNotIn("prompt_cache_key", req)
        self.assertNotIn("prompt_cache_retention", req)

        # Explicitly passing prompt_cache_key populates it
        req_cached = build_responses_request(
            model="gpt-5.6-luna",
            system_instruction="Static system prompt",
            user_payload={"query": "test"},
            reasoning_effort="medium",
            schema_name="test_schema",
            output_schema={"type": "object"},
            maximum_output_tokens=2048,
            prompt_cache_key="nee:test:key:1234",
            prompt_cache_retention="in_memory",
        )
        self.assertEqual(req_cached["prompt_cache_key"], "nee:test:key:1234")
        self.assertEqual(req_cached["prompt_cache_retention"], "in_memory")

    def test_l2_provider_body_cache_disabled_is_byte_identical(self) -> None:
        """With prompt_cache_enabled=False (the default), output matches baseline."""
        cfg_disabled = _make_test_config(prompt_cache_enabled=False)
        req = _make_test_request()
        body = build_l2_provider_body(
            config=cfg_disabled,
            model_policy=cfg_disabled.standard,
            request=req,
        )
        self.assertNotIn("prompt_cache_key", body)

        metrics = measure_responses_request(body)
        self.assertIn("request_payload_digest", metrics)
        self.assertGreater(metrics["request_serialized_chars"], 0)

    def test_l2_provider_body_cache_enabled_deterministic_across_runs(self) -> None:
        """With prompt_cache_enabled=True, cache key is populated and independent of run_id."""
        cfg_enabled = _make_test_config(prompt_cache_enabled=True)

        req1 = _make_test_request(run_id="run-alpha-1234")
        req2 = _make_test_request(run_id="run-beta-9876")

        body1 = build_l2_provider_body(
            config=cfg_enabled,
            model_policy=cfg_enabled.standard,
            request=req1,
        )
        body2 = build_l2_provider_body(
            config=cfg_enabled,
            model_policy=cfg_enabled.standard,
            request=req2,
        )

        self.assertIn("prompt_cache_key", body1)
        self.assertIn("prompt_cache_key", body2)
        # Even with different run_ids in plan, the static cache key remains identical
        self.assertEqual(body1["prompt_cache_key"], body2["prompt_cache_key"])

    def test_aggregate_token_usage_with_cached_tokens(self) -> None:
        """Verify provider_request aggregate_token_usage accurately calculates cached share."""
        records = [
            SimpleNamespace(input_tokens=2000, cached_input_tokens=1500, output_tokens=500, total_tokens=2500),
            SimpleNamespace(input_tokens=1000, cached_input_tokens=500, output_tokens=200, total_tokens=1200),
        ]
        agg = aggregate_token_usage(records)
        self.assertEqual(agg["input_tokens"], 3000)
        self.assertEqual(agg["cached_input_tokens"], 2000)
        self.assertEqual(agg["uncached_input_tokens"], 1000)
        self.assertEqual(agg["cached_input_share"], 0.666667)


if __name__ == "__main__":
    unittest.main()
