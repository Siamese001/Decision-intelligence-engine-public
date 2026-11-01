"""Provider-free, evidence-bound decision-context projection for W14."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .authorization import AuthorizationContext
from .exceptions import DualPipelineValueError
from .reference_monitor import ReferenceMonitor
from .strategic_evidence import project_synthesis_workspace, validate_evidence_packet
from .temporal_observations import project_observation_state, validate_observation

DECISION_CONTEXT_STATES = frozenset(
    {
        "CANDIDATE_READY_WITH_COUNTEREVIDENCE",
        "CANDIDATE_WITH_GAPS",
        "HITL_REQUIRED",
        "STALE_OR_UNKNOWN",
    }
)


class DecisionContextError(DualPipelineValueError):
    """Raised when a decision-context request is under-specified or unsafe."""


def _nonempty_unique(values: Iterable[str], *, field: str) -> list[str]:
    normalized = list(values)
    if (
        not normalized
        or any(not isinstance(value, str) or not value.strip() for value in normalized)
        or len(set(normalized)) != len(normalized)
    ):
        raise DecisionContextError(f"{field} must be a unique nonempty list of text")
    return sorted(value.strip() for value in normalized)


def _assert_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionContextError(f"{field} must be nonblank text")
    return value.strip()


def _source_ids_by_id(items: Iterable[Mapping[str, Any]], *, id_field: str) -> dict[str, list[str]]:
    return {item[id_field]: list(item["source_assertion_ids"]) for item in items}


def _observation_lineage(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "item_type": "observation",
        "source_assertion_ids": list(item["source_assertion_ids"]),
        "classification": item["classification"],
        "audiences": list(item["audiences"]),
        "source_as_of": item["source_as_of"],
        "recorded_at": item["recorded_at"],
        "expires_at": item["stale_after"],
        "observed_at": item["observed_at"],
    }


def _evidence_lineage(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "item_type": "strategic_evidence",
        "source_assertion_ids": list(item["source_assertion_ids"]),
        "classification": item["classification"],
        "audiences": list(item["audiences"]),
        "source_as_of": item["source_as_of"],
        "recorded_at": item["recorded_at"],
        "expires_at": item["expires_at"],
        "review_state": item["review_state"],
    }


def project_decision_context(
    *,
    scope_id: str,
    audience: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
    observations: Iterable[Mapping[str, Any]],
    required_dimensions: Iterable[str],
    evidence_packets: Iterable[Mapping[str, Any]],
    claim_ids: Iterable[str],
) -> dict[str, Any]:
    """Build a candidate-only decision context from W3/W6 guarded projections."""

    scope_id = _assert_text(scope_id, field="scope_id")
    audience = _assert_text(audience, field="audience")
    as_of = _assert_text(as_of, field="as_of")
    dimensions = _nonempty_unique(required_dimensions, field="required_dimensions")
    claims = _nonempty_unique(claim_ids, field="claim_ids")
    if not isinstance(reference_monitor, ReferenceMonitor):
        raise DecisionContextError("a ReferenceMonitor is required for projection")
    validated_observations = [
        validate_observation(item, reference_monitor=reference_monitor)
        for item in observations
    ]
    validated_packets = [
        validate_evidence_packet(item, reference_monitor=reference_monitor)
        for item in evidence_packets
    ]
    observation_sources = _source_ids_by_id(validated_observations, id_field="observation_id")
    packet_sources = _source_ids_by_id(validated_packets, id_field="evidence_id")
    lineage_by_item = {
        item["observation_id"]: _observation_lineage(item) for item in validated_observations
    }
    lineage_by_item.update(
        {item["evidence_id"]: _evidence_lineage(item) for item in validated_packets}
    )

    status = [
        project_observation_state(
            validated_observations,
            scope_id=scope_id,
            dimension=dimension,
            as_of=as_of,
            known_at=known_at,
            audience=audience,
            authorization_context=authorization_context,
            reference_monitor=reference_monitor,
        )
        for dimension in dimensions
    ]
    claims_projection = [
        project_synthesis_workspace(
            validated_packets,
            claim_id=claim_id,
            audience=audience,
            as_of=as_of,
            known_at=known_at,
            authorization_context=authorization_context,
            reference_monitor=reference_monitor,
        )
        for claim_id in claims
    ]

    context_sources: dict[str, list[str]] = {}
    for projection in status:
        ids = projection.get("observation_ids", [])
        context_sources.update(
            {
                item_id: observation_sources[item_id]
                for item_id in ids
                if item_id in observation_sources
            }
        )
    for projection in claims_projection:
        ids = (
            projection.get("supporting_evidence_ids", [])
            + projection.get("counterevidence_ids", [])
            + projection.get("context_evidence_ids", [])
            + projection.get("expired_evidence_ids", [])
        )
        context_sources.update(
            {item_id: packet_sources[item_id] for item_id in ids if item_id in packet_sources}
        )

    observation_states = {item["state"] for item in status}
    evidence_states = {item["state"] for item in claims_projection}
    if "CONFLICT" in observation_states or "HITL_REQUIRED" in evidence_states:
        state = "HITL_REQUIRED"
    elif "STALE_OR_UNKNOWN" in observation_states or "STALE_OR_UNKNOWN" in evidence_states:
        state = "STALE_OR_UNKNOWN"
    elif "READY_SUPPORT_ONLY" in evidence_states:
        state = "CANDIDATE_WITH_GAPS"
    else:
        state = "CANDIDATE_READY_WITH_COUNTEREVIDENCE"
    assert state in DECISION_CONTEXT_STATES

    gaps = sorted(
        {gap for projection in claims_projection for gap in projection.get("gaps", [])}
        | {
            f"observation_{projection['dimension'].lower()}_{projection['state'].lower()}"
            for projection in status
            if projection["state"] != "CURRENT"
        }
    )
    return {
        "scope_id": scope_id,
        "audience": audience,
        "as_of": as_of,
        "known_at": known_at,
        "state": state,
        "recommendation_posture": "CANDIDATE_ONLY",
        "status_projections": status,
        "strategic_claim_projections": claims_projection,
        "source_assertion_ids_by_item": dict(sorted(context_sources.items())),
        "source_lineage_by_item": {
            item_id: lineage_by_item[item_id]
            for item_id in sorted(context_sources)
            if item_id in lineage_by_item
        },
        "gaps": gaps,
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
    }
