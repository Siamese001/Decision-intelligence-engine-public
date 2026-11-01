"""Google Gemini provider implementation for X1 quality controls."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping

from config.credentials import CredentialManager

from ..core import canonical_json, digest
from ..parameters import get_float_parameter
from .models import (
    QualityFailureCode,
    QualityModelPolicy,
    QualityProviderError,
    QualityProviderRequest,
    QualityProviderResponse,
    QualityUsage,
    X1QualityConfig,
)
from .rubrics import build_quality_provider_body


def resolve_google_api_key(config: X1QualityConfig) -> str:
    value = CredentialManager.get_credential(config.api_key_env, "").strip()
    if not value:
        raise QualityProviderError(
            QualityFailureCode.MISSING_API_KEY,
            f"{config.api_key_env} is unavailable to the quality provider",
        )
    return value


resolve_gemini_api_key = resolve_google_api_key


class GoogleQualityProvider:
    """Google Gemini adapter, inert until constructed from an authorized policy."""

    provider_id = "google"

    def __init__(self, config: X1QualityConfig, model_policy: QualityModelPolicy) -> None:
        if not config.provider_egress_allowed:
            raise QualityProviderError(
                QualityFailureCode.PROVIDER_EGRESS_NOT_AUTHORIZED,
                "quality-provider egress is not authorized",
            )
        if model_policy.provider != self.provider_id:
            raise QualityProviderError(
                QualityFailureCode.PROVIDER_UNAVAILABLE,
                "quality model is not owned by the governed Google provider",
            )
        self.config = config
        self.model_policy = model_policy
        self._api_key = resolve_google_api_key(config)

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        body = build_quality_provider_body(
            config=self.config,
            model_policy=self.model_policy,
            request=request,
        )
        endpoint = self.config.endpoint_template.format(model=self.model_policy.model)
        started = time.perf_counter()
        http_request = urllib.request.Request(
            endpoint,
            data=canonical_json(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
        )
        timeout = get_float_parameter("timeouts.llm_sync_seconds", 60.0)
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise QualityProviderError(
                QualityFailureCode.PROVIDER_UNAVAILABLE,
                "quality provider is unavailable",
            ) from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
            candidates = parsed.get("candidates", [])
            content = candidates[0].get("content", {}) if candidates else {}
            parts = content.get("parts", []) if isinstance(content, Mapping) else []
            text = parts[0].get("text") if parts and isinstance(parts[0], Mapping) else None
            output = json.loads(text) if isinstance(text, str) else None
        except (UnicodeDecodeError, json.JSONDecodeError, IndexError, AttributeError) as exc:
            raise QualityProviderError(
                QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
                "quality provider did not return a structured JSON verdict",
            ) from exc
        usage_metadata = parsed.get("usageMetadata", {}) if isinstance(parsed, Mapping) else {}
        input_tokens = int(usage_metadata.get("promptTokenCount", 0)) if isinstance(usage_metadata, Mapping) else 0
        output_tokens = int(usage_metadata.get("candidatesTokenCount", 0)) if isinstance(usage_metadata, Mapping) else 0
        total_tokens = int(usage_metadata.get("totalTokenCount", input_tokens + output_tokens)) if isinstance(usage_metadata, Mapping) else input_tokens + output_tokens

        from dual_pipeline.prompt_cache import normalize_cached_tokens

        cached_tokens = normalize_cached_tokens(parsed, provider="gemini")
        return QualityProviderResponse(
            output=output,
            response_digest=digest(output),
            model=self.model_policy.model,
            usage=QualityUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )


@dataclass(frozen=True)
class GoogleQualityProviderFactory:
    config: X1QualityConfig

    def __call__(self, model_policy: QualityModelPolicy) -> GoogleQualityProvider:
        return GoogleQualityProvider(self.config, model_policy)
