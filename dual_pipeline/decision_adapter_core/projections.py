"""Projection and evidence packet generators for decision adapter."""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from ..authorization import AuthorizationContext
from ..core import stable_id
from ..reference_monitor import ReferenceMonitor, ReferenceMonitorError
from ..strategic_evidence import build_evidence_packet
from ..temporal_observations import OBSERVATION_DIMENSIONS, build_observation
from .constants import (
    RuntimeDecisionSourceError,
    _one_second_after,
    _projection,
    _text,
    _timestamp,
    _tokens,
)


def _shell(record: Mapping[str, Any]) -> dict[str, Any]:
    source = record["source_assertion"]
    return {
        "source_assertion_ids": [source["source_assertion_id"]],
        "classification": source["classification"],
        "audiences": list(source["audiences"]),
        "recorded_at": record["recorded_at"],
    }


def _score(record: Mapping[str, Any], query_terms: set[str]) -> int:
    corpus = " ".join(
        (
            str(record["subject_name"]),
            str(record["assertion_text"]),
            str(record["scope_text"]),
            str(record["source_name"]),
        )
    )
    return len(_tokens(corpus) & query_terms)


def _visible_records(
    reference_monitor: ReferenceMonitor,
    *,
    records: Iterable[Mapping[str, Any]],
    audience: str,
    authorization_context: AuthorizationContext,
    known_at: str,
) -> list[dict[str, Any]]:
    indexed = {
        item["source_assertion"]["source_assertion_id"]: dict(item) for item in records
    }
    shells = [_shell(item) for item in indexed.values()]
    try:
        visible = reference_monitor.filter_retrieval_records(
            shells,
            audience=audience,
            authorization_context=authorization_context,
            known_at=known_at,
        )
    except ReferenceMonitorError as exc:
        raise RuntimeDecisionSourceError(str(exc)) from exc
    visible_ids = [item["source_assertion_ids"][0] for item in visible]
    return [indexed[item] for item in visible_ids]


def _evidence_state(record: Mapping[str, Any]) -> tuple[str, None, None]:
    """Mark typed source-bound graph evidence without a reviewer handoff."""
    del record
    return ("SOURCE_BOUND", None, None)


def _projection_for_use_case(
    record: Mapping[str, Any], *, use_case_id: str
) -> dict[str, Any] | None:
    projection = _projection(record["qualifiers"])
    if projection is None or use_case_id not in projection["use_case_ids"]:
        return None
    return projection


def _source_content(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return direct graph fields only after monitor visibility has passed."""
    return {
        "projection_kind": "source_bound_graph_assertion",
        "graph_assertion_id": record["assertion_id"],
        "evidence_id": record["evidence_id"],
        "subject_node_id": record["subject_node_id"],
        "subject_name": record["subject_name"],
        "predicate": record["predicate"],
        "assertion_text": record["assertion_text"],
        "source_name": record["source_name"],
    }


def _context_packet(
    reference_monitor: ReferenceMonitor,
    *,
    record: Mapping[str, Any],
    claim_id: str,
    use_case_id: str,
) -> dict[str, Any]:
    source = record["source_assertion"]
    return build_evidence_packet(
        evidence_id=stable_id(
            "runtime-unmapped-context",
            use_case_id,
            source["source_assertion_id"],
        ),
        claim_id=claim_id,
        evidence_role="context",
        content={
            **_source_content(record),
            "projection_boundary": (
                "No explicit decision_intelligence_projection qualifier authorizes this "
                "source as typed status, supporting evidence, or counterevidence."
            ),
        },
        source_assertion_ids=[source["source_assertion_id"]],
        source_as_of=source["source_as_of"],
        recorded_at=record["recorded_at"],
        # No source-specific freshness interval was declared.  This is an
        # intentionally conservative expiry control, not an assertion that
        # the source lost validity one second later.
        expires_at=_one_second_after(record["recorded_at"]),
        classification=source["classification"],
        audiences=source["audiences"],
        review_state="SOURCE_BOUND",
        reviewed_by=None,
        reviewed_at=None,
        falsification_tests=[],
        reference_monitor=reference_monitor,
    )


def _observation_from_mapping(
    *,
    record: Mapping[str, Any],
    use_case_id: str,
    scope_id: str,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {"dimension", "value", "observed_at", "stale_after"}
    if set(observation) != expected:
        raise RuntimeDecisionSourceError(
            "decision_intelligence_projection observation has an invalid shape"
        )
    dimension = _text(observation["dimension"], field="projection observation dimension")
    if dimension not in OBSERVATION_DIMENSIONS:
        raise RuntimeDecisionSourceError("projection observation dimension is invalid")
    observed_at = _timestamp(
        observation["observed_at"], field="projection observation observed_at"
    )
    stale_after = _timestamp(
        observation["stale_after"], field="projection observation stale_after"
    )
    source = record["source_assertion"]
    return build_observation(
        observation_id=stable_id(
            "runtime-observation",
            use_case_id,
            source["source_assertion_id"],
            dimension,
            observed_at,
        ),
        scope_id=scope_id,
        dimension=dimension,
        value=copy.deepcopy(observation["value"]),
        observed_at=observed_at,
        recorded_at=record["recorded_at"],
        source_as_of=source["source_as_of"],
        stale_after=stale_after,
        source_assertion_ids=[source["source_assertion_id"]],
        classification=source["classification"],
        audiences=source["audiences"],
    )


def _evidence_from_mapping(
    reference_monitor: ReferenceMonitor,
    *,
    record: Mapping[str, Any],
    use_case_id: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {"claim_id", "evidence_role", "expires_at"}
    if set(evidence) != expected:
        raise RuntimeDecisionSourceError(
            "decision_intelligence_projection evidence has an invalid shape"
        )
    claim_id = _text(evidence["claim_id"], field="projection evidence claim_id")
    role = _text(evidence["evidence_role"], field="projection evidence role")
    if role not in {"supporting", "counterevidence", "context"}:
        raise RuntimeDecisionSourceError("projection evidence role is invalid")
    expires_at = _timestamp(evidence["expires_at"], field="projection evidence expires_at")
    source = record["source_assertion"]
    review_state, reviewed_by, reviewed_at = _evidence_state(record)
    return build_evidence_packet(
        evidence_id=stable_id(
            "runtime-evidence",
            use_case_id,
            source["source_assertion_id"],
            claim_id,
            role,
        ),
        claim_id=claim_id,
        evidence_role=role,
        content={
            **_source_content(record),
            "projection_kind": "source_bound_declared_projection",
        },
        source_assertion_ids=[source["source_assertion_id"]],
        source_as_of=source["source_as_of"],
        recorded_at=record["recorded_at"],
        expires_at=expires_at,
        classification=source["classification"],
        audiences=source["audiences"],
        review_state=review_state,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        falsification_tests=[
            {
                "test_id": stable_id(
                    "runtime-falsification", source["source_assertion_id"], claim_id
                ),
                "question": (
                    "Does a later or conflicting governed source change this "
                    "candidate-only projection?"
                ),
            }
        ],
        reference_monitor=reference_monitor,
    )
