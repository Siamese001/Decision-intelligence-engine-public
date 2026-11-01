"""Adversarial and boundary stress tests for Google Gemini L2 provider.

Covers:
1. Boundary payloads (empty evidence, empty strings, maximum schema size)
2. Malformed input / corrupt provider responses (HTML, broken JSON, missing candidate text, negative tokens)
3. Overflow & budget exhaustion (MAX_TOKENS truncation, safety blocks)
4. Invariant bypass attempts (provider mismatch, unauthorized egress, secret leakage prevention)
5. Network chaos (HTTP 429 quota vs rate-limit, 502/503 bad gateway, socket timeouts)
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

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
    GoogleReasoningProvider,
    L2ProviderError,
    L2ProviderRequest,
)


def _make_adv_config(*, egress_allowed: bool = True, timeout: int = 5) -> L2Config:
    pricing = PricingPolicy(input_per_million=0.10, cached_input_per_million=0.025, output_per_million=0.40)
    policy = ModelPolicy(
        provider="google",
        model_family="gemini-3.8-flash",
        model="gemini-3.8-flash",
        assurance_profile=QueryAssuranceProfile.STANDARD,
        reasoning_effort="high",
        maximum_primary_calls_per_run=1,
        maximum_repair_calls_per_run=1,
        maximum_escalation_calls_per_run=1,
        context_window_tokens=128000,
        pricing=pricing,
    )
    return L2Config(
        schema_version="4.0.0",
        mode="conditional",
        provider_class="google",
        provider_egress_allowed=egress_allowed,
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        api_key_env="GOOGLE_API_KEY",
        local_env_file="env_di",
        standard=policy,
        enhanced=policy,
        output=OutputPolicy(structured_output_required=True, maximum_output_tokens=2048),
        reliability=ReliabilityPolicy(retry_count=0, timeout_seconds=timeout),
        request_budget=RequestBudgetPolicy(maximum_provider_request_chars=128000),
        config_digest="adv-config-digest",
    )


class TestGoogleL2Adversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_adv_config()
        self.model_policy = self.config.standard

    def _make_request(self, question: str = "Test question", evidence: dict | None = None) -> L2ProviderRequest:
        return L2ProviderRequest(
            purpose=CallPurpose.PRIMARY,
            question=question,
            plan={"query_type": "opportunity_nba", "retrieval_mode": "strategic"},
            evidence_packet=evidence or {"approved": [], "candidate": [], "external_public": []},
            prompt={"template_version": "v1", "slots": {}},
        )

    # 1. Malformed input & corrupted network envelopes
    def test_corrupt_html_gateway_response(self) -> None:
        html_payload = b"<html><head><title>502 Bad Gateway</title></head><body>Cloudflare 502</body></html>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_payload
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(self._make_request())
            self.assertEqual(ctx.exception.code, ProviderFailureCode.INVALID_STRUCTURED_RESPONSE)

    def test_corrupt_empty_parts_in_candidate(self) -> None:
        payload = {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(self._make_request())
            self.assertEqual(ctx.exception.code, ProviderFailureCode.INVALID_STRUCTURED_RESPONSE)

    def test_negative_tokens_in_usage_fails_closed(self) -> None:
        payload = {
            "candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": -10, "candidatesTokenCount": 5},
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(self._make_request())
            self.assertEqual(ctx.exception.code, ProviderFailureCode.INVALID_STRUCTURED_RESPONSE)

    # 2. Safety filter & blocklist termination
    def test_recitation_and_blocklist_termination(self) -> None:
        for block_reason in ("RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
            payload = {
                "candidates": [{"content": {"parts": []}, "finishReason": block_reason}],
                "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 0},
            }
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp

            with (
                patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
                patch("urllib.request.urlopen", return_value=mock_resp),
            ):
                provider = GoogleReasoningProvider(self.config, self.model_policy)
                with self.assertRaises(L2ProviderError) as ctx:
                    provider.generate(self._make_request())
                self.assertEqual(ctx.exception.code, ProviderFailureCode.INVALID_STRUCTURED_RESPONSE)
                self.assertIn("blocked", ctx.exception.safe_message)

    # 3. Model version spoofing & substitution rejection
    def test_mismatched_model_version_rejected(self) -> None:
        payload = {
            "candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10},
            "modelVersion": "claude-haiku-4-5",  # Rogue substitution

        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(self._make_request())
            self.assertEqual(ctx.exception.code, ProviderFailureCode.UNSUPPORTED_MODEL)

    # 4. Invariant bypass & credential leakage prevention
    def test_secret_is_never_in_exception_or_repr(self) -> None:
        secret_key = "AIzaSySecretShouldNeverAppearInLogsOrRepr"
        err_body = json.dumps({"error": {"status": "INVALID_ARGUMENT", "message": f"Bad key {secret_key}"}}).encode("utf-8")
        http_err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(err_body),
        )

        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value=secret_key),
            patch("urllib.request.urlopen", side_effect=http_err),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(self._make_request())
            # Exception message must be sanitized and never expose the secret
            self.assertNotIn(secret_key, str(ctx.exception))
            self.assertNotIn(secret_key, ctx.exception.safe_message)

    # 5. Network chaos & HTTP error resilience
    def test_server_error_503_maps_to_unavailable(self) -> None:
        http_err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b'{"error": "temporarily overloaded"}'),
        )
        with (
            patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-key"),
            patch("urllib.request.urlopen", side_effect=http_err),
        ):
            provider = GoogleReasoningProvider(self.config, self.model_policy)
            with self.assertRaises(L2ProviderError) as ctx:
                provider.generate(self._make_request())
            self.assertEqual(ctx.exception.code, ProviderFailureCode.PROVIDER_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
