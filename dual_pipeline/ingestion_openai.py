"""Lazy OpenAI Responses API provider for advisory ingestion reasoning only."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from config.credentials import CredentialManager

from .core import ROOT, digest
from .ingestion_reasoning import (
    IngestionModelPolicy,
    IngestionProviderError,
    IngestionProviderRequest,
    IngestionProviderResponse,
    IngestionReasoningPolicy,
    build_ingestion_provider_body,
    ingestion_system_instruction,
    ingestion_user_payload,
)


def _key_from_local_env(policy: IngestionReasoningPolicy) -> str | None:
    path = Path(policy.local_env_file)
    if not path.is_absolute():
        path = ROOT / path
    return CredentialManager.load_local_env_key(path, policy.api_key_env)


def resolve_ingestion_openai_api_key(policy: IngestionReasoningPolicy) -> str:
    """Resolve the secret only after L2D selects a provider call."""

    value = CredentialManager.get_credential(policy.api_key_env, "").strip()
    if not value:
        value = _key_from_local_env(policy) or ""
    if not value:
        raise IngestionProviderError(
            "MISSING_API_KEY",
            f"{policy.api_key_env} is unavailable to the selected provider",
        )
    return value


@dataclass(frozen=True)
class OpenAIIngestionProviderFactory:
    """Construction is side-effect free; provider creation resolves the key."""

    policy: IngestionReasoningPolicy

    def __call__(
        self, model_policy: IngestionModelPolicy
    ) -> "OpenAIIngestionReasoningProvider":
        return OpenAIIngestionReasoningProvider(self.policy, model_policy)


class OpenAIIngestionReasoningProvider:
    provider_id = "openai"

    def __init__(
        self,
        policy: IngestionReasoningPolicy,
        model_policy: IngestionModelPolicy,
    ) -> None:
        if not policy.provider_egress_allowed:
            raise IngestionProviderError(
                "PROVIDER_EGRESS_NOT_AUTHORIZED",
                "external provider egress is not authorized by policy",
            )
        if model_policy.provider != self.provider_id:
            raise IngestionProviderError(
                "UNSUPPORTED_MODEL",
                "selected model does not belong to the governed provider class",
            )
        self.policy = policy
        self.model_policy = model_policy
        self._api_key = resolve_ingestion_openai_api_key(policy)

    @staticmethod
    def _instruction(purpose: str) -> str:
        return ingestion_system_instruction(purpose)

    @staticmethod
    def _user_payload(request: IngestionProviderRequest) -> dict[str, Any]:
        return ingestion_user_payload(request)

    def _body(self, request: IngestionProviderRequest) -> dict[str, Any]:
        return build_ingestion_provider_body(
            policy=self.policy,
            model_policy=self.model_policy,
            request=request,
        )

    @staticmethod
    def _safe_error_code(raw: bytes) -> str | None:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        error = parsed.get("error") if isinstance(parsed, Mapping) else None
        if not isinstance(error, Mapping):
            return None
        code = error.get("code", error.get("type"))
        return str(code)[:128] if code is not None else None

    @staticmethod
    def _extract_output_text(response: Mapping[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str):
            return direct
        for item in response.get("output", []):
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, Mapping) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        return text
        raise IngestionProviderError(
            "INVALID_STRUCTURED_RESPONSE",
            "provider response did not contain structured output text",
        )

    @staticmethod
    def _usage(response: Mapping[str, Any]) -> tuple[int, int, int, int]:
        raw = response.get("usage")
        if not isinstance(raw, Mapping):
            return 0, 0, 0, 0
        details = raw.get("input_tokens_details")
        cached = details.get("cached_tokens", 0) if isinstance(details, Mapping) else 0

        def token(name: str, value: Any) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise IngestionProviderError(
                    "INVALID_STRUCTURED_RESPONSE",
                    f"provider reported invalid {name}",
                )
            return value

        input_tokens = token("input_tokens", raw.get("input_tokens", 0))
        cached_tokens = min(
            token("cached_input_tokens", cached),
            input_tokens,
        )
        output_tokens = token("output_tokens", raw.get("output_tokens", 0))
        total_tokens = token(
            "total_tokens",
            raw.get("total_tokens", input_tokens + output_tokens),
        )
        return input_tokens, cached_tokens, output_tokens, total_tokens

    def generate(
        self, request: IngestionProviderRequest
    ) -> IngestionProviderResponse:
        body = json.dumps(
            self._body(request), separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self.policy.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                http_request, timeout=self.policy.timeout_seconds
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            safe_code = self._safe_error_code(exc.read())
            if exc.code == 400 and safe_code in {
                "model_not_found",
                "unsupported_model",
                "invalid_model",
            }:
                code = "UNSUPPORTED_MODEL"
            elif exc.code == 429 and safe_code in {
                "insufficient_quota",
                "billing_hard_limit_reached",
            }:
                code = "BUDGET_EXHAUSTED"
            else:
                code = "PROVIDER_UNAVAILABLE"
            raise IngestionProviderError(
                code, f"external provider returned HTTP {exc.code}"
            ) from None
        except (TimeoutError, socket.timeout) as exc:
            raise IngestionProviderError(
                "PROVIDER_TIMEOUT", "external provider request timed out"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                code = "PROVIDER_TIMEOUT"
                message = "external provider request timed out"
            else:
                code = "PROVIDER_UNAVAILABLE"
                message = "external provider is unavailable"
            raise IngestionProviderError(code, message) from None
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IngestionProviderError(
                "INVALID_STRUCTURED_RESPONSE",
                "provider returned an invalid response envelope",
            ) from exc
        if not isinstance(parsed, Mapping):
            raise IngestionProviderError(
                "INVALID_STRUCTURED_RESPONSE",
                "provider returned a non-object response envelope",
            )
        delivered_model = parsed.get("model")
        if delivered_model != self.model_policy.model:
            raise IngestionProviderError(
                "UNSUPPORTED_MODEL",
                "provider response model did not match the selected exact model",
            )
        try:
            output: Any = json.loads(self._extract_output_text(parsed))
        except json.JSONDecodeError as exc:
            raise IngestionProviderError(
                "INVALID_STRUCTURED_RESPONSE",
                "provider output was not valid JSON",
            ) from exc
        input_tokens, cached_tokens, output_tokens, total_tokens = self._usage(parsed)
        return IngestionProviderResponse(
            output=output,
            response_digest=digest(parsed),
            requested_model=self.model_policy.model,
            delivered_model=delivered_model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )


__all__ = [
    "OpenAIIngestionProviderFactory",
    "OpenAIIngestionReasoningProvider",
    "resolve_ingestion_openai_api_key",
]
