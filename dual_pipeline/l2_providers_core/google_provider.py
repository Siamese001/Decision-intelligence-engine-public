"""Google Gemini reasoning provider implementation, factory, and call record construction."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from dual_pipeline.core import canonical_json, digest
from dual_pipeline.l2_output_contract import record_bound_decision_schema
from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Config,
    ModelPolicy,
    ProviderFailureCode,
    canonical_query_type,
)

from .auth import resolve_google_api_key
from .constants import DECISION_MODEL_OUTPUT_SCHEMA
from .payload import l2_system_instruction, l2_user_payload
from .types import (
    L2ProviderError,
    L2ProviderRequest,
    L2ProviderResponse,
    ReasoningProvider,
    TokenUsage,
)


class GoogleReasoningProvider:
    provider_id = "google"

    def __init__(self, config: L2Config, model_policy: ModelPolicy) -> None:
        from dual_pipeline.l2_providers_core.sentinel import require_l2d_authorization
        require_l2d_authorization()

        if not config.provider_egress_allowed:
            raise L2ProviderError(
                ProviderFailureCode.PROVIDER_EGRESS_NOT_AUTHORIZED,
                "external provider egress is not authorized",
            )
        if model_policy.provider != self.provider_id:
            raise L2ProviderError(
                ProviderFailureCode.UNSUPPORTED_MODEL,
                "selected model does not belong to the governed provider class",
            )
        self.config = config
        self.model_policy = model_policy
        self._api_key = resolve_google_api_key(config)

    def _endpoint(self) -> str:
        if "{model}" in self.config.endpoint:
            return self.config.endpoint.format(model=self.model_policy.model)
        if "googleapis.com" in self.config.endpoint:
            clean = self.config.endpoint.rstrip("/")
            if ":generateContent" in clean:
                return clean
            return f"{clean}/models/{self.model_policy.model}:generateContent"
        return f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_policy.model}:generateContent"

    @classmethod
    def _sanitize_gemini_schema(cls, schema: Any) -> Any:
        """Adapts standard JSON Schema to Google Gemini OpenAPI-compatible schema subset."""
        if not isinstance(schema, Mapping):
            return schema
        clean: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "additionalProperties":
                continue
            elif key == "const":
                if isinstance(value, str):
                    clean["enum"] = [value]
                elif isinstance(value, bool):
                    clean["type"] = "boolean"
                elif isinstance(value, int):
                    clean["type"] = "integer"
            elif isinstance(value, Mapping):
                clean[key] = cls._sanitize_gemini_schema(value)
            elif isinstance(value, list):
                clean[key] = [
                    cls._sanitize_gemini_schema(item) if isinstance(item, Mapping) else item
                    for item in value
                ]
            else:
                clean[key] = value
        return clean

    def _build_body(self, request: L2ProviderRequest) -> dict[str, Any]:
        schema = (
            DECISION_MODEL_OUTPUT_SCHEMA
            if getattr(self.config, "prompt_cache_enabled", True)
            else record_bound_decision_schema(
                DECISION_MODEL_OUTPUT_SCHEMA,
                request.evidence_packet,
                expected_query_type=(
                    str(request.plan["query_type"])
                    if request.plan.get("query_type") is not None
                    else None
                ),
                canonicalize_query_type=canonical_query_type,
            )
        )
        system_instruction = l2_system_instruction(request.purpose)
        user_payload_data = l2_user_payload(request)

        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "maxOutputTokens": self.config.output.maximum_output_tokens,
        }
        if self.config.output.structured_output_required and schema:
            generation_config["responseSchema"] = self._sanitize_gemini_schema(schema)

        effort = (self.model_policy.reasoning_effort or "").upper()
        if effort in ("LOW", "MEDIUM", "HIGH"):
            generation_config["thinkingConfig"] = {"thinkingLevel": effort}
        elif effort in ("MAX", "XHIGH"):
            generation_config["thinkingConfig"] = {"thinkingLevel": "HIGH"}

        thinking_budget = request.plan.get("thinking_budget") if isinstance(request.plan, Mapping) else None
        if isinstance(thinking_budget, int) and thinking_budget > 0:
            generation_config.setdefault("thinkingConfig", {})["thinkingBudget"] = thinking_budget

        body_payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": canonical_json(user_payload_data)}]}],
            "generationConfig": generation_config,
        }
        cached_content = (
            request.plan.get("cached_content_name")
            if isinstance(request.plan, Mapping)
            else None
        ) or getattr(self.config, "cached_content_name", None)
        if cached_content:
            body_payload["cachedContent"] = str(cached_content)
        return body_payload

    @staticmethod
    def _safe_error_code(raw: bytes) -> str | None:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        error = parsed.get("error") if isinstance(parsed, Mapping) else None
        if not isinstance(error, Mapping):
            return None
        status = error.get("status")
        message = error.get("message")
        return str(status or message)[:128] if (status or message) is not None else None

    @staticmethod
    def _extract_output_text(response: Mapping[str, Any]) -> str:
        candidates = response.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            raise L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "provider response contained no candidates",
            )
        candidate = candidates[0]
        if not isinstance(candidate, Mapping):
            raise L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "provider candidate is not an object",
            )
        content = candidate.get("content", {})
        if not isinstance(content, Mapping):
            raise L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "provider candidate content is not an object",
            )
        parts = content.get("parts", [])
        if not isinstance(parts, list):
            raise L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "provider content parts is not a list",
            )
        for part in parts:
            if isinstance(part, Mapping) and "text" in part:
                text = part["text"]
                if isinstance(text, str):
                    return text
        raise L2ProviderError(
            ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
            "provider response did not contain structured output text",
        )

    @staticmethod
    def _usage(response: Mapping[str, Any]) -> TokenUsage:
        usage_meta = response.get("usageMetadata")
        if not isinstance(usage_meta, Mapping):
            return TokenUsage()

        def token(name: str, value: Any) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    f"provider reported invalid {name}",
                )
            return value

        input_tokens = token("promptTokenCount", usage_meta.get("promptTokenCount", 0))
        cached = token("cachedContentTokenCount", usage_meta.get("cachedContentTokenCount", 0))
        output_tokens = token("candidatesTokenCount", usage_meta.get("candidatesTokenCount", 0))
        total_tokens = token(
            "totalTokenCount", usage_meta.get("totalTokenCount", input_tokens + output_tokens)
        )

        return TokenUsage(
            input_tokens=input_tokens,
            cached_input_tokens=min(cached, input_tokens),
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            usage_reported=True,
        )

    def generate(self, request: L2ProviderRequest) -> L2ProviderResponse:
        body_dict = self._build_body(request)
        body = canonical_json(body_dict).encode("utf-8")
        endpoint = self._endpoint()
        http_request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                http_request, timeout=self.config.reliability.timeout_seconds
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            safe_code = self._safe_error_code(exc.read())
            if exc.code == 429:
                code = (
                    ProviderFailureCode.BUDGET_EXHAUSTED
                    if safe_code in {"RESOURCE_EXHAUSTED", "insufficient_quota"}
                    else ProviderFailureCode.PROVIDER_UNAVAILABLE
                )
            elif exc.code == 400 and safe_code in {
                "NOT_FOUND",
                "model_not_found",
                "INVALID_ARGUMENT",
            }:
                code = ProviderFailureCode.UNSUPPORTED_MODEL
            else:
                code = ProviderFailureCode.PROVIDER_UNAVAILABLE
            raise L2ProviderError(
                code, f"external provider returned HTTP {exc.code}"
            ) from None
        except (TimeoutError, socket.timeout) as exc:
            raise L2ProviderError(
                ProviderFailureCode.PROVIDER_TIMEOUT,
                "external provider request timed out",
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                code = ProviderFailureCode.PROVIDER_TIMEOUT
                message = "external provider request timed out"
            else:
                code = ProviderFailureCode.PROVIDER_UNAVAILABLE
                message = "external provider is unavailable"
            raise L2ProviderError(code, message) from None

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "provider returned an invalid response envelope",
            ) from exc
        if not isinstance(parsed, Mapping):
            raise L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "provider returned a non-object response envelope",
            )

        returned_model = parsed.get("modelVersion")
        if (
            returned_model is not None
            and self.model_policy.model not in str(returned_model)
            and str(returned_model) not in self.model_policy.model
        ):
            raise L2ProviderError(
                ProviderFailureCode.UNSUPPORTED_MODEL,
                "provider response model did not match the selected model",
            )

        usage = self._usage(parsed)
        response_digest = digest(parsed)

        candidates = parsed.get("candidates", [])
        if candidates and isinstance(candidates[0], Mapping):
            finish_reason = str(candidates[0].get("finishReason", "")).upper()
            if finish_reason in ("MAX_TOKENS", "LENGTH"):
                raise L2ProviderError(
                    ProviderFailureCode.BUDGET_EXHAUSTED,
                    "provider response incomplete: max_output_tokens",
                    usage=usage,
                    response_digest=response_digest,
                    latency_ms=latency_ms,
                )
            if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
                raise L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    f"provider response blocked: {finish_reason.lower()}",
                    usage=usage,
                    response_digest=response_digest,
                    latency_ms=latency_ms,
                )

        output_text = self._extract_output_text(parsed)
        try:
            output: Any = json.loads(output_text)
        except json.JSONDecodeError:
            output = output_text

        return L2ProviderResponse(
            output=output,
            response_digest=response_digest,
            model=self.model_policy.model,
            usage=usage,
            latency_ms=latency_ms,
        )


@dataclass(frozen=True)
class GoogleProviderFactory:
    """Lazy factory; construction performs no key access or network operation."""

    config: L2Config

    def __call__(self, model_policy: ModelPolicy) -> ReasoningProvider:
        return GoogleReasoningProvider(self.config, model_policy)


@dataclass(frozen=True)
class UnifiedL2ProviderFactory:
    """Dispatches to OpenAI or Google provider based on model_policy.provider."""

    config: L2Config

    def __call__(self, model_policy: ModelPolicy) -> ReasoningProvider:
        if model_policy.provider == "google":
            return GoogleReasoningProvider(self.config, model_policy)
        from .provider import OpenAIReasoningProvider
        return OpenAIReasoningProvider(self.config, model_policy)


def select_l2_provider(
    config: L2Config,
    purpose: CallPurpose | str,
    assurance_profile: str = "standard",
) -> tuple[ModelPolicy, ReasoningProvider]:
    """Select the governed ModelPolicy and instantiated ReasoningProvider for a purpose."""
    model_policy = config.model_for_purpose(purpose, assurance_profile)
    factory = UnifiedL2ProviderFactory(config)
    return model_policy, factory(model_policy)


GoogleProvider = GoogleReasoningProvider
