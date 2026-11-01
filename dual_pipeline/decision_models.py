"""Closed runtime models for Decision Intelligence query results.

The public representation remains JSON-compatible dictionaries because the
query runtime already persists JSON artifacts.  These validators keep the
new decision surface closed and make terminal semantics explicit without
introducing a second serialization framework.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping, Sequence

from ._validators import require_text
from .contracts import validate_artifact
from .core import digest

ARTIFACT_KEYS = frozenset(
    {"contract_id", "schema_version", "artifact_id", "canonical_digest", "extensions"}
)


class DecisionTerminalState(StrEnum):
    DECISION_PRODUCED = "DECISION_PRODUCED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NO_DECISION = "NO_DECISION"
    EVIDENCE_READY_DECISION_NOT_PRODUCED = "EVIDENCE_READY_DECISION_NOT_PRODUCED"


class ResponseTerminalState(StrEnum):
    DECISION_PRODUCED = DecisionTerminalState.DECISION_PRODUCED
    CLARIFICATION_REQUIRED = DecisionTerminalState.CLARIFICATION_REQUIRED
    NO_DECISION = DecisionTerminalState.NO_DECISION
    EVIDENCE_READY_DECISION_NOT_PRODUCED = (
        DecisionTerminalState.EVIDENCE_READY_DECISION_NOT_PRODUCED
    )
    EVIDENCE_ANSWER_PRODUCED = "EVIDENCE_ANSWER_PRODUCED"
    HANDOFF_READY = "HANDOFF_READY"
    REQUEST_FAILED = "REQUEST_FAILED"


class ConfidenceBand(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class AssessmentState(StrEnum):
    SUPPORTS = "SUPPORTS"
    OPPOSES = "OPPOSES"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


DECISION_FRAME_KEYS = ARTIFACT_KEYS | frozenset(
    {
        "decision_question",
        "desired_outcome",
        "time_horizon",
        "options",
        "criteria",
        "constraints",
        "assumptions",
        "missing_fields",
        "frame_state",
        "frame_digest",
    }
)

DECISION_RESULT_KEYS = ARTIFACT_KEYS | frozenset(
    {
        "terminal_state",
        "decision_frame",
        "evidence_pack_digest",
        "considered_options",
        "option_assessments",
        "trade_off_matrix",
        "recommended_option",
        "recommendation_rationale",
        "alternative_options",
        "risks",
        "constraints",
        "assumptions",
        "unknowns",
        "confidence",
        "what_would_change_the_conclusion",
        "material_conclusions",
        "execution",
        "decision_result_digest",
    }
)

RESPONSE_ENVELOPE_KEYS = ARTIFACT_KEYS | frozenset(
    {
        "request_id",
        "run_id",
        "destination",
        "handler",
        "terminal_state",
        "result",
        "citations",
        "retrieval_call_count",
        "provider_call_count",
        "latency_ms",
        "error",
        "response_digest",
    }
)


def _closed(value: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise ValueError(f"{label} keys are invalid; missing={missing}, unknown={unknown}")


def _text(value: object, *, field: str, allow_empty: bool = False) -> str:
    return require_text(value, field=field, allow_empty=allow_empty)


def _string_list(value: object, *, field: str, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a list of strings")
    result = [_text(item, field=field) for item in value]
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def validate_decision_frame(value: Mapping[str, Any]) -> dict[str, Any]:
    _closed(value, DECISION_FRAME_KEYS, label="decision frame")
    result = dict(value)
    if result["schema_version"] != "1.0.0":
        raise ValueError("decision frame schema_version must be 1.0.0")
    _text(result["decision_question"], field="decision_question")
    _text(result["desired_outcome"], field="desired_outcome")
    _text(result["time_horizon"], field="time_horizon")
    if not isinstance(result["options"], list) or any(
        not isinstance(item, Mapping) for item in result["options"]
    ):
        raise ValueError("decision frame options must be objects")
    if not isinstance(result["criteria"], list) or any(
        not isinstance(item, Mapping) for item in result["criteria"]
    ):
        raise ValueError("decision frame criteria must be objects")
    _string_list(result["constraints"], field="constraints")
    _string_list(result["assumptions"], field="assumptions")
    missing = _string_list(result["missing_fields"], field="missing_fields")
    expected_state = "CLARIFICATION_REQUIRED" if missing else "READY"
    if result["frame_state"] != expected_state:
        raise ValueError("decision frame state does not match missing fields")
    expected_digest = digest(
        {
            key: item
            for key, item in result.items()
            if key not in ARTIFACT_KEYS | {"frame_digest"}
        }
    )
    if result["frame_digest"] != expected_digest:
        raise ValueError("decision frame digest is invalid")
    validate_artifact(result)
    return result


def validate_decision_result(value: Mapping[str, Any]) -> dict[str, Any]:
    _closed(value, DECISION_RESULT_KEYS, label="decision result")
    result = dict(value)
    if result["schema_version"] != "1.0.0":
        raise ValueError("decision result schema_version must be 1.0.0")
    terminal = DecisionTerminalState(str(result["terminal_state"]))
    validate_decision_frame(result["decision_frame"])
    recommended = result["recommended_option"]
    if terminal is DecisionTerminalState.DECISION_PRODUCED and not isinstance(
        recommended, Mapping
    ):
        raise ValueError("DECISION_PRODUCED requires a recommended option")
    if terminal is not DecisionTerminalState.DECISION_PRODUCED and recommended is not None:
        raise ValueError("a non-decision terminal state cannot contain a recommendation")
    confidence = result["confidence"]
    if not isinstance(confidence, Mapping):
        raise ValueError("decision confidence must be an object")
    ConfidenceBand(str(confidence.get("band")))
    conclusions = result["material_conclusions"]
    if not isinstance(conclusions, list) or any(
        not isinstance(item, Mapping) or not item.get("evidence_ids") for item in conclusions
    ):
        if terminal is DecisionTerminalState.DECISION_PRODUCED:
            raise ValueError("a produced decision requires evidence-bound conclusions")
    expected_digest = digest(
        {
            key: item
            for key, item in result.items()
            if key not in ARTIFACT_KEYS | {"decision_result_digest"}
        }
    )
    if result["decision_result_digest"] != expected_digest:
        raise ValueError("decision result digest is invalid")
    validate_artifact(result)
    return result


def validate_response_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    _closed(value, RESPONSE_ENVELOPE_KEYS, label="response envelope")
    result = dict(value)
    if result["schema_version"] != "1.0.0":
        raise ValueError("response envelope schema_version must be 1.0.0")
    ResponseTerminalState(str(result["terminal_state"]))
    _text(result["request_id"], field="request_id")
    _text(result["destination"], field="destination")
    _text(result["handler"], field="handler")
    _string_list(result["citations"], field="citations")
    for field in ("retrieval_call_count", "provider_call_count", "latency_ms"):
        if not isinstance(result[field], int | float) or result[field] < 0:
            raise ValueError(f"{field} must be nonnegative")
    expected_digest = digest(
        {
            key: item
            for key, item in result.items()
            if key not in ARTIFACT_KEYS | {"response_digest"}
        }
    )
    if result["response_digest"] != expected_digest:
        raise ValueError("response envelope digest is invalid")
    validate_artifact(result)
    return result


__all__ = [
    "AssessmentState",
    "ConfidenceBand",
    "DecisionTerminalState",
    "ResponseTerminalState",
    "validate_decision_frame",
    "validate_decision_result",
    "validate_response_envelope",
]
