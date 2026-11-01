"""Visibility filtering through reference monitor for daily intelligence."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..authorization import AuthorizationContext
from ..reference_monitor import ReferenceMonitor, ReferenceMonitorError
from ..temporal_observations import TemporalObservationError, validate_observation
from .constants import DailyIntelligenceError, _validate_projection_audience


def _visible_observations(
    observations: Iterable[Mapping[str, Any]],
    *,
    audience: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> list[dict[str, Any]]:
    _validate_projection_audience(audience)
    if not isinstance(reference_monitor, ReferenceMonitor):
        raise DailyIntelligenceError("a ReferenceMonitor is required for projection")
    try:
        return [
            item
            for item in (
                validate_observation(raw, reference_monitor=reference_monitor)
                for raw in observations
            )
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
    except (ReferenceMonitorError, TemporalObservationError) as exc:
        raise DailyIntelligenceError(str(exc)) from exc


def _visible_records(
    records: Iterable[Mapping[str, Any]],
    *,
    audience: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
    validator: Any,
) -> list[dict[str, Any]]:
    _validate_projection_audience(audience)
    if not isinstance(reference_monitor, ReferenceMonitor):
        raise DailyIntelligenceError("a ReferenceMonitor is required for projection")
    try:
        return [
            item
            for item in (
                validator(raw, reference_monitor=reference_monitor) for raw in records
            )
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
        raise DailyIntelligenceError(str(exc)) from exc
