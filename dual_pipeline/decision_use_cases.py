"""Provider-free use-case readiness evaluation for W15 decision intelligence."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ._validators import require_text
from .authorization import AuthorizationContext
from .decision_context import project_decision_context
from .exceptions import DualPipelineValueError
from .reference_monitor import ReferenceMonitor

USE_CASE_FIELDS = frozenset(
    {
        "use_case_id",
        "question",
        "scope_id",
        "audience",
        "required_dimensions",
        "claim_ids",
        "candidate_answer_sections",
    }
)
ANSWER_STATES = frozenset(
    {
        "CANDIDATE_ANSWER_READY",
        "CANDIDATE_ANSWER_WITH_GAPS",
        "BLOCKED_HITL_REQUIRED",
        "BLOCKED_STALE_OR_UNKNOWN",
    }
)
ANSWER_SECTION_VOCABULARY = frozenset(
    {
        "current_status",
        "evidence",
        "counterevidence",
        "gaps",
        "candidate_pov",
        "candidate_metrics",
        "candidate_intervention",
    }
)


class DecisionUseCaseError(DualPipelineValueError):
    """Raised when a decision use-case contract is invalid."""


def _unique_text_list(value: object, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise DecisionUseCaseError(f"{field} must be a unique nonempty list of text")
    return sorted(item.strip() for item in value)


def _text(value: object, *, field: str) -> str:
    return require_text(value, field=field, error_type=DecisionUseCaseError)


def validate_use_case(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a declarative high-value decision question."""

    if not isinstance(value, Mapping) or set(value) != USE_CASE_FIELDS:
        raise DecisionUseCaseError(f"use case must contain exactly {sorted(USE_CASE_FIELDS)}")
    result = dict(value)
    for field in ("use_case_id", "question", "scope_id", "audience"):
        result[field] = _text(result[field], field=field)
    for field in ("required_dimensions", "claim_ids", "candidate_answer_sections"):
        result[field] = _unique_text_list(result[field], field=field)
    unknown_sections = sorted(
        set(result["candidate_answer_sections"]) - ANSWER_SECTION_VOCABULARY
    )
    if unknown_sections:
        raise DecisionUseCaseError(
            f"candidate_answer_sections contains unknown sections: {unknown_sections}"
        )
    return result


def evaluate_use_case(
    *,
    use_case: Mapping[str, Any],
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
    observations: Iterable[Mapping[str, Any]],
    evidence_packets: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return whether a use case may emit a candidate-only answer context."""

    item = validate_use_case(use_case)
    context = project_decision_context(
        scope_id=item["scope_id"],
        audience=item["audience"],
        as_of=as_of,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
        observations=observations,
        required_dimensions=item["required_dimensions"],
        evidence_packets=evidence_packets,
        claim_ids=item["claim_ids"],
    )
    state_by_context = {
        "CANDIDATE_READY_WITH_COUNTEREVIDENCE": "CANDIDATE_ANSWER_READY",
        "CANDIDATE_WITH_GAPS": "CANDIDATE_ANSWER_WITH_GAPS",
        "HITL_REQUIRED": "BLOCKED_HITL_REQUIRED",
        "STALE_OR_UNKNOWN": "BLOCKED_STALE_OR_UNKNOWN",
    }
    state = state_by_context[context["state"]]
    assert state in ANSWER_STATES
    candidate_sections = (
        item["candidate_answer_sections"]
        if state in {"CANDIDATE_ANSWER_READY", "CANDIDATE_ANSWER_WITH_GAPS"}
        else []
    )
    return {
        "use_case_id": item["use_case_id"],
        "question": item["question"],
        "as_of": as_of,
        "known_at": known_at,
        "state": state,
        "answer_posture": "CANDIDATE_ONLY" if candidate_sections else "NO_ANSWER",
        "candidate_answer_sections": candidate_sections,
        "decision_context": context,
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
    }
