"""Provider execution runner and telemetry recording for ingestion reasoning."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Optional

from ..core import write_json
from ..provider_request import (
    aggregate_token_usage,
    measure_responses_request,
    provider_request_budget_verdict,
)
from .advisory import (
    _call_payload,
    _correlation,
    build_ingestion_provider_body,
    validate_ingestion_proposal_output,
)
from .errors import IngestionProviderError, ProposalValidationError
from .models import (
    IngestionModelPolicy,
    IngestionProviderRequest,
    IngestionProviderResponse,
    IngestionReasoningPolicy,
)
from .selective import merge_advisory_subpacket


def init_and_check_budget(
    *,
    service_ref: Any,
    decision: dict[str, Any],
    packet: dict[str, Any],
    model_policy: IngestionModelPolicy,
    request_metrics: dict[str, Any],
    budget_verdict: dict[str, Any],
    bundle: Mapping[str, Any],
    recorder: Any,
    run_dir: Path,
) -> tuple[dict[str, Any], Any | None]:
    """Emit PA prompt event, evaluate budget status, and initialize provider."""
    write_json(run_dir / "ingestion_reasoning_packet.json", packet)
    recorder.record(
        stage="PA",
        event_type="prompt_event",
        payload={
            "packet_id": packet["packet_id"],
            "packet_digest": packet["canonical_digest"]["value"],
            "request_metrics": request_metrics,
            "budget_verdict": budget_verdict,
        },
        correlation=_correlation(bundle["source_id"]),
        record_count=len(packet["candidates"]),
    )
    result = service_ref._base_result(
        decision=decision,
        status="PENDING",
        disposition="PENDING",
    )
    result["packet_digest"] = packet["canonical_digest"]["value"]
    result["request_metrics"] = request_metrics
    result["budget_verdict"] = budget_verdict

    if budget_verdict["status"] != "ALLOW":
        result.update(
            status="CONSERVATIVE_FALLBACK",
            disposition="BUDGET_EXHAUSTED_DETERMINISTIC_UNCHANGED",
            failure_code="BUDGET_EXHAUSTED",
        )
        recorder.record(
            stage="L2",
            event_type="l2_provider_failure",
            payload={
                "decision_id": decision["decision_id"],
                "failure_code": "BUDGET_EXHAUSTED",
                "provider_initialized": False,
                "budget_verdict": budget_verdict,
                "deterministic_dispositions_unchanged": True,
            },
            correlation=_correlation(bundle["source_id"]),
            model_id=model_policy.model,
        )
        write_json(run_dir / "ingestion_reasoning_execution.json", result)
        return result, None

    try:
        provider = service_ref.provider_factory(model_policy)
        return result, provider
    except Exception as exc:
        code = exc.code if isinstance(exc, IngestionProviderError) else "PROVIDER_UNAVAILABLE"
        result.update(
            status="CONSERVATIVE_FALLBACK",
            disposition="PROVIDER_UNAVAILABLE_DETERMINISTIC_UNCHANGED",
            failure_code=code,
        )
        recorder.record(
            stage="L2",
            event_type="l2_provider_failure",
            payload={
                "decision_id": decision["decision_id"],
                "failure_code": code,
                "provider_initialized": False,
                "deterministic_dispositions_unchanged": True,
            },
            correlation=_correlation(bundle["source_id"]),
            model_id=model_policy.model,
        )
        write_json(run_dir / "ingestion_reasoning_execution.json", result)
        return result, None


def record_call_failure(
    recorder: Any,
    source_id: str,
    purpose: str,
    ordinal: int,
    model_policy: IngestionModelPolicy,
    code: str,
    elapsed_ms: float,
    result: dict[str, Any],
) -> None:
    """Record an L2 provider call failure with OpenTelemetry attributes."""
    call = _call_payload(
        purpose=purpose,
        ordinal=ordinal,
        model_policy=model_policy,
        response=None,
        failure_code=code,
    )
    call["latency_ms"] = elapsed_ms
    recorder.record(
        stage="L2",
        event_type="l2_provider_failure",
        payload=call,
        correlation=_correlation(source_id),
        model_id=model_policy.model,
        otel_attributes={
            "call_purpose": purpose,
            "call_ordinal": ordinal,
            "selected_model_tier": model_policy.model_tier,
            "selected_model": model_policy.model,
            "latency_ms": elapsed_ms,
            "model_call_count": result["provider_call_count"],
            "repair_call_count": result["repair_call_count"],
            "escalation_call_count": 0,
            "judge_call_count": 0,
            "challenger_call_count": 0,
        },
    )


def record_call_success(
    recorder: Any,
    source_id: str,
    purpose: str,
    ordinal: int,
    model_policy: IngestionModelPolicy,
    response: IngestionProviderResponse,
    result: dict[str, Any],
) -> None:
    """Record an L2 provider call success with OpenTelemetry attributes."""
    event_type = "l2_primary_call" if purpose == "PRIMARY" else "l2_schema_repair"
    call = _call_payload(
        purpose=purpose,
        ordinal=ordinal,
        model_policy=model_policy,
        response=response,
    )
    recorder.record(
        stage="L2",
        event_type=event_type,
        payload=call,
        correlation=_correlation(source_id),
        model_id=response.delivered_model,
        otel_attributes={
            "call_purpose": purpose,
            "call_ordinal": ordinal,
            "selected_model_tier": model_policy.model_tier,
            "selected_model": model_policy.model,
            "delivered_model_tier": model_policy.model_tier,
            "delivered_model": response.delivered_model,
            "input_tokens": response.input_tokens,
            "cached_input_tokens": response.cached_input_tokens,
            "uncached_input_tokens": response.input_tokens - response.cached_input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.total_tokens,
            "input_output_ratio": round(
                response.input_tokens / max(response.output_tokens, 1), 6
            ),
            "cached_input_share": round(
                response.cached_input_tokens / max(response.input_tokens, 1), 6
            ),
            "latency_ms": response.latency_ms,
            "model_call_count": result["provider_call_count"],
            "repair_call_count": result["repair_call_count"],
            "escalation_call_count": 0,
            "judge_call_count": 0,
            "challenger_call_count": 0,
            "response_digest": response.response_digest,
        },
    )


def check_call_budget(
    policy: IngestionReasoningPolicy,
    request: IngestionProviderRequest,
    model_policy: IngestionModelPolicy,
    purpose: str,
    ordinal: int,
    responses: list[IngestionProviderResponse],
    bundle: Mapping[str, Any],
    result: dict[str, Any],
    recorder: Any,
    run_dir: Path,
) -> Optional[dict[str, Any]]:
    """Verify request size against character and token budget ceiling."""
    body = build_ingestion_provider_body(
        policy=policy,
        model_policy=model_policy,
        request=request,
    )
    metrics = measure_responses_request(body)
    max_chars = int(policy.routing["maximum_provider_request_chars"])
    budget = provider_request_budget_verdict(
        request_metrics=metrics,
        maximum_provider_request_chars=max_chars,
    )
    if budget["status"] == "ALLOW":
        return None

    result.update(aggregate_token_usage(responses))
    result.update(
        status="CONSERVATIVE_FALLBACK",
        disposition="BUDGET_EXHAUSTED_DETERMINISTIC_UNCHANGED",
        failure_code="BUDGET_EXHAUSTED",
    )
    recorder.record(
        stage="L2",
        event_type="l2_provider_failure",
        payload={
            "call_purpose": purpose,
            "call_ordinal": ordinal,
            "failure_code": "BUDGET_EXHAUSTED",
            "request_metrics": metrics,
            "budget_verdict": budget,
            "deterministic_dispositions_unchanged": True,
        },
        correlation=_correlation(bundle["source_id"]),
        model_id=model_policy.model,
    )
    write_json(run_dir / "ingestion_reasoning_execution.json", result)
    return result


def call_provider_safe(
    provider: Any,
    request: IngestionProviderRequest,
    model_policy: IngestionModelPolicy,
    purpose: str,
    ordinal: int,
    bundle: Mapping[str, Any],
    result: dict[str, Any],
    recorder: Any,
    run_dir: Path,
) -> tuple[Optional[IngestionProviderResponse], Optional[dict[str, Any]]]:
    """Execute LLM provider call catching exceptions and recording failures."""
    started = time.perf_counter()
    try:
        response = provider.generate(request)
        return response, None
    except Exception as exc:
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        code = exc.code if isinstance(exc, IngestionProviderError) else "PROVIDER_UNAVAILABLE"
        result["provider_call_count"] += 1
        if purpose == "PRIMARY":
            result["primary_call_count"] += 1
        else:
            result["repair_call_count"] += 1
        record_call_failure(
            recorder, bundle["source_id"], purpose, ordinal, model_policy, code, elapsed, result
        )
        result.update(
            status="CONSERVATIVE_FALLBACK",
            disposition="PROVIDER_UNAVAILABLE_DETERMINISTIC_UNCHANGED",
            failure_code=code,
        )
        write_json(run_dir / "ingestion_reasoning_execution.json", result)
        return None, result


def build_call_request(
    primary_request: IngestionProviderRequest,
    purpose: str,
    schema: dict[str, Any],
    original_response: Any,
    validation_errors: tuple[str, ...],
    packet: dict[str, Any] | None = None,
) -> IngestionProviderRequest:
    """Build primary request or schema repair request payload."""
    if purpose == "PRIMARY":
        return primary_request
    return IngestionProviderRequest(
        purpose=purpose,
        packet=packet,
        output_schema=schema,
        original_response=original_response,
        validation_errors=validation_errors,
    )


def validate_call_output(
    raw_output: Any,
    packet: dict[str, Any],
    use_subpacket: bool,
    easy_cands: list[Any],
    bundle: Mapping[str, Any],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
) -> tuple[Optional[dict[str, Any]], tuple[str, ...]]:
    """Validate and optionally merge subpacket advisory outputs."""
    try:
        output = validate_ingestion_proposal_output(raw_output, packet)
        if use_subpacket and easy_cands:
            output = merge_advisory_subpacket(
                output, easy_cands, bundle["candidates"], resolutions, comparison
            )
        return output, ()
    except ProposalValidationError as exc:
        return None, exc.errors


def _track_repair_tokens(
    policy: IngestionReasoningPolicy,
    model_policy: IngestionModelPolicy,
    request: IngestionProviderRequest,
) -> None:
    from dual_pipeline.cost_tracker import get_token_tracker
    repair_body = build_ingestion_provider_body(
        policy=policy, model_policy=model_policy, request=request
    )
    repair_metrics = measure_responses_request(repair_body)
    estimated_tokens = repair_metrics.get("request_input_chars", 0) // 4
    get_token_tracker().intercept_token_call(estimated_tokens)


def execute_provider_loop(
    *,
    policy: IngestionReasoningPolicy,
    provider: Any,
    primary_request: IngestionProviderRequest,
    schema: dict[str, Any],
    model_policy: IngestionModelPolicy,
    packet: dict[str, Any],
    bundle: Mapping[str, Any],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    use_subpacket: bool,
    easy_cands: list[Any],
    result: dict[str, Any],
    recorder: Any,
    run_dir: Path,
) -> tuple[
    Optional[IngestionProviderResponse],
    list[IngestionProviderResponse],
    Optional[dict[str, Any]],
    tuple[str, ...],
    Optional[dict[str, Any]],
]:
    """Execute primary call and bounded schema repair iteration."""
    responses: list[IngestionProviderResponse] = []
    response: IngestionProviderResponse | None = None
    output: dict[str, Any] | None = None
    validation_errors: tuple[str, ...] = ()
    original_response: Any = None

    _PURPOSE_COUNTER = {"PRIMARY": "primary_call_count", "SCHEMA_REPAIR": "repair_call_count"}
    repair_pkt = packet if packet.get("include_on_repair") else None
    for purpose, ordinal in (("PRIMARY", 1), ("SCHEMA_REPAIR", 2)):
        if purpose == "SCHEMA_REPAIR" and not validation_errors:
            break
        request = build_call_request(
            primary_request, purpose, schema, original_response, validation_errors, packet=repair_pkt
        )
        if purpose == "SCHEMA_REPAIR":
            _track_repair_tokens(policy, model_policy, request)

        budget_err = check_call_budget(
            policy, request, model_policy, purpose, ordinal, responses, bundle, result, recorder, run_dir
        )
        if budget_err is not None:
            return None, responses, None, (), budget_err

        response, call_err = call_provider_safe(
            provider, request, model_policy, purpose, ordinal, bundle, result, recorder, run_dir
        )
        if call_err is not None:
            return None, responses, None, (), call_err

        result["provider_call_count"] += 1
        responses.append(response)
        result[_PURPOSE_COUNTER[purpose]] += 1

        record_call_success(
            recorder, bundle["source_id"], purpose, ordinal, model_policy, response, result
        )
        original_response = response.output
        output, validation_errors = validate_call_output(
            response.output, packet, use_subpacket, easy_cands, bundle, resolutions, comparison
        )
        if output is not None:
            break

    return response, responses, output, validation_errors, None
