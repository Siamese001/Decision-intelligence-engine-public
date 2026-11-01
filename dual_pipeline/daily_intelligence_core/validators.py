"""Validators and artifact builder for daily intelligence."""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from ..core import stable_id
from ..reference_monitor import ReferenceMonitor, ReferenceMonitorError
from .constants import (
    DAILY_ARTIFACT_REQUIRED_FIELDS,
    DAILY_ARTIFACT_TYPES,
    DAILY_AUDIENCES,
    DAILY_STATES,
    DECISION_REQUIRED_FIELDS,
    DECISION_STATES,
    DEPENDENCY_REQUIRED_FIELDS,
    DEPENDENCY_STATES,
    DailyIntelligenceError,
    _classification_for,
    _minimum_expiry,
    _parse_time,
    _text,
    _text_list,
    _time_text,
    _validate_visibility,
)


def _validate_record_common(
    value: Mapping[str, Any],
    *,
    required_fields: frozenset[str],
    reference_monitor: ReferenceMonitor | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != required_fields:
        raise DailyIntelligenceError(f"record must contain exactly {sorted(required_fields)}")
    result = copy.deepcopy(dict(value))
    for field in required_fields - {"owner", "source_assertion_ids", "source_as_of", "recorded_at", "stale_after", "classification", "audiences"}:
        result[field] = _text(result[field], field=field)
    result["source_assertion_ids"] = _text_list(result["source_assertion_ids"], field="source_assertion_ids")
    source_as_of = _parse_time(result["source_as_of"], field="source_as_of")
    recorded_at = _parse_time(result["recorded_at"], field="recorded_at")
    stale_after = _parse_time(result["stale_after"], field="stale_after")
    if recorded_at < source_as_of:
        raise DailyIntelligenceError("recorded_at must not precede source_as_of")
    if stale_after <= recorded_at:
        raise DailyIntelligenceError("stale_after must be after source_as_of")
    result["source_as_of"] = _time_text(source_as_of)
    result["recorded_at"] = _time_text(recorded_at)
    result["stale_after"] = _time_text(stale_after)
    result["classification"], result["audiences"] = _validate_visibility(
        classification=result["classification"], audiences=result["audiences"]
    )
    if reference_monitor is not None:
        try:
            reference_monitor.verify_derived_labels(
                source_assertion_ids=result["source_assertion_ids"],
                classification=result["classification"],
                audiences=result["audiences"],
            )
        except ReferenceMonitorError as exc:
            raise DailyIntelligenceError(str(exc)) from exc
    return result


def validate_decision_record(
    value: Mapping[str, Any], *, reference_monitor: ReferenceMonitor | None = None
) -> dict[str, Any]:
    """Validate an immutable, source-bound decision record for W4."""
    result = _validate_record_common(
        value,
        required_fields=DECISION_REQUIRED_FIELDS,
        reference_monitor=reference_monitor,
    )
    if result["decision_state"] not in DECISION_STATES:
        raise DailyIntelligenceError("decision_state is outside the W4 vocabulary")
    owner = result["owner"]
    if owner is not None:
        result["owner"] = _text(owner, field="owner")
    return result


def validate_dependency_record(
    value: Mapping[str, Any], *, reference_monitor: ReferenceMonitor | None = None
) -> dict[str, Any]:
    """Validate an immutable, source-bound cross-scope dependency record for W4."""
    result = _validate_record_common(
        value,
        required_fields=DEPENDENCY_REQUIRED_FIELDS,
        reference_monitor=reference_monitor,
    )
    if result["dependency_state"] not in DEPENDENCY_STATES:
        raise DailyIntelligenceError("dependency_state is outside the W4 vocabulary")
    return result


def validate_daily_artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one derived, candidate-only W4 artifact."""
    if not isinstance(value, Mapping) or set(value) != DAILY_ARTIFACT_REQUIRED_FIELDS:
        raise DailyIntelligenceError(
            f"daily artifact must contain exactly {sorted(DAILY_ARTIFACT_REQUIRED_FIELDS)}"
        )
    result = copy.deepcopy(dict(value))
    for field in ("artifact_id", "title", "summary", "falsification_criteria"):
        result[field] = _text(result[field], field=field)
    if result["artifact_type"] not in DAILY_ARTIFACT_TYPES:
        raise DailyIntelligenceError("artifact_type is outside the W4 vocabulary")
    if result["authority"] != "DERIVED_CANDIDATE_ONLY":
        raise DailyIntelligenceError("daily artifacts must remain candidate-only")
    result["audience"] = _text(result["audience"], field="audience")
    if result["audience"] not in DAILY_AUDIENCES:
        raise DailyIntelligenceError("audience is outside the W4 audience registry")
    result["classification"], artifact_audiences = _validate_visibility(
        classification=result["classification"], audiences=[result["audience"]]
    )
    del artifact_audiences
    result["scope_ids"] = _text_list(result["scope_ids"], field="scope_ids")
    result["as_of"] = _time_text(_parse_time(result["as_of"], field="as_of"))
    result["known_at"] = _time_text(_parse_time(result["known_at"], field="known_at"))
    result["expires_at"] = _time_text(_parse_time(result["expires_at"], field="expires_at"))
    if result["state"] not in DAILY_STATES:
        raise DailyIntelligenceError("state is outside the W4 vocabulary")
    expected_review_state = "HITL_REQUIRED" if result["state"] == "HITL_REQUIRED" else "UNREVIEWED"
    if result["review_state"] != expected_review_state:
        raise DailyIntelligenceError("review_state does not match the candidate state")
    if not isinstance(result["findings"], list) or any(
        not isinstance(item, Mapping) for item in result["findings"]
    ):
        raise DailyIntelligenceError("findings must be a list of objects")
    result["findings"] = [copy.deepcopy(dict(item)) for item in result["findings"]]
    result["source_assertion_ids"] = _text_list(
        result["source_assertion_ids"], field="source_assertion_ids"
    )
    result["observation_ids"] = _text_list(
        result["observation_ids"], field="observation_ids", allow_empty=True
    )
    result["counterevidence_assertion_ids"] = _text_list(
        result["counterevidence_assertion_ids"],
        field="counterevidence_assertion_ids",
        allow_empty=True,
    )
    result["validation_questions"] = _text_list(
        result["validation_questions"], field="validation_questions"
    )
    if result["graph_mutation_authorized"] is not False:
        raise DailyIntelligenceError("daily artifacts cannot authorize graph mutation")
    return result


def _observation_metadata(
    observations: Iterable[Mapping[str, Any]], observation_ids: Iterable[str]
) -> tuple[list[str], list[str], str, str]:
    ids = sorted(set(observation_ids))
    by_id = {item["observation_id"]: item for item in observations}
    selected = [by_id[item] for item in ids if item in by_id]
    source_ids = sorted(
        {source_id for item in selected for source_id in item["source_assertion_ids"]}
    )
    return (
        source_ids,
        ids,
        _classification_for(item["classification"] for item in selected),
        _minimum_expiry((item["stale_after"] for item in selected), fallback="1970-01-01T00:00:00Z"),
    )


def _envelope(
    *, artifact_type: str, artifact: Mapping[str, Any] | None, state: str, gaps: Iterable[str]
) -> dict[str, Any]:
    return {
        "projection_type": artifact_type,
        "state": state,
        "artifact": None if artifact is None else validate_daily_artifact(artifact),
        "gaps": sorted(set(gaps)),
    }


def _unknown_projection(*, artifact_type: str, gaps: Iterable[str]) -> dict[str, Any]:
    return _envelope(
        artifact_type=artifact_type,
        artifact=None,
        state="STALE_OR_UNKNOWN",
        gaps=gaps,
    )


def _build_artifact(
    *,
    artifact_type: str,
    audience: str,
    scope_ids: Iterable[str],
    as_of: str,
    known_at: str,
    state: str,
    title: str,
    summary: str,
    findings: Iterable[Mapping[str, Any]],
    source_assertion_ids: Iterable[str],
    observation_ids: Iterable[str],
    classification: str,
    expires_at: str,
    validation_questions: Iterable[str],
    falsification_criteria: str,
    reference_monitor: ReferenceMonitor,
    counterevidence_assertion_ids: Iterable[str] = (),
) -> dict[str, Any]:
    normalized_scope_ids = sorted(set(scope_ids))
    normalized_source_ids = sorted(set(source_assertion_ids))
    normalized_observation_ids = sorted(set(observation_ids))
    normalized_findings = [copy.deepcopy(dict(item)) for item in findings]
    normalized_questions = list(validation_questions)
    try:
        derived_labels = reference_monitor.derived_labels(normalized_source_ids)
    except ReferenceMonitorError as exc:
        raise DailyIntelligenceError(str(exc)) from exc
    if classification != derived_labels["classification"]:
        raise DailyIntelligenceError("artifact classification is not source-derived")
    if audience not in derived_labels["audiences"]:
        raise DailyIntelligenceError("artifact audience is not source-derived")
    raw = {
        "artifact_id": stable_id(
            "daily-intelligence",
            artifact_type,
            audience,
            as_of,
            normalized_scope_ids,
            normalized_source_ids,
            normalized_observation_ids,
            normalized_findings,
        ),
        "artifact_type": artifact_type,
        "authority": "DERIVED_CANDIDATE_ONLY",
        "audience": audience,
        "classification": classification,
        "scope_ids": normalized_scope_ids,
        "as_of": as_of,
        "known_at": known_at,
        "state": state,
        "title": title,
        "summary": summary,
        "findings": normalized_findings,
        "source_assertion_ids": normalized_source_ids,
        "observation_ids": normalized_observation_ids,
        "counterevidence_assertion_ids": sorted(set(counterevidence_assertion_ids)),
        "review_state": "HITL_REQUIRED" if state == "HITL_REQUIRED" else "UNREVIEWED",
        "expires_at": expires_at,
        "validation_questions": normalized_questions,
        "falsification_criteria": falsification_criteria,
        "graph_mutation_authorized": False,
    }
    return validate_daily_artifact(raw)
