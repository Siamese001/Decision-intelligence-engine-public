"""Advisory packet preparation, output schema generation, and proposal validation."""
from __future__ import annotations

from typing import Any, Mapping

from ..contracts import create_artifact
from ..core import digest, stable_id, utc_now
from ..provider_request import build_responses_request
from .constants import ALLOWED_RECOMMENDATIONS, REASON_CODE_PATTERN
from .errors import ProposalValidationError
from .models import (
    IngestionModelPolicy,
    IngestionProviderRequest,
    IngestionProviderResponse,
    IngestionReasoningPolicy,
)


def build_ingestion_reasoning_packet(
    *,
    decision: Mapping[str, Any],
    bundle: Mapping[str, Any],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    governed_context: Mapping[str, Any],
    routes: list[Mapping[str, Any]],
    candidate_ids: set[str] | list[str] | None = None,
) -> dict[str, Any]:
    """Build a source-bound packet without raw files or canonical-write authority."""

    resolution_by_mention = {
        value["mention_id"]: value["outcome"] for value in resolutions
    }
    route_by_candidate = {value["subject_id"]: value for value in routes}
    packet_candidates: list[dict[str, Any]] = []
    all_evidence_ids: set[str] = set()
    target_candidates = (
        [c for c in bundle["candidates"] if c["candidate_id"] in candidate_ids]
        if candidate_ids is not None
        else bundle["candidates"]
    )
    for candidate in target_candidates:
        candidate_id = candidate["candidate_id"]
        all_evidence_ids.update(candidate["evidence_ids"])
        candidate_comparison = comparison["comparisons"][candidate_id]
        packet_candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_type": candidate["candidate_type"],
                "materiality": candidate["materiality"],
                "normalized_text": candidate["normalized_text"],
                "evidence_ids": sorted(candidate["evidence_ids"]),
                "classification": candidate["classification"],
                "intake_route": route_by_candidate[candidate_id]["intake_route"],
                "entity_resolution_outcomes": sorted(
                    {
                        resolution_by_mention[mention["mention_id"]]
                        for mention in candidate["entity_mentions"]
                    }
                ),
                "exact_record_ids": sorted(
                    {
                        match["record_id"]
                        for match in candidate_comparison["exact_matches"]
                    }
                ),
                "semantic_matches": [
                    {
                        "record_id": match["record_id"],
                        "lane": match["lane"],
                        "score": float(match["score"]),
                    }
                    for match in candidate_comparison["semantic_matches"][:5]
                ],
            }
        )
    packet_id = stable_id(
        "ingestion-reasoning-packet",
        decision["decision_id"],
        digest(packet_candidates),
        length=28,
    )
    return create_artifact(
        "ingestion-reasoning-packet",
        packet_id,
        packet_id=packet_id,
        run_id=bundle["run_id"],
        trace_id=bundle["trace_id"],
        decision_id=decision["decision_id"],
        decision_digest=decision["canonical_digest"]["value"],
        source_id=bundle["source_id"],
        source_version_id=bundle["source_version_id"],
        evidence_ids=sorted(all_evidence_ids),
        candidates=packet_candidates,
        source_bound_only=True,
        raw_source_included=False,
        governed_context_digest=digest(governed_context),
        created_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.authority-boundary": {
                "proposal_only": True,
                "canonical_write_allowed": False,
                "identity_selection_allowed": False,
            }
        },
    )


def ingestion_proposal_output_schema(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the exact strict JSON Schema supplied to Responses API."""

    candidate_ids = sorted(
        candidate["candidate_id"] for candidate in packet["candidates"]
    )
    evidence_ids = sorted(
        {
            evidence_id
            for candidate in packet["candidates"]
            for evidence_id in candidate["evidence_ids"]
        }
    )
    count = len(candidate_ids)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["recommendations", "overall_review_priority", "summary_reason_codes"],
        "properties": {
            "recommendations": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["candidate_id", "recommendation", "evidence_ids", "reason_codes", "reviewer_question"],
                    "properties": {
                        "candidate_id": {"type": "string", "enum": candidate_ids},
                        "recommendation": {"type": "string", "enum": sorted(ALLOWED_RECOMMENDATIONS)},
                        "evidence_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "enum": evidence_ids}},
                        "reason_codes": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"}},
                        "reviewer_question": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 512}, {"type": "null"}]},
                    },
                },
            },
            "overall_review_priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary_reason_codes": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"}},
        },
    }


def ingestion_system_instruction(purpose: str = "") -> str:
    """Return the governed instruction without constructing a provider.

    The primary system prompt prefix is invariant across all purposes to maximize
    provider prompt caching efficiency. Purpose-specific repair instructions are
    situated in the user payload.
    """
    return (
        "Return only the strict structured output. This is a non-authoritative "
        "ingestion-review proposal. Use only the supplied sealed candidate packet "
        "and its evidence and record identifiers. Do not browse, retrieve, call "
        "tools, select canonical identities, approve facts, propose graph operations, "
        "or introduce unsupported facts. Recommendations may only be CANDIDATE, "
        "QUARANTINE, REJECTED_NOOP, or HITL_REQUIRED. Do not expose hidden reasoning."
    )


def _repair_candidate_context(request: IngestionProviderRequest) -> list[dict[str, Any]]:
    if isinstance(request.packet, Mapping) and isinstance(request.packet.get("candidates"), list):
        return [
            {
                "candidate_id": c.get("candidate_id"),
                "evidence_ids": c.get("evidence_ids"),
                "entity_resolution_outcomes": c.get("entity_resolution_outcomes"),
            }
            for c in request.packet["candidates"]
            if isinstance(c, Mapping)
        ]
    props = (
        request.output_schema.get("properties", {})
        .get("recommendations", {})
        .get("items", {})
        .get("properties", {})
        if isinstance(request.output_schema, Mapping)
        else {}
    )
    cids = props.get("candidate_id", {}).get("enum", [])
    eids = props.get("evidence_ids", {}).get("items", {}).get("enum", [])
    return [{"candidate_id": cid, "allowed_evidence_ids": eids} for cid in cids]


def ingestion_user_payload(request: IngestionProviderRequest) -> dict[str, Any]:
    """Return the minimized provider user payload."""

    if request.purpose == "SCHEMA_REPAIR":
        payload: dict[str, Any] = {
            "purpose": request.purpose,
            "purpose_instruction": (
                "Repair only the listed serialization, required-field, enum, "
                "candidate-coverage, and evidence-ID defects. Add no new facts."
            ),
            "original_response": request.original_response,
            "validation_errors": list(request.validation_errors),
            "candidate_context": _repair_candidate_context(request),
        }
        if request.packet is not None:
            payload["sealed_ingestion_packet"] = request.packet
        return payload
    return {
        "purpose": request.purpose,
        "sealed_ingestion_packet": request.packet,
    }


def build_ingestion_provider_body(
    *,
    policy: IngestionReasoningPolicy,
    model_policy: IngestionModelPolicy,
    request: IngestionProviderRequest,
) -> dict[str, Any]:
    """Build the exact Responses body for measurement and provider execution."""

    return build_responses_request(
        model=model_policy.model,
        reasoning_effort=model_policy.reasoning_effort,
        system_instruction=ingestion_system_instruction(request.purpose),
        user_payload=ingestion_user_payload(request),
        output_schema=request.output_schema,
        schema_name="nee_ingestion_reasoning_proposal",
        maximum_output_tokens=policy.maximum_output_tokens,
    )


def _validate_reason_codes(value: Any, path: str, errors: list[str]) -> None:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 8
        or len(set(item for item in value if isinstance(item, str))) != len(value)
        or any(
            not isinstance(item, str) or REASON_CODE_PATTERN.fullmatch(item) is None
            for item in value
        )
    ):
        errors.append(f"{path} must contain 1-8 unique governed reason codes")


def _check_item_structure(value: Any, path: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return False
    required_keys = {
        "candidate_id", "recommendation", "evidence_ids",
        "reason_codes", "reviewer_question",
    }
    if set(value) != required_keys:
        errors.append(f"{path} keys do not match the strict item contract")
        return False
    return True


def _check_item_safety(
    rec: Any, has_unresolved: bool, path: str, errors: list[str]
) -> None:
    if rec not in ALLOWED_RECOMMENDATIONS:
        errors.append(f"{path}.recommendation is not advisory-safe")
    if has_unresolved and rec not in {"HITL_REQUIRED", "QUARANTINE"}:
        errors.append(
            f"{path}.recommendation must preserve unresolved identity as HITL or quarantine"
        )


def _check_item_evidence(
    ev_ids: Any, allowed: set[str], path: str, errors: list[str]
) -> None:
    if (
        not isinstance(ev_ids, list)
        or not ev_ids
        or len(set(ev_ids)) != len(ev_ids)
        or not set(ev_ids) <= allowed
    ):
        errors.append(f"{path}.evidence_ids escape the candidate evidence boundary")


def _check_item_question(question: Any, path: str, errors: list[str]) -> None:
    if question is not None and (
        not isinstance(question, str) or not 1 <= len(question) <= 512
    ):
        errors.append(f"{path}.reviewer_question is invalid")


def _is_ambiguous_outcome(outcome: Any) -> bool:
    return str(outcome or "") not in {"AUTO_LINK_EXACT", "SOURCE_ONLY", ""}


def _validate_recommendation_item(
    index: int,
    value: Any,
    expected_candidates: Mapping[str, set[str]],
    packet_candidates: Mapping[str, Any],
    seen: set[str],
    errors: list[str],
) -> None:
    path = f"recommendations[{index}]"
    if not _check_item_structure(value, path, errors):
        return
    candidate_id = value.get("candidate_id")
    if candidate_id not in expected_candidates:
        errors.append(f"{path}.candidate_id is not in the sealed packet")
        return
    if candidate_id in seen:
        errors.append(f"{path}.candidate_id is duplicated")
    seen.add(candidate_id)
    has_unresolved = any(
        _is_ambiguous_outcome(o)
        for o in (packet_candidates.get(candidate_id, {}).get("entity_resolution_outcomes") or [])
    )
    _check_item_safety(value.get("recommendation"), has_unresolved, path, errors)
    _check_item_evidence(value.get("evidence_ids"), expected_candidates[candidate_id], path, errors)
    _validate_reason_codes(value.get("reason_codes"), f"{path}.reason_codes", errors)
    _check_item_question(value.get("reviewer_question"), path, errors)


def validate_ingestion_proposal_output(
    output: Any, packet: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate exact candidate/evidence coverage and prohibit write authority."""

    errors: list[str] = []
    if not isinstance(output, dict):
        raise ProposalValidationError(["output must be an object"])
    expected_keys = {
        "recommendations",
        "overall_review_priority",
        "summary_reason_codes",
    }
    if set(output) != expected_keys:
        errors.append("output keys do not match the strict proposal contract")
    recommendations = output.get("recommendations")
    if not isinstance(recommendations, list):
        errors.append("recommendations must be an array")
        recommendations = []
    expected_candidates = {
        candidate["candidate_id"]: set(candidate["evidence_ids"])
        for candidate in packet["candidates"]
    }
    packet_candidates = {
        candidate["candidate_id"]: candidate for candidate in packet["candidates"]
    }
    seen: set[str] = set()
    for index, value in enumerate(recommendations):
        _validate_recommendation_item(
            index, value, expected_candidates, packet_candidates, seen, errors
        )
    if seen != set(expected_candidates):
        errors.append("recommendations must cover every sealed candidate exactly once")
    if output.get("overall_review_priority") not in {"low", "medium", "high"}:
        errors.append("overall_review_priority is invalid")
    _validate_reason_codes(
        output.get("summary_reason_codes"), "summary_reason_codes", errors
    )
    if errors:
        raise ProposalValidationError(errors)
    return dict(output)


def create_ingestion_reasoning_proposal(
    *,
    mode: str,
    decision: Mapping[str, Any],
    packet: Mapping[str, Any],
    model_policy: IngestionModelPolicy,
    response: IngestionProviderResponse,
    output: Mapping[str, Any],
) -> dict[str, Any]:
    proposal_id = stable_id(
        "ingestion-reasoning-proposal",
        decision["decision_id"],
        response.response_digest,
        length=28,
    )
    return create_artifact(
        "ingestion-reasoning-proposal",
        proposal_id,
        proposal_id=proposal_id,
        run_id=decision["run_id"],
        trace_id=decision["trace_id"],
        decision_id=decision["decision_id"],
        packet_digest=packet["canonical_digest"]["value"],
        mode=mode,
        model_tier=model_policy.model_tier,
        requested_model=model_policy.model,
        delivered_model=response.delivered_model,
        reasoning_effort=model_policy.reasoning_effort,
        recommendations=output["recommendations"],
        overall_review_priority=output["overall_review_priority"],
        summary_reason_codes=output["summary_reason_codes"],
        authority_changes_proposed=False,
        graph_operations_proposed=False,
        canonical_approval_proposed=False,
        created_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.provider-response": {
                "response_digest": response.response_digest
            }
        },
    )


def _correlation(source_id: str) -> dict[str, str | None]:
    return {"source_id": source_id, "candidate_id": None, "query_id": None, "graph_operation_id": None, "hitl_item_id": None}


def _call_payload(
    *, purpose: str, ordinal: int, model_policy: IngestionModelPolicy,
    response: IngestionProviderResponse | None, failure_code: str | None = None,
) -> dict[str, Any]:
    return {
        "call_purpose": purpose, "call_ordinal": ordinal, "provider": model_policy.provider,
        "requested_model": model_policy.model, "delivered_model": response.delivered_model if response else None,
        "model_tier": model_policy.model_tier, "reasoning_effort": model_policy.reasoning_effort,
        "response_digest": response.response_digest if response else None,
        "input_tokens": response.input_tokens if response else 0,
        "cached_input_tokens": response.cached_input_tokens if response else 0,
        "output_tokens": response.output_tokens if response else 0,
        "total_tokens": response.total_tokens if response else 0,
        "latency_ms": response.latency_ms if response else 0,
        "failure_code": failure_code,
    }
