"""Constants, error classes, and validation helpers for decision adapter."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .._validators import require_sha256, require_text
from ..exceptions import DualPipelineValueError
from ..parameters import get_decision_service_parameters
from ..reference_monitor import AUDIENCE_REGISTRY

RUNTIME_SOURCE_ADAPTER_ID = "nee-runtime-decision-source-adapter"
RUNTIME_SOURCE_ADAPTER_VERSION = "1.0"
PROJECTION_QUALIFIER = "decision_intelligence_projection"
PROJECTION_SCHEMA_VERSION = "1.0"
MAX_CONTEXT_RECORDS = int(get_decision_service_parameters()["max_context_records"])
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "around",
        "based",
        "be",
        "become",
        "could",
        "current",
        "for",
        "given",
        "in",
        "is",
        "of",
        "or",
        "should",
        "the",
        "to",
        "what",
        "where",
        "which",
        "while",
        "with",
    }
)


class RuntimeDecisionSourceError(DualPipelineValueError):
    """Raised when a graph cannot provide a safe DI source projection."""


def _text(value: object, *, field: str) -> str:
    return require_text(value, field=field, error_type=RuntimeDecisionSourceError)


def _sha256(value: object, *, field: str) -> str:
    return require_sha256(value, field=field, error_type=RuntimeDecisionSourceError)


def _timestamp(value: object, *, field: str) -> str:
    raw = _text(value, field=field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeDecisionSourceError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parsed_timestamp(value: object, *, field: str) -> datetime:
    return datetime.fromisoformat(_timestamp(value, field=field).replace("Z", "+00:00"))


def _one_second_after(value: str) -> str:
    parsed = _parsed_timestamp(value, field="recorded_at") + timedelta(seconds=1)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RuntimeDecisionSourceError(f"{field} must be a JSON object string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeDecisionSourceError(f"{field} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeDecisionSourceError(f"{field} must contain a JSON object")
    return parsed


def _audiences(value: object) -> list[str]:
    if not isinstance(value, str):
        raise RuntimeDecisionSourceError("allowed_audiences_json must be a JSON array string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeDecisionSourceError("allowed_audiences_json is not valid JSON") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(item, str) or not item.strip() for item in parsed)
    ):
        raise RuntimeDecisionSourceError("allowed_audiences_json must be a nonempty text array")
    normalized = sorted(item.strip() for item in parsed)
    if len(set(normalized)) != len(normalized):
        raise RuntimeDecisionSourceError("allowed_audiences_json contains duplicates")
    unknown = sorted(set(normalized) - AUDIENCE_REGISTRY)
    if unknown:
        raise RuntimeDecisionSourceError(
            f"allowed_audiences_json has non-DI audiences: {unknown}"
        )
    return normalized


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in TOKEN_RE.findall(value.casefold())
        if len(token) > 1 and token not in STOPWORDS
    }


def _nonempty_unique_text_list(value: object, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise RuntimeDecisionSourceError(f"{field} must be a nonempty text list")
    result = sorted(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise RuntimeDecisionSourceError(f"{field} must not contain duplicates")
    return result


def _mapping_list(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise RuntimeDecisionSourceError(f"{field} must be a list of objects")
    return [dict(item) for item in value]


def _row_source_assertion_id(assertion_id: str, evidence_id: str) -> str:
    return f"graph-assertion:{assertion_id}:evidence:{evidence_id}"


def _projection(value: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = value.get(PROJECTION_QUALIFIER)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise RuntimeDecisionSourceError(f"{PROJECTION_QUALIFIER} must be an object")
    result = dict(raw)
    expected = {"schema_version", "use_case_ids", "observations", "evidence"}
    if set(result) != expected:
        raise RuntimeDecisionSourceError(
            f"{PROJECTION_QUALIFIER} must contain exactly {sorted(expected)}"
        )
    if result.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        raise RuntimeDecisionSourceError(
            "decision_intelligence_projection schema version is invalid"
        )
    result["use_case_ids"] = _nonempty_unique_text_list(
        result["use_case_ids"], field="decision_intelligence_projection.use_case_ids"
    )
    result["observations"] = _mapping_list(
        result["observations"], field="decision_intelligence_projection.observations"
    )
    result["evidence"] = _mapping_list(
        result["evidence"], field="decision_intelligence_projection.evidence"
    )
    return result
