"""Change brief and decision inbox projections."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..authorization import AuthorizationContext
from ..core import digest
from ..reference_monitor import ReferenceMonitor
from ..temporal_observations import project_observation_state
from .constants import (
    DailyIntelligenceError,
    _classification_for,
    _minimum_expiry,
    _parse_time,
    _projection_ids,
    _state_from_w3,
)
from .validators import (
    _build_artifact,
    _envelope,
    _observation_metadata,
    _unknown_projection,
    validate_decision_record,
)
from .visibility import _visible_observations, _visible_records


def project_change_brief(
    observations: Iterable[Mapping[str, Any]],
    *,
    previous_as_of: str,
    as_of: str,
    audience: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    """Project material temporal changes into a candidate-only change brief."""

    previous_time = _parse_time(previous_as_of, field="previous_as_of")
    as_of_time = _parse_time(as_of, field="as_of")
    if previous_time > as_of_time:
        raise DailyIntelligenceError("previous_as_of must not follow as_of")
    visible = _visible_observations(
        observations,
        audience=audience,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
    )
    if not visible:
        return _unknown_projection(artifact_type="change_brief", gaps=["no_visible_observations"])
    pairs = sorted({(item["scope_id"], item["dimension"]) for item in visible})
    findings: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    states: list[str] = []
    for scope_id, dimension in pairs:
        before = project_observation_state(
            visible,
            scope_id=scope_id,
            dimension=dimension,
            as_of=previous_as_of,
            known_at=known_at,
            audience=audience,
            authorization_context=authorization_context,
            reference_monitor=reference_monitor,
        )
        current = project_observation_state(
            visible,
            scope_id=scope_id,
            dimension=dimension,
            as_of=as_of,
            known_at=known_at,
            audience=audience,
            authorization_context=authorization_context,
            reference_monitor=reference_monitor,
        )
        states.append(current["state"])
        changed = before["state"] != current["state"] or (
            before["state"] == "CURRENT"
            and current["state"] == "CURRENT"
            and digest(before["value"]) != digest(current["value"])
        )
        if not changed:
            continue
        finding = {
            "scope_id": scope_id,
            "dimension": dimension,
            "previous_state": before["state"],
            "current_state": current["state"],
            "previous_value": before.get("value"),
            "current_value": current.get("value"),
        }
        findings.append(finding)
        selected_ids.update(_projection_ids(before))
        selected_ids.update(_projection_ids(current))
        states.append(before["state"])
    if not findings:
        selected_ids.update(item["observation_id"] for item in visible)
        states.append("CURRENT")
    source_ids, observation_ids, classification, expiry = _observation_metadata(
        visible, selected_ids
    )
    if not source_ids:
        return _unknown_projection(artifact_type="change_brief", gaps=["no_source_bound_observations"])
    state = _state_from_w3(states)
    gaps: list[str] = []
    if state == "HITL_REQUIRED":
        gaps.append("conflicting_observations")
    elif state == "STALE_OR_UNKNOWN":
        gaps.append("stale_or_missing_observations")
    question = (
        "Resolve the conflicting source-bound observations before treating this change as authoritative."
        if state == "HITL_REQUIRED"
        else "Confirm whether a newer source-bound observation changes this briefing."
    )
    artifact = _build_artifact(
        artifact_type="change_brief",
        audience=audience,
        scope_ids=[finding["scope_id"] for finding in findings] or [item["scope_id"] for item in visible],
        as_of=as_of,
        known_at=known_at,
        state=state,
        title="Material change brief",
        summary=(
            f"{len(findings)} material source-bound observation change(s) were found."
            if findings
            else "No material source-bound observation change was found in the requested interval."
        ),
        findings=findings,
        source_assertion_ids=source_ids,
        observation_ids=observation_ids,
        classification=classification,
        expires_at=expiry,
        validation_questions=[question],
        falsification_criteria="A newer source-bound observation with a different projected state or value invalidates this brief.",
        reference_monitor=reference_monitor,
    )
    return _envelope(artifact_type="change_brief", artifact=artifact, state=state, gaps=gaps)


def project_decision_inbox(
    decisions: Iterable[Mapping[str, Any]],
    *,
    audience: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    """Project current unresolved decisions without treating them as recommendations."""

    as_of_time = _parse_time(as_of, field="as_of")
    visible = _visible_records(
        decisions,
        audience=audience,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
        validator=validate_decision_record,
    )
    active = [
        item
        for item in visible
        if _parse_time(item["stale_after"], field="stale_after") >= as_of_time
    ]
    if not active:
        return _unknown_projection(artifact_type="decision_inbox", gaps=["no_current_visible_decisions"])
    unresolved = [item for item in active if item["decision_state"] in {"OPEN", "BLOCKED"}]
    source_rows = unresolved or active
    state = (
        "HITL_REQUIRED"
        if any(item["decision_state"] == "BLOCKED" or item["owner"] is None for item in unresolved)
        else "READY_FOR_REVIEW"
    )
    findings = [
        {
            "decision_id": item["decision_id"],
            "scope_id": item["scope_id"],
            "title": item["title"],
            "decision_state": item["decision_state"],
            "owner": item["owner"],
            "consequence_of_delay": item["consequence_of_delay"],
        }
        for item in unresolved
    ]
    source_ids = sorted(
        {source_id for item in source_rows for source_id in item["source_assertion_ids"]}
    )
    artifact = _build_artifact(
        artifact_type="decision_inbox",
        audience=audience,
        scope_ids=[item["scope_id"] for item in source_rows],
        as_of=as_of,
        known_at=known_at,
        state=state,
        title="Current decision inbox",
        summary=(
            f"{len(unresolved)} current unresolved decision(s) require monitoring."
            if unresolved
            else "No current unresolved decisions were found in the supplied records."
        ),
        findings=findings,
        source_assertion_ids=source_ids,
        observation_ids=[],
        classification=_classification_for(item["classification"] for item in source_rows),
        expires_at=_minimum_expiry((item["stale_after"] for item in source_rows), fallback=as_of),
        validation_questions=[
            "Assign an owner and confirm the consequence of delay for each unresolved decision."
            if any(item["owner"] is None for item in unresolved)
            else "Confirm whether a newer source-bound decision record changes this inbox."
        ],
        falsification_criteria="A newer source-bound decision record that resolves or changes an item invalidates this inbox entry.",
        reference_monitor=reference_monitor,
    )
    gaps = ["unowned_or_blocked_decision"] if state == "HITL_REQUIRED" else []
    return _envelope(artifact_type="decision_inbox", artifact=artifact, state=state, gaps=gaps)
