"""Fail-closed temporal observation validation and projection for W3."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Iterable, Mapping

from ._validators import format_time_text, parse_iso_time
from .authorization import AuthorizationContext
from .core import digest, stable_id
from .exceptions import DualPipelineValueError
from .reference_monitor import ReferenceMonitor, ReferenceMonitorError

W3_OBSERVATION_SCHEMA_VERSION = "1.0"
OBSERVATION_DIMENSIONS = frozenset(
    {
        "delivery",
        "schedule",
        "scope",
        "capacity",
        "readiness",
        "adoption",
        "value",
        "dependency",
        "decision",
    }
)
OBSERVATION_STATES = frozenset(
    {"CURRENT", "STALE_OR_UNKNOWN", "CONFLICT", "FUTURE", "SUPERSEDED"}
)
REQUIRED_FIELDS = frozenset(
    {
        "observation_id",
        "scope_id",
        "dimension",
        "value",
        "observed_at",
        "recorded_at",
        "source_as_of",
        "stale_after",
        "valid_from",
        "valid_to",
        "superseded_at",
        "superseded_by",
        "source_assertion_ids",
        "classification",
        "audiences",
    }
)


class TemporalObservationError(DualPipelineValueError):
    """Raised when an observation violates the W3 contract."""


def _parse_time(value: object, *, field: str, allow_none: bool = False) -> datetime | None:
    return parse_iso_time(value, field=field, error_type=TemporalObservationError, allow_none=allow_none)


def _time_text(value: datetime | None) -> str | None:
    return format_time_text(value)


def validate_observation(
    value: Mapping[str, Any], *, reference_monitor: ReferenceMonitor | None = None
) -> dict[str, Any]:
    """Validate and normalize one immutable source-bound observation."""

    if not isinstance(value, Mapping) or set(value) != REQUIRED_FIELDS:
        raise TemporalObservationError(
            f"observation must contain exactly {sorted(REQUIRED_FIELDS)}"
        )
    result = copy.deepcopy(dict(value))
    for field in ("observation_id", "scope_id", "classification"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise TemporalObservationError(f"{field} must be nonblank text")
        result[field] = result[field].strip()
    if result["dimension"] not in OBSERVATION_DIMENSIONS:
        raise TemporalObservationError("dimension is outside the W3 vocabulary")
    if result["value"] is None:
        raise TemporalObservationError("value is required; use an explicit unknown value")
    times = {
        field: _parse_time(
            result[field], field=field, allow_none=field in {"valid_to", "superseded_at"}
        )
        for field in (
            "observed_at",
            "recorded_at",
            "source_as_of",
            "stale_after",
            "valid_from",
            "valid_to",
            "superseded_at",
        )
    }
    if not (
        times["valid_from"] <= times["observed_at"] <= times["stale_after"]
        and times["recorded_at"] >= times["observed_at"]
        and times["source_as_of"] <= times["recorded_at"]
    ):
        raise TemporalObservationError("observation temporal ordering is invalid")
    if times["valid_to"] is not None and times["valid_to"] < times["valid_from"]:
        raise TemporalObservationError("valid_to must be >= valid_from")
    if result["superseded_by"] is not None:
        if not isinstance(result["superseded_by"], str) or not result["superseded_by"].strip():
            raise TemporalObservationError("superseded_by must be text or null")
        if times["superseded_at"] is None:
            raise TemporalObservationError("superseded_at is required with superseded_by")
    elif times["superseded_at"] is not None:
        raise TemporalObservationError("superseded_by is required with superseded_at")
    source_ids = result["source_assertion_ids"]
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or any(not isinstance(item, str) or not item.strip() for item in source_ids)
        or len(set(source_ids)) != len(source_ids)
    ):
        raise TemporalObservationError("source_assertion_ids must be a unique nonempty list")
    audiences = result["audiences"]
    if (
        not isinstance(audiences, list)
        or not audiences
        or any(not isinstance(item, str) or not item.strip() for item in audiences)
        or len(set(audiences)) != len(audiences)
    ):
        raise TemporalObservationError("audiences must be a unique nonempty list")
    source_ids = [item.strip() for item in source_ids]
    audiences = [item.strip() for item in audiences]
    for field, parsed in times.items():
        result[field] = _time_text(parsed)
    result["source_assertion_ids"] = sorted(source_ids)
    result["audiences"] = sorted(audiences)
    if reference_monitor is not None:
        try:
            reference_monitor.verify_derived_labels(
                source_assertion_ids=result["source_assertion_ids"],
                classification=result["classification"],
                audiences=result["audiences"],
            )
        except ReferenceMonitorError as exc:
            raise TemporalObservationError(str(exc)) from exc
    return result


def build_observation(
    *,
    scope_id: str,
    dimension: str,
    value: Any,
    observed_at: str,
    recorded_at: str,
    source_as_of: str,
    stale_after: str,
    source_assertion_ids: Iterable[str],
    classification: str,
    audiences: Iterable[str],
    valid_from: str | None = None,
    valid_to: str | None = None,
    superseded_at: str | None = None,
    superseded_by: str | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    if valid_from is None:
        valid_from = observed_at
    source_ids = list(source_assertion_ids)
    raw = {
        "observation_id": observation_id
        or stable_id("observation", scope_id, dimension, observed_at, source_ids),
        "scope_id": scope_id,
        "dimension": dimension,
        "value": value,
        "observed_at": observed_at,
        "recorded_at": recorded_at,
        "source_as_of": source_as_of,
        "stale_after": stale_after,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "superseded_at": superseded_at,
        "superseded_by": superseded_by,
        "source_assertion_ids": source_ids,
        "classification": classification,
        "audiences": list(audiences),
    }
    return validate_observation(raw)


def observation_state(observation: Mapping[str, Any], *, as_of: str) -> str:
    item = validate_observation(observation)
    query_time = _parse_time(as_of, field="as_of")
    assert query_time is not None
    valid_from = _parse_time(item["valid_from"], field="valid_from")
    valid_to = _parse_time(item["valid_to"], field="valid_to", allow_none=True)
    stale_after = _parse_time(item["stale_after"], field="stale_after")
    superseded_at = _parse_time(
        item["superseded_at"], field="superseded_at", allow_none=True
    )
    if superseded_at is not None and superseded_at <= query_time:
        return "SUPERSEDED"
    if valid_from is not None and query_time < valid_from:
        return "FUTURE"
    if (valid_to is not None and query_time > valid_to) or (
        stale_after is not None and query_time > stale_after
    ):
        return "STALE_OR_UNKNOWN"
    return "CURRENT"


def project_observation_state(
    observations: Iterable[Mapping[str, Any]],
    *,
    scope_id: str,
    dimension: str,
    as_of: str,
    known_at: str,
    audience: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    """Project one scope/dimension after a fail-closed bitemporal read guard."""

    if not isinstance(reference_monitor, ReferenceMonitor):
        raise TemporalObservationError("a ReferenceMonitor is required for projection")
    try:
        validated = [
            validate_observation(item, reference_monitor=reference_monitor)
            for item in observations
        ]
        visible = [
            item
            for item in validated
            if reference_monitor.visible(
                source_assertion_ids=item["source_assertion_ids"],
                classification=item["classification"],
                audiences=item["audiences"],
                audience=audience,
                authorization_context=authorization_context,
                known_at=known_at,
                recorded_at=item["recorded_at"],
            )
        ]
    except ReferenceMonitorError as exc:
        raise TemporalObservationError(str(exc)) from exc
    scoped = [
        item
        for item in visible
        if item["scope_id"] == scope_id and item["dimension"] == dimension
    ]
    states = [(item, observation_state(item, as_of=as_of)) for item in scoped]
    current = [item for item, state in states if state == "CURRENT"]
    if not current:
        stale_or_unknown = [
            item["observation_id"]
            for item, state in states
            if state in {"STALE_OR_UNKNOWN", "SUPERSEDED"}
        ]
        future = [item["observation_id"] for item, state in states if state == "FUTURE"]
        return {
            "scope_id": scope_id,
            "dimension": dimension,
            "state": "STALE_OR_UNKNOWN",
            "value": None,
            "observation_ids": sorted(stale_or_unknown),
            "future_observation_ids": sorted(future),
            "as_of": as_of,
            "known_at": known_at,
            "audience": audience,
        }
    value_digests = {digest(item["value"]) for item in current}
    if len(value_digests) > 1:
        return {
            "scope_id": scope_id,
            "dimension": dimension,
            "state": "CONFLICT",
            "value": None,
            "observation_ids": sorted(item["observation_id"] for item in current),
            "values": [
                item["value"]
                for item in sorted(current, key=lambda item: item["observation_id"])
            ],
            "as_of": as_of,
            "known_at": known_at,
            "audience": audience,
        }
    latest_observed_at = max(item["observed_at"] for item in current)
    latest = [item for item in current if item["observed_at"] == latest_observed_at]
    selected = max(latest, key=lambda item: (item["recorded_at"], item["observation_id"]))
    return {
        "scope_id": scope_id,
        "dimension": dimension,
        "state": "CURRENT",
        "value": selected["value"],
        "observation_ids": [selected["observation_id"]],
        "observed_at": selected["observed_at"],
        "stale_after": selected["stale_after"],
        "as_of": as_of,
        "known_at": known_at,
        "audience": audience,
    }
