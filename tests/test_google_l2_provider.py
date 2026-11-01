"""Unit and integration tests for GoogleReasoningProvider and GoogleProviderFactory."""

from __future__ import annotations

import io
import json
import socket
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from config.credentials import CredentialManager
from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Config,
    ModelPolicy,
    OutputPolicy,
    PricingPolicy,
    ProviderFailureCode,
    QueryAssuranceProfile,
    ReliabilityPolicy,
    RequestBudgetPolicy,
)
from dual_pipeline.l2_providers import (
    GoogleProviderFactory,
    GoogleReasoningProvider,
    L2ProviderError,
    L2ProviderRequest,
    L2ProviderResponse,
    UnifiedL2ProviderFactory,
)


def _make_config(*, egress_allowed: bool = True, endpoint: str = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent") -> L2Config:
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
        provider_egress_allowed=egress_allowed,
        endpoint=endpoint,
        api_key_env="GOOGLE_API_KEY",
        local_env_file="env_di",

        standard=standard_policy,
        enhanced=standard_policy,
        output=OutputPolicy(structured_output_required=True, maximum_output_tokens=4096),
        reliability=ReliabilityPolicy(retry_count=0, timeout_seconds=30),
        request_budget=RequestBudgetPolicy(maximum_provider_request_chars=256000),
        config_digest="test-config-digest",
    )


def _make_request(*, question: str = "What are the key decisions?", query_type: str = "opportunity_nba") -> L2ProviderRequest:
    return L2ProviderRequest(
        purpose=CallPurpose.PRIMARY,
        question=question,
        plan={"query_type": query_type, "retrieval_mode": "strategic"},
        evidence_packet={
            "approved": [],
            "candidate": [],
            "external_public": [],
        },
        prompt={"template_version": "v1", "slots": {}},
    )


class TestGoogleReasoningProvider(unittest.TestCase):
    def setUp(self) -> None:
        from dual_pipeline.l2_providers_core.sentinel import authorize_provider_instantiation
        authorize_provider_instantiation()
        self.config = _make_config()
        self.model_policy = self.config.standard
        from dual_pipeline.l2_providers_core.sentinel import authorize_provider_instantiation
        authorize_provider_instantiation()

    def tearDown(self) -> None:
        from dual_pipeline.l2_providers_core.sentinel import reset_l2d_authorization
        reset_l2d_authorization()

    def test_provider_egress_not_authorized(self) -> None:
        cfg = _make_config(egress_allowed=False)
        with self.assertRaises(L2ProviderError) as ctx:
            GoogleReasoningProvider(cfg, self.model_policy)
        self.assertEqual(ctx.exception.code, ProviderFailureCode.PROVIDER_EGRESS_NOT_AUTHORIZED)

    def test_unsupported_model_provider(self) -> None:
        openai_policy = ModelPolicy(
            provider="openai",
            model_family="gpt-5.6-sol",
            model="gpt-5.6-sol",
            assurance_profile=QueryAssuranceProfile.STANDARD,
            reasoning_effort="high",
            maximum_primary_calls_per_run=1,
            maximum_repair_calls_per_run=1,
            maximum_escalation_calls_per_run=1,
            pricing=PricingPolicy(input_per_million=15.0, cached_input_per_million=3.75, output_per_million=60.0),
        )
        with self.assertRaises(L2ProviderError) as ctx:
            GoogleReasoningProvider(self.config, openai_policy)
        self.assertEqual(ctx.exception.code, ProviderFailureCode.UNSUPPORTED_MODEL)

    def test_missing_api_key(self) -> None:
        cfg = _make_config()
        with (
            patch.object(CredentialManager, "get_credential", return_value=""),
            patch("dual_pipeline.l2_providers_core.auth._key_from_ignored_local_env", return_value=""),
            self.assertRaises(L2ProviderError) as ctx,
        ):
            GoogleReasoningProvider(cfg, self.model_policy)
        self.assertEqual(ctx.exception.code, ProviderFailureCode.MISSING_API_KEY)

    def test_endpoint_resolution(self) -> None:
        with patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"):
            # Format with {model}
            cfg1 = _make_config(endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent")
            p1 = GoogleReasoningProvider(cfg1, self.model_policy)
            self.assertIn("models/gemini-3.8-flash:generateContent", p1._endpoint())

            # Base googleapis URL without {model}
            cfg2 = _make_config(endpoint="https://generativelanguage.googleapis.com/v1beta")
            p2 = GoogleReasoningProvider(cfg2, self.model_policy)
            self.assertIn("models/gemini-3.8-flash:generateContent", p2._endpoint())

            # Fallback URL
            cfg3 = _make_config(endpoint="https://api.openai.com/v1/responses")
            p3 = GoogleReasoningProvider(cfg3, self.model_policy)
            self.assertIn("generativelanguage.googleapis.com", p3._endpoint())
            self.assertIn("gemini-3.8-flash:generateContent", p3._endpoint())

    def test_body_construction(self) -> None:
        with patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            req = _make_request()
            body = provider._build_body(req)

            self.assertIn("systemInstruction", body)
            self.assertIn("contents", body)
            self.assertIn("generationConfig", body)

            gen_cfg = body["generationConfig"]
            self.assertEqual(gen_cfg["responseMimeType"], "application/json")
            self.assertEqual(gen_cfg["maxOutputTokens"], 4096)
            self.assertIn("responseSchema", gen_cfg)
            self.assertEqual(gen_cfg["thinkingConfig"]["thinkingLevel"], "HIGH")

    def test_output_text_extraction(self) -> None:
        valid_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": '{"result": "success"}'}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        text = GoogleReasoningProvider._extract_output_text(valid_response)
        self.assertEqual(text, '{"result": "success"}')

        # Empty candidates
        with self.assertRaises(L2ProviderError) as ctx:
            GoogleReasoningProvider._extract_output_text({"candidates": []})
        self.assertEqual(ctx.exception.code, ProviderFailureCode.INVALID_STRUCTURED_RESPONSE)

        # Missing parts
        with self.assertRaises(L2ProviderError) as ctx:
            GoogleReasoningProvider._extract_output_text({"candidates": [{"content": {}}]})
        self.assertEqual(ctx.exception.code, ProviderFailureCode.INVALID_STRUCTURED_RESPONSE)

    def test_usage_parsing(self) -> None:
        response = {
            "usageMetadata": {
                "promptTokenCount": 150,
                "cachedContentTokenCount": 50,
                "candidatesTokenCount": 75,
                "totalTokenCount": 225,
            }
        }
        usage = GoogleReasoningProvider._usage(response)
        self.assertEqual(usage.input_tokens, 150)
        self.assertEqual(usage.cached_input_tokens, 50)
        self.assertEqual(usage.output_tokens, 75)
        self.assertEqual(usage.total_tokens, 225)
        self.assertTrue(usage.usage_reported)

        # Missing usageMetadata
        empty_usage = GoogleReasoningProvider._usage({})
        self.assertEqual(empty_usage.input_tokens, 0)
        self.assertFalse(empty_usage.usage_reported)

    def test_mock_generate_roundtrip(self) -> None:
        mock_response_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps({"schema_version": "1.0.0", "status": "ok"})}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 100,
                "cachedContentTokenCount": 20,
                "candidatesTokenCount": 40,
                "totalTokenCount": 140,
            },
            "modelVersion": "gemini-3.8-flash",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            req = _make_request()
            res = provider.generate(req)

            self.assertIsInstance(res, L2ProviderResponse)
            self.assertEqual(res.model, "gemini-3.8-flash")
            self.assertEqual(res.output, {"schema_version": "1.0.0", "status": "ok"})
            self.assertEqual(res.usage.input_tokens, 100)
            self.assertEqual(res.usage.cached_input_tokens, 20)
            self.assertEqual(res.usage.output_tokens, 40)
            self.assertGreater(res.latency_ms, 0)

    def test_finish_reason_max_tokens(self) -> None:
        mock_response_data = {
            "candidates": [
                {
                    "content": {"parts": [{"text": '{"incomplete": '}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 4096},
            "modelVersion": "gemini-3.8-flash",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            req = _make_request()
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(req)
            self.assertEqual(ctx.exception.code, ProviderFailureCode.BUDGET_EXHAUSTED)

    def test_finish_reason_safety(self) -> None:
        mock_response_data = {
            "candidates": [
                {
                    "content": {"parts": []},
                    "finishReason": "SAFETY",
                }
            ],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 0},
            "modelVersion": "gemini-3.8-flash",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            req = _make_request()
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(req)
            self.assertEqual(ctx.exception.code, ProviderFailureCode.INVALID_STRUCTURED_RESPONSE)

    def test_http_429_quota_exhausted(self) -> None:
        err_body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}}).encode("utf-8")
        http_err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(err_body),
        )
        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", side_effect=http_err),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            req = _make_request()
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(req)
            self.assertEqual(ctx.exception.code, ProviderFailureCode.BUDGET_EXHAUSTED)

    def test_timeout_error(self) -> None:
        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            req = _make_request()
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(req)
            self.assertEqual(ctx.exception.code, ProviderFailureCode.PROVIDER_TIMEOUT)

    def test_factories(self) -> None:
        factory = GoogleProviderFactory(self.config)
        with patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"):
            provider = factory(self.model_policy)
            self.assertIsInstance(provider, GoogleReasoningProvider)
            self.assertEqual(provider.provider_id, "google")

        unified = UnifiedL2ProviderFactory(self.config)
        with patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"):
            p_google = unified(self.model_policy)
            self.assertIsInstance(p_google, GoogleReasoningProvider)

    def test_live_api_call_if_credentials_available(self) -> None:
        import os
        if os.environ.get("RUN_LIVE_TESTS") != "1":
            self.skipTest("Live tests require RUN_LIVE_TESTS=1")
        key = CredentialManager.get_credential("GOOGLE_API_KEY") or CredentialManager.get_credential("GEMINI_API_KEY")
        if not key:
            from config.credentials import resolve_ssot_env_file
            ssot_file = resolve_ssot_env_file()
            if ssot_file.is_file():
                for line in ssot_file.read_text().splitlines():
                    if line.startswith("GOOGLE_API_KEY=") or line.startswith("GEMINI_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not key:
            self.skipTest("Live GOOGLE_API_KEY not configured in environment")

        with patch.dict(os.environ, {"GOOGLE_API_KEY": key}):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            req = _make_request(question="What are the strategic priorities?")
            response = provider.generate(req)

            self.assertIsInstance(response, L2ProviderResponse)
            self.assertEqual(response.model, "gemini-3.8-flash")
            self.assertGreater(response.usage.input_tokens, 0)
            self.assertGreater(response.latency_ms, 0)
            self.assertIsNotNone(response.response_digest)



if __name__ == "__main__":
    unittest.main()
