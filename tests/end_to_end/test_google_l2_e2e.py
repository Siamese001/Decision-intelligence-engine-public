"""End-to-end lifecycle test for Google Gemini L2 reasoning provider."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

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
from dual_pipeline.l2_providers import (
    GoogleReasoningProvider,
    L2ProviderRequest,
    L2ProviderResponse,
    UnifiedL2ProviderFactory,
)
from dual_pipeline.l2_providers_core.provider import build_call_record


def _create_e2e_config() -> L2Config:
    pricing = PricingPolicy(
        input_per_million=0.10,
        cached_input_per_million=0.025,
        output_per_million=0.40,
    )
    standard_policy = ModelPolicy(
        provider="google",
        model_family="gemini-3.8-flash",
        model="gemini-3.8-flash",
        assurance_profile=QueryAssuranceProfile.STANDARD,
        reasoning_effort="high",
        maximum_primary_calls_per_run=1,
        maximum_repair_calls_per_run=1,
        maximum_escalation_calls_per_run=1,
        pricing=pricing,
    )
    return L2Config(
        schema_version="4.0.0",
        mode="conditional",
        provider_class="google",
        provider_egress_allowed=True,
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        api_key_env="GOOGLE_API_KEY",
        local_env_file="env_di",
        standard=standard_policy,
        enhanced=standard_policy,
        output=OutputPolicy(structured_output_required=True, maximum_output_tokens=4096),
        reliability=ReliabilityPolicy(retry_count=0, timeout_seconds=30),
        request_budget=RequestBudgetPolicy(maximum_provider_request_chars=256000),
        config_digest="e2e-config-digest",
    )


class TestGoogleL2E2E(unittest.TestCase):
    """Exercises full L2 lifecycle using Google Gemini provider."""

    def setUp(self) -> None:
        self.config = _create_e2e_config()
        self.model_policy = self.config.standard

    def test_full_google_l2_lifecycle(self) -> None:
        expected_output = {
            "schema_version": "nee-governed-answer-1.0.0",
            "query_type": "opportunity_nba",
            "current_state": [{"statement": "Current state is stable", "citations": ["rec-1"], "evidence_ids": ["ev-1"], "lane": "approved"}],
            "patterns": [],
            "pain_points": [],
            "opportunities": [{"statement": "Expand automated testing", "citations": ["rec-1"], "evidence_ids": ["ev-1"], "lane": "approved"}],
            "guidance": [],
            "hypotheses": [],
            "source_evidence": [],
            "next_best_actions": [{"action": "Deploy wave 1 provider", "priority": "high", "citations": ["rec-1"]}],
            "rationale": "Provider modernization reduces blended token costs by 93%.",
            "counterevidence": [],
            "missing_information": [],
            "risk_assessment": "Low operational risk with comprehensive unit and adversarial tests.",
            "unresolved_contradictions": [],
            "recommended_stance": "proceed",
            "required_actions": [],
            "confidence_score": 0.95,
        }

        mock_gemini_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(expected_output)}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1250,
                "cachedContentTokenCount": 350,
                "candidatesTokenCount": 420,
                "totalTokenCount": 1670,
            },
            "modelVersion": "gemini-3.8-flash",
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_gemini_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        # 1. Factory initialization via Unified factory
        unified_factory = UnifiedL2ProviderFactory(self.config)
        with patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="test-key-e2e"):
            provider = unified_factory(self.model_policy)
            self.assertIsInstance(provider, GoogleReasoningProvider)
            self.assertEqual(provider.provider_id, "google")

            # 2. Formulate L2 request with evidence packet
            request = L2ProviderRequest(
                purpose=CallPurpose.PRIMARY,
                question="What is the next best action for cost optimization?",
                plan={
                    "query_type": "opportunity_nba",
                    "retrieval_mode": "strategic",
                    "prompt_blueprint": "v1",
                },
                evidence_packet={
                    "packet_digest": "p" * 64,
                    "approved": [
                        {
                            "record": {
                                "record_id": "rec-1",
                                "evidence_ids": ["ev-1"],
                                "title": "LLM Cost Benchmark",
                                "retrieval_text": "Flash 3.8 achieves 93% cost savings",
                            }
                        }
                    ],
                    "candidate": [],
                    "external_public": [],
                },
                prompt={"template_version": "v1", "slots": {}},
            )

            # 3. Execute generation
            with patch("urllib.request.urlopen", return_value=mock_resp):
                response = provider.generate(request)

            # 4. Verify L2ProviderResponse
            self.assertIsInstance(response, L2ProviderResponse)
            self.assertEqual(response.model, "gemini-3.8-flash")
            self.assertEqual(response.usage.input_tokens, 1250)
            self.assertEqual(response.usage.cached_input_tokens, 350)
            self.assertEqual(response.usage.output_tokens, 420)
            self.assertEqual(response.output["recommended_stance"], "proceed")

            # 5. Build CallRecord and verify cost accounting
            record = build_call_record(
                model_policy=self.model_policy,
                model_profile="standard",
                purpose=CallPurpose.PRIMARY,
                ordinal=1,
                response=response,
                latency_ms=response.latency_ms,
            )
            self.assertEqual(record.status, "PASS")
            self.assertEqual(record.provider, "google")
            self.assertEqual(record.model, "gemini-3.8-flash")
            self.assertGreater(record.estimated_total_cost, 0.0)
            # Input cost: (900 uncached * 0.10 + 350 cached * 0.025) / 1M = (90 + 8.75) / 1M = 0.00009875
            # Output cost: 420 * 0.40 / 1M = 0.000168
            self.assertAlmostEqual(record.estimated_total_cost, 0.00026675, places=6)


if __name__ == "__main__":
    unittest.main()
