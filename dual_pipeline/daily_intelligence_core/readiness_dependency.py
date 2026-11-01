"""Readiness gap and cross-scope dependency warning projections."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..authorization import AuthorizationContext
from ..reference_monitor import ReferenceMonitor
from ..temporal_observations import (
    OBSERVATION_DIMENSIONS,
    project_observation_state,
)
from .constants import (
    DailyIntelligenceError,
    _classification_for,
    _minimum_expiry,
    _parse_time,
    _projection_ids,
    _state_from_w3,
    _text_list,
)
from .validators import (
    _build_artifact,
    _envelope,
    _observation_metadata,
    _unknown_projection,
    validate_dependency_record,
)
from .visibility import _visible_observations, _visible_records


def project_readiness_gaps(
    observations: Iterable[Mapping[str, Any]],
    *,
    scope_ids: Iterable[str],
    required_dimensions: Iterable[str],
    audience: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    """Project missing, stale, and conflicting readiness evidence explicitly."""

    normalized_scope_ids = _text_list(list(scope_ids), field="scope_ids")
    dimensions = _text_list(list(required_dimensions), field="required_dimensions")
    if set(dimensions) - OBSERVATION_DIMENSIONS:
        raise DailyIntelligenceError("required_dimensions contains an unknown W3 dimension")
    visible = _visible_observations(
        observations,
        audience=audience,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
    )
    findings: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    states: list[str] = []
    for scope_id in normalized_scope_ids:
        for dimension in dimensions:
            projection = project_observation_state(
                visible,
                scope_id=scope_id,
                dimension=dimension,
                as_of=as_of,
                known_at=known_at,
                audience=audience,
                authorization_context=authorization_context,
                reference_monitor=reference_monitor,
            )
            if projection["state"] == "CURRENT":
                selected_ids.update(_projection_ids(projection))
                continue
            findings.append(
                {
                    "scope_id": scope_id,
                    "dimension": dimension,
                    "state": projection["state"],
                    "observation_ids": _projection_ids(projection),
                }
            )
            selected_ids.update(_projection_ids(projection))
            states.append(projection["state"])
    if not findings:
        selected_ids.update(item["observation_id"] for item in visible if item["scope_id"] in normalized_scope_ids)
        states.append("CURRENT")
    source_ids, observation_ids, classification, expiry = _observation_metadata(
        visible, selected_ids
    )
    if not source_ids:
        return _unknown_projection(
            artifact_type="readiness_gap", gaps=["missing_required_observations"]
        )
    state = _state_from_w3(states)
    gaps = [
        "conflicting_readiness_evidence"
        if state == "HITL_REQUIRED"
        else "missing_or_stale_readiness_evidence"
    ] if findings else []
    artifact = _build_artifact(
        artifact_type="readiness_gap",
        audience=audience,
        scope_ids=normalized_scope_ids,
        as_of=as_of,
        known_at=known_at,
        state=state,
        title="Readiness evidence gaps",
        summary=(
            f"{len(findings)} required readiness observation gap(s) were found."
            if findings
            else "No readiness evidence gap was found for the requested scope and dimensions."
        ),
        findings=findings,
        source_assertion_ids=source_ids,
        observation_ids=observation_ids,
        classification=classification,
        expires_at=expiry,
        validation_questions=[
            "Resolve conflicting observations before using this readiness view."
            if state == "HITL_REQUIRED"
            else "Collect a current, source-bound observation for each missing or stale readiness dimension."
        ],
        falsification_criteria="A current, non-conflicting source-bound observation for every required dimension invalidates this gap.",
        reference_monitor=reference_monitor,
    )
    return _envelope(artifact_type="readiness_gap", artifact=artifact, state=state, gaps=gaps)


def project_dependency_warnings(
    dependencies: Iterable[Mapping[str, Any]],
    *,
    audience: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    """Project current cross-scope dependency warnings as candidate evidence."""

    as_of_time = _parse_time(as_of, field="as_of")
    visible = _visible_records(
        dependencies,
        audience=audience,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
        validator=validate_dependency_record,
    )
    active = [
        item
        for item in visible
        if _parse_time(item["stale_after"], field="stale_after") >= as_of_time
    ]
    if not active:
        return _unknown_projection(
            artifact_type="dependency_warning", gaps=["no_current_visible_dependencies"]
        )
    unresolved = [item for item in active if item["dependency_state"] in {"OPEN", "BLOCKED"}]
    source_rows = unresolved or active
    state = "HITL_REQUIRED" if any(item["dependency_state"] == "BLOCKED" for item in unresolved) else "READY_FOR_REVIEW"
    findings = [
        {
            "dependency_id": item["dependency_id"],
            "scope_id": item["scope_id"],
            "depends_on_scope_id": item["depends_on_scope_id"],
            "title": item["title"],
            "dependency_state": item["dependency_state"],
            "impact": item["impact"],
        }
        for item in unresolved
    ]
    source_ids = sorted(
        {source_id for item in source_rows for source_id in item["source_assertion_ids"]}
    )
    artifact = _build_artifact(
        artifact_type="dependency_warning",
        audience=audience,
        scope_ids=[
            scope_id
            for item in source_rows
            for scope_id in (item["scope_id"], item["depends_on_scope_id"])
        ],
        as_of=as_of,
        known_at=known_at,
        state=state,
        title="Cross-scope dependency warnings",
        summary=(
            f"{len(unresolved)} current unresolved cross-scope dependency warning(s) were found."
            if unresolved
            else "No current unresolved cross-scope dependencies were found in the supplied records."
        ),
        findings=findings,
        source_assertion_ids=source_ids,
        observation_ids=[],
        classification=_classification_for(item["classification"] for item in source_rows),
        expires_at=_minimum_expiry((item["stale_after"] for item in source_rows), fallback=as_of),
        validation_questions=[
            "Confirm the owner, dependency state, and impact before escalating the blocked dependency."
            if state == "HITL_REQUIRED"
            else "Confirm whether a newer source-bound dependency record changes this warning."
        ],
        falsification_criteria="A newer source-bound dependency record that resolves or changes the dependency invalidates this warning.",
        reference_monitor=reference_monitor,
    )
    gaps = ["blocked_dependency_requires_review"] if state == "HITL_REQUIRED" else []
    return _envelope(artifact_type="dependency_warning", artifact=artifact, state=state, gaps=gaps)
