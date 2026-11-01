"""OpenAI reasoning provider implementation, factory, and call record construction."""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from dual_pipeline.core import digest
from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Config,
    ModelPolicy,
    ProviderFailureCode,
)

from .auth import resolve_openai_api_key
from .payload import (
    build_l2_provider_body,
    l2_system_instruction,
    l2_user_payload,
)
from .types import (
    L2CallRecord,
    L2ProviderError,
    L2ProviderRequest,
    L2ProviderResponse,
    ReasoningProvider,
    TokenUsage,
)


class OpenAIReasoningProvider:
    provider_id = "openai"

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
        self._api_key = resolve_openai_api_key(config)

    @staticmethod
    def _system_instruction(purpose: CallPurpose) -> str:
        return l2_system_instruction(purpose)

    @staticmethod
    def _user_payload(request: L2ProviderRequest) -> dict[str, Any]:
        return l2_user_payload(request)

    def _body(self, request: L2ProviderRequest) -> dict[str, Any]:
        return build_l2_provider_body(
            config=self.config,
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
        raise L2ProviderError(
            ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
            "provider response did not contain structured output text",
        )

    @staticmethod
    def _incomplete_reason(response: Mapping[str, Any]) -> str | None:
        if response.get("status") != "incomplete":
            return None
        details = response.get("incomplete_details")
        raw_reason = details.get("reason") if isinstance(details, Mapping) else None
        if not isinstance(raw_reason, str) or not raw_reason:
            return "unspecified"
        sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", raw_reason)[:128]
        return sanitized or "unspecified"

    @staticmethod
    def _usage(response: Mapping[str, Any]) -> TokenUsage:
        raw = response.get("usage")
        if not isinstance(raw, Mapping):
            return TokenUsage()
        details = raw.get("input_tokens_details")
        cached = details.get("cached_tokens", 0) if isinstance(details, Mapping) else 0

        def token(name: str, value: Any) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    f"provider reported invalid {name}",
                )
            return value

        input_tokens = token("input_tokens", raw.get("input_tokens", 0))
        cached_tokens = token("cached_input_tokens", cached)
        output_tokens = token("output_tokens", raw.get("output_tokens", 0))
        total_tokens = token(
            "total_tokens", raw.get("total_tokens", input_tokens + output_tokens)
        )
        return TokenUsage(
            input_tokens=input_tokens,
            cached_input_tokens=min(cached_tokens, input_tokens),
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            usage_reported=True,
        )

    def generate(self, request: L2ProviderRequest) -> L2ProviderResponse:
        body = json.dumps(
            self._body(request), separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self.config.endpoint,
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
                http_request, timeout=self.config.reliability.timeout_seconds
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            safe_code = self._safe_error_code(exc.read())
            if exc.code == 429:
                code = (
                    ProviderFailureCode.BUDGET_EXHAUSTED
                    if safe_code in {"insufficient_quota", "billing_hard_limit_reached"}
                    else ProviderFailureCode.PROVIDER_UNAVAILABLE
                )
            elif exc.code == 400 and safe_code in {
                "model_not_found",
                "unsupported_model",
                "invalid_model",
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
        returned_model = parsed.get("model")
        if returned_model is not None and returned_model != self.model_policy.model:
            raise L2ProviderError(
                ProviderFailureCode.UNSUPPORTED_MODEL,
                "provider response model did not match the selected model",
            )
        usage = self._usage(parsed)
        response_digest = digest(parsed)
        incomplete_reason = self._incomplete_reason(parsed)
        if incomplete_reason is not None:
            failure_code = (
                ProviderFailureCode.BUDGET_EXHAUSTED
                if incomplete_reason == "max_output_tokens"
                else ProviderFailureCode.INVALID_STRUCTURED_RESPONSE
            )
            raise L2ProviderError(
                failure_code,
                f"provider response incomplete: {incomplete_reason}",
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
class OpenAIProviderFactory:
    """Lazy factory; construction performs no key access or network operation."""

    config: L2Config

    def __call__(self, model_policy: ModelPolicy) -> ReasoningProvider:
        from dual_pipeline.l2_providers_core.sentinel import authorize_provider_instantiation
        authorize_provider_instantiation()
        return OpenAIReasoningProvider(self.config, model_policy)


def build_call_record(
    *,
    model_policy: ModelPolicy,
    model_profile: str,
    purpose: CallPurpose,
    ordinal: int,
    response: L2ProviderResponse | None,
    latency_ms: float,
    failure: L2ProviderError | None = None,
) -> L2CallRecord:
    usage = (
        response.usage
        if response is not None
        else failure.usage
        if failure is not None and failure.usage is not None
        else TokenUsage()
    )
    uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
    input_cost = (
        uncached * model_policy.pricing.input_per_million
        + usage.cached_input_tokens * model_policy.pricing.cached_input_per_million
    ) / 1_000_000
    output_cost = (
        usage.output_tokens * model_policy.pricing.output_per_million / 1_000_000
    )
    return L2CallRecord(
        provider=model_policy.provider,
        model=model_policy.model,
        model_profile=model_profile,
        reasoning_effort=model_policy.reasoning_effort,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        estimated_input_cost=round(input_cost, 12),
        estimated_output_cost=round(output_cost, 12),
        estimated_total_cost=round(input_cost + output_cost, 12),
        latency_ms=round(latency_ms, 3),
        call_purpose=purpose.value,
        call_ordinal=ordinal,
        call_cap_status="WITHIN_CAP",
        response_digest=(
            response.response_digest
            if response
            else failure.response_digest
            if failure is not None
            else None
        ),
        status="FAIL" if failure else "PASS",
        failure_code=failure.code.value if failure else None,
    )
