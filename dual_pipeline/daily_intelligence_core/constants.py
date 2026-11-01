"""Constants, errors, and basic formatting helpers for daily intelligence."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from .._validators import format_time_text, parse_iso_time, require_text
from ..exceptions import DualPipelineValueError

DAILY_ARTIFACT_TYPES = frozenset(
    {
        "change_brief",
        "decision_inbox",
        "readiness_gap",
        "dependency_warning",
        "meeting_brief",
    }
)
DAILY_STATES = frozenset({"READY_FOR_REVIEW", "STALE_OR_UNKNOWN", "HITL_REQUIRED"})
DAILY_AUDIENCES = frozenset(
    {"nee-engineering", "client_approved_external", "internal_consulting", "internal_working_team"}
)
CLIENT_AUDIENCES = frozenset({"nee-engineering", "client_approved_external"})
RESTRICTED_AUDIENCES = frozenset({"internal_working_team"})
CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})
CLASSIFICATION_RISK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
DECISION_STATES = frozenset({"OPEN", "BLOCKED", "DECIDED", "DEFERRED"})
DEPENDENCY_STATES = frozenset({"OPEN", "BLOCKED", "RESOLVED"})
DECISION_REQUIRED_FIELDS = frozenset(
    {
        "decision_id",
        "scope_id",
        "title",
        "decision_state",
        "owner",
        "consequence_of_delay",
        "source_assertion_ids",
        "source_as_of",
        "recorded_at",
        "stale_after",
        "classification",
        "audiences",
    }
)
DEPENDENCY_REQUIRED_FIELDS = frozenset(
    {
        "dependency_id",
        "scope_id",
        "depends_on_scope_id",
        "title",
        "dependency_state",
        "impact",
        "source_assertion_ids",
        "source_as_of",
        "recorded_at",
        "stale_after",
        "classification",
        "audiences",
    }
)
DAILY_ARTIFACT_REQUIRED_FIELDS = frozenset(
    {
        "artifact_id",
        "artifact_type",
        "authority",
        "audience",
        "classification",
        "scope_ids",
        "as_of",
        "known_at",
        "state",
        "title",
        "summary",
        "findings",
        "source_assertion_ids",
        "observation_ids",
        "counterevidence_assertion_ids",
        "review_state",
        "expires_at",
        "validation_questions",
        "falsification_criteria",
        "graph_mutation_authorized",
    }
)


class DailyIntelligenceError(DualPipelineValueError):
    """Raised when a W4 projection violates its boundary contract."""


def _parse_time(value: object, *, field: str) -> datetime:
    result = parse_iso_time(value, field=field, error_type=DailyIntelligenceError)
    assert result is not None
    return result


def _time_text(value: datetime) -> str:
    result = format_time_text(value)
    assert result is not None
    return result


def _text(value: object, *, field: str) -> str:
    return require_text(value, field=field, error_type=DailyIntelligenceError)


def _text_list(value: object, *, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise DailyIntelligenceError(f"{field} must be a {'possibly empty' if allow_empty else 'nonempty'} list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise DailyIntelligenceError(f"{field} must contain nonblank text")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise DailyIntelligenceError(f"{field} must not contain duplicates")
    return sorted(normalized)


def _validate_visibility(*, classification: object, audiences: object) -> tuple[str, list[str]]:
    normalized_classification = _text(classification, field="classification")
    if normalized_classification not in CLASSIFICATIONS:
        raise DailyIntelligenceError("classification is outside the W4 vocabulary")
    normalized_audiences = _text_list(audiences, field="audiences")
    unknown = sorted(set(normalized_audiences) - DAILY_AUDIENCES)
    if unknown:
        raise DailyIntelligenceError(f"audiences contains unsupported or private labels: {unknown}")
    audience_set = set(normalized_audiences)
    if audience_set & CLIENT_AUDIENCES and audience_set & RESTRICTED_AUDIENCES:
        raise DailyIntelligenceError("client and restricted audiences cannot be mixed")
    if normalized_classification in {"confidential", "restricted"} and audience_set & CLIENT_AUDIENCES:
        raise DailyIntelligenceError("confidential or restricted records cannot target a client audience")
    return normalized_classification, normalized_audiences


def _validate_projection_audience(audience: str) -> None:
    if not isinstance(audience, str) or audience not in DAILY_AUDIENCES:
        raise DailyIntelligenceError("projection audience is outside the W4 audience registry")


def _state_from_w3(states: Iterable[str]) -> str:
    values = set(states)
    if "CONFLICT" in values:
        return "HITL_REQUIRED"
    if "STALE_OR_UNKNOWN" in values:
        return "STALE_OR_UNKNOWN"
    return "READY_FOR_REVIEW"


def _minimum_expiry(values: Iterable[str], *, fallback: str) -> str:
    parsed = [_parse_time(value, field="stale_after") for value in values]
    return _time_text(min(parsed)) if parsed else _time_text(_parse_time(fallback, field="as_of"))


def _classification_for(values: Iterable[str]) -> str:
    classifications = list(values)
    return max(classifications, key=lambda item: CLASSIFICATION_RISK[item]) if classifications else "internal"


def _projection_ids(projection: Mapping[str, Any]) -> list[str]:
    return sorted(
        set(projection.get("observation_ids", []))
        | set(projection.get("future_observation_ids", []))
    )
