"""Meeting brief aggregator and composite daily intelligence orchestrator."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..authorization import AuthorizationContext
from ..reference_monitor import ReferenceMonitor, ReferenceMonitorError
from .change_brief import (
    project_change_brief,
    project_decision_inbox,
)
from .constants import (
    DailyIntelligenceError,
    _classification_for,
    _minimum_expiry,
    _validate_projection_audience,
)
from .readiness_dependency import (
    project_dependency_warnings,
    project_readiness_gaps,
)
from .validators import (
    _build_artifact,
    _envelope,
    _unknown_projection,
    validate_daily_artifact,
)


def project_meeting_brief(
    projections: Iterable[Mapping[str, Any]],
    *,
    audience: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    """Aggregate W4 projections into a candidate-only meeting brief."""

    _validate_projection_audience(audience)
    if not isinstance(reference_monitor, ReferenceMonitor):
        raise DailyIntelligenceError("a ReferenceMonitor is required for projection")
    artifacts: list[dict[str, Any]] = []
    try:
        for projection in projections:
            if not isinstance(projection, Mapping) or projection.get("artifact") is None:
                continue
            item = validate_daily_artifact(projection["artifact"])
            labels = reference_monitor.derived_labels(item["source_assertion_ids"])
            if (
                item["classification"] != labels["classification"]
                or item["audience"] not in labels["audiences"]
            ):
                raise DailyIntelligenceError("meeting artifact is not source-derived")
            if reference_monitor.visible(
                source_assertion_ids=item["source_assertion_ids"],
                classification=labels["classification"],
                audiences=labels["audiences"],
                audience=audience,
                authorization_context=authorization_context,
                known_at=known_at,
                recorded_at=item["as_of"],
            ):
                artifacts.append(item)
    except ReferenceMonitorError as exc:
        raise DailyIntelligenceError(str(exc)) from exc
    if not artifacts:
        return _unknown_projection(artifact_type="meeting_brief", gaps=["no_material_daily_evidence"])
    if any(item["audience"] != audience for item in artifacts):
        raise DailyIntelligenceError("meeting brief cannot mix audiences")
    state = (
        "HITL_REQUIRED"
        if any(item["state"] == "HITL_REQUIRED" for item in artifacts)
        else "STALE_OR_UNKNOWN"
        if any(item["state"] == "STALE_OR_UNKNOWN" for item in artifacts)
        else "READY_FOR_REVIEW"
    )
    artifact = _build_artifact(
        artifact_type="meeting_brief",
        audience=audience,
        scope_ids=[scope_id for item in artifacts for scope_id in item["scope_ids"]],
        as_of=as_of,
        known_at=known_at,
        state=state,
        title="Daily decision-intelligence meeting brief",
        summary=f"{len(artifacts)} candidate daily artifact(s) were assembled for review.",
        findings=[
            {
                "artifact_id": item["artifact_id"],
                "artifact_type": item["artifact_type"],
                "state": item["state"],
                "title": item["title"],
            }
            for item in artifacts
        ],
        source_assertion_ids=[
            source_id for item in artifacts for source_id in item["source_assertion_ids"]
        ],
        observation_ids=[
            observation_id for item in artifacts for observation_id in item["observation_ids"]
        ],
        counterevidence_assertion_ids=[
            source_id
            for item in artifacts
            for source_id in item["counterevidence_assertion_ids"]
        ],
        classification=_classification_for(item["classification"] for item in artifacts),
        expires_at=_minimum_expiry((item["expires_at"] for item in artifacts), fallback=as_of),
        validation_questions=[
            question
            for item in artifacts
            for question in item["validation_questions"]
        ],
        falsification_criteria="A newer source-bound artifact or observation that changes a constituent item invalidates this meeting brief.",
        reference_monitor=reference_monitor,
    )
    gaps = ["candidate_items_require_review"] if state == "HITL_REQUIRED" else []
    return _envelope(artifact_type="meeting_brief", artifact=artifact, state=state, gaps=gaps)


def project_daily_intelligence(
    *,
    observations: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    dependencies: Iterable[Mapping[str, Any]],
    scope_ids: Iterable[str],
    required_dimensions: Iterable[str],
    audience: str,
    previous_as_of: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, dict[str, Any]]:
    """Build the five W4 daily projections without graph or provider access."""

    observation_rows = list(observations)
    change_brief = project_change_brief(
        observation_rows,
        previous_as_of=previous_as_of,
        as_of=as_of,
        audience=audience,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
    )
    decision_inbox = project_decision_inbox(
        decisions,
        audience=audience,
        as_of=as_of,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
    )
    readiness_gap = project_readiness_gaps(
        observation_rows,
        scope_ids=scope_ids,
        required_dimensions=required_dimensions,
        audience=audience,
        as_of=as_of,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
    )
    dependency_warning = project_dependency_warnings(
        dependencies,
        audience=audience,
        as_of=as_of,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
    )
    meeting_brief = project_meeting_brief(
        [change_brief, decision_inbox, readiness_gap, dependency_warning],
        audience=audience,
        as_of=as_of,
        known_at=known_at,
        authorization_context=authorization_context,
        reference_monitor=reference_monitor,
    )
    return {
        "change_brief": change_brief,
        "decision_inbox": decision_inbox,
        "readiness_gap": readiness_gap,
        "dependency_warning": dependency_warning,
        "meeting_brief": meeting_brief,
    }
