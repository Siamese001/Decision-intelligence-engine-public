"""Pure Responses API request assembly, measurement, and budget controls.

The helpers in this module perform no secret resolution, provider
initialization, network access, graph access, or durable mutation.  They let
L2D and E2 reason over the exact serialized provider request before any
external call is possible.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .core import canonical_json, digest

READ_WRITE_BUDGET_VERSION = "nee-provider-request-budget-1.0.0"


def build_responses_request(
    *,
    model: str,
    system_instruction: str,
    user_payload: Mapping[str, Any],
    reasoning_effort: str,
    schema_name: str,
    output_schema: Mapping[str, Any],
    maximum_output_tokens: int,
    prompt_cache_key: str | None = None,
    prompt_cache_retention: str | None = None,
) -> dict[str, Any]:
    """Build the canonical provider request without resolving credentials."""

    body: dict[str, Any] = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_instruction,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": canonical_json(user_payload),
                    }
                ],
            },
        ],
        "reasoning": {"effort": reasoning_effort},
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": dict(output_schema),
            }
        },
        "tools": [],
        "store": False,
        "max_output_tokens": maximum_output_tokens,
    }
    if prompt_cache_key is not None:
        body["prompt_cache_key"] = prompt_cache_key
    if prompt_cache_retention is not None:
        body["prompt_cache_retention"] = prompt_cache_retention
    return body


def _calculate_part_chars(content: Any) -> int:
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return 0
    chars = 0
    for part in content:
        if isinstance(part, Mapping) and part.get("type") == "input_text":
            val = part.get("text")
            if isinstance(val, str):
                chars += len(val)
    return chars


def _calculate_input_chars(inputs: Any) -> tuple[int, int]:
    if not isinstance(inputs, Sequence) or isinstance(inputs, (str, bytes)):
        return 0, 0
    system_chars, user_chars = 0, 0
    for item in inputs:
        if not isinstance(item, Mapping):
            continue
        role = item.get("role")
        part_chars = _calculate_part_chars(item.get("content", []))
        if role == "system":
            system_chars += part_chars
        elif role == "user":
            user_chars += part_chars
    return system_chars, user_chars


def _extract_schema(body: Mapping[str, Any]) -> Mapping[str, Any]:
    text = body.get("text")
    if isinstance(text, Mapping):
        output_format = text.get("format")
        if isinstance(output_format, Mapping):
            candidate = output_format.get("schema")
            if isinstance(candidate, Mapping):
                return candidate
    return {}


def measure_responses_request(body: Mapping[str, Any]) -> dict[str, Any]:
    """Return digest-only size telemetry for one exact provider request."""
    system_chars, user_chars = _calculate_input_chars(body.get("input", []))
    schema = _extract_schema(body)
    serialized = canonical_json(body)
    return {
        "request_payload_digest": digest(body),
        "request_input_chars": system_chars + user_chars,
        "request_system_chars": system_chars,
        "request_user_chars": user_chars,
        "request_schema_chars": len(canonical_json(schema)),
        "request_serialized_chars": len(serialized),
    }


def provider_request_budget_verdict(
    *,
    request_metrics: Mapping[str, Any],
    maximum_provider_request_chars: int,
    request_context: Optional[Any] = None,
    action_role: str = "primary",
) -> dict[str, Any]:
    """Return a deterministic G20-style allow/block verdict."""
    actual = int(request_metrics["request_serialized_chars"])
    maximum = int(maximum_provider_request_chars)
    if maximum < 1:
        raise ValueError("maximum provider request characters must be positive")

    # Evaluate RequestContext constraints if provided
    if request_context is not None:
        if getattr(request_context, "is_cancelled", False):
            return {
                "budget_version": READ_WRITE_BUDGET_VERSION,
                "status": "BLOCK",
                "reason_code": "REQUEST_CONTEXT_CANCELLED",
                "request_serialized_chars": actual,
                "maximum_provider_request_chars": maximum,
                "remaining_chars": 0,
                "request_payload_digest": request_metrics["request_payload_digest"],
            }
        is_expired_fn = getattr(request_context, "is_expired", None)
        if callable(is_expired_fn) and is_expired_fn():
            return {
                "budget_version": READ_WRITE_BUDGET_VERSION,
                "status": "BLOCK",
                "reason_code": "REQUEST_CONTEXT_DEADLINE_EXCEEDED",
                "request_serialized_chars": actual,
                "maximum_provider_request_chars": maximum,
                "remaining_chars": 0,
                "request_payload_digest": request_metrics["request_payload_digest"],
            }
        if getattr(request_context, "disable_optional_work", False) and action_role in ("healer", "optional_reasoning"):
            return {
                "budget_version": READ_WRITE_BUDGET_VERSION,
                "status": "BLOCK",
                "reason_code": "OPTIONAL_WORK_DISABLED",
                "request_serialized_chars": actual,
                "maximum_provider_request_chars": maximum,
                "remaining_chars": 0,
                "request_payload_digest": request_metrics["request_payload_digest"],
            }
        avail_cost_fn = getattr(request_context, "available_cost_usd", None)
        if callable(avail_cost_fn) and avail_cost_fn() <= 0.0:
            return {
                "budget_version": READ_WRITE_BUDGET_VERSION,
                "status": "BLOCK",
                "reason_code": "REQUEST_CONTEXT_BUDGET_EXHAUSTED",
                "request_serialized_chars": actual,
                "maximum_provider_request_chars": maximum,
                "remaining_chars": 0,
                "request_payload_digest": request_metrics["request_payload_digest"],
            }

    allowed = actual <= maximum
    return {
        "budget_version": READ_WRITE_BUDGET_VERSION,
        "status": "ALLOW" if allowed else "BLOCK",
        "reason_code": (
            "WITHIN_PROVIDER_REQUEST_BUDGET"
            if allowed
            else "PROVIDER_REQUEST_TOO_LARGE"
        ),
        "request_serialized_chars": actual,
        "maximum_provider_request_chars": maximum,
        "remaining_chars": max(0, maximum - actual),
        "request_payload_digest": request_metrics["request_payload_digest"],
    }


def aggregate_token_usage(records: Sequence[Any]) -> dict[str, Any]:
    """Aggregate provider response or call-record usage without prompt content."""

    input_tokens = sum(int(getattr(value, "input_tokens", 0)) for value in records)
    cached_input_tokens = sum(
        min(
            int(getattr(value, "cached_input_tokens", 0)),
            int(getattr(value, "input_tokens", 0)),
        )
        for value in records
    )
    output_tokens = sum(int(getattr(value, "output_tokens", 0)) for value in records)
    total_tokens = sum(int(getattr(value, "total_tokens", 0)) for value in records)
    uncached_input_tokens = input_tokens - cached_input_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": uncached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "input_output_ratio": round(
            input_tokens / max(output_tokens, 1),
            6,
        ),
        "cached_input_share": round(
            cached_input_tokens / max(input_tokens, 1),
            6,
        ),
    }


def build_anthropic_messages(
    *,
    system_instruction: str,
    user_content: str,
    enable_cache: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Format Anthropic system blocks and message payload with explicit cache breakpoint."""
    from dual_pipeline.prompt_cache import anthropic_cache_breakpoint

    if enable_cache:
        system_blocks = [anthropic_cache_breakpoint(system_instruction)]
    else:
        system_blocks = [{"type": "text", "text": system_instruction}]
    messages = [{"role": "user", "content": user_content}]
    return system_blocks, messages


__all__ = [
    "READ_WRITE_BUDGET_VERSION",
    "aggregate_token_usage",
    "build_anthropic_messages",
    "build_responses_request",
    "measure_responses_request",
    "provider_request_budget_verdict",
]
