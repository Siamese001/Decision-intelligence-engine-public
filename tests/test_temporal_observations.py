#!/usr/bin/env python3
"""Provider-free regression tests for the W3 temporal observation model."""

from __future__ import annotations

import unittest

from dual_pipeline.temporal_observations import (
    TemporalObservationError,
    build_observation,
    observation_state,
    project_observation_state,
    validate_observation,
)
from tests.decision_intelligence_support import authorization, monitor_for


def _observation(**overrides):
    values = {
        "scope_id": "project-1",
        "dimension": "delivery",
        "value": {"status": "on_track"},
        "observed_at": "2026-08-08T12:00:00Z",
        "recorded_at": "2026-08-08T12:05:00Z",
        "source_as_of": "2026-08-08T12:00:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "source_assertion_ids": ["assertion-1"],
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return build_observation(**values)


def _project(observations, *, as_of="2026-08-09T00:00:00Z"):
    rows = list(observations)
    source_ids = [
        source_id for item in rows for source_id in item["source_assertion_ids"]
    ]
    return project_observation_state(
        rows,
        scope_id="project-1",
        dimension="delivery",
        as_of=as_of,
        known_at="2026-08-09T00:00:00Z",
        audience="internal_consulting",
        authorization_context=authorization(),
        reference_monitor=monitor_for(source_ids),
    )


class TemporalObservationTests(unittest.TestCase):
    def test_current_observation_is_normalized_and_source_bound(self) -> None:
        item = _observation()
        self.assertEqual("CURRENT", observation_state(item, as_of="2026-08-09T00:00:00Z"))
        self.assertEqual(["assertion-1"], item["source_assertion_ids"])
        self.assertEqual("2026-08-08T12:00:00Z", item["observed_at"])

    def test_history_selects_latest_observation(self) -> None:
        older = _observation(value={"status": "at_risk"}, observed_at="2026-08-01T12:00:00Z",
                             recorded_at="2026-08-01T12:05:00Z", source_as_of="2026-08-01T12:00:00Z",
                             stale_after="2026-08-10T00:00:00Z", source_assertion_ids=["assertion-0"],
                             superseded_at="2026-08-08T12:00:00Z", superseded_by="obs-current")
        newer = _observation()
        result = _project([older, newer])
        self.assertEqual("CURRENT", result["state"])
        self.assertEqual({"status": "on_track"}, result["value"])
        self.assertEqual([newer["observation_id"]], result["observation_ids"])

    def test_expired_observation_is_stale_or_unknown(self) -> None:
        item = _observation(stale_after="2026-08-08T23:59:00Z")
        self.assertEqual("STALE_OR_UNKNOWN", observation_state(item, as_of="2026-08-09T00:00:00Z"))
        result = _project([item])
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertIsNone(result["value"])

    def test_missing_observation_is_explicitly_unknown(self) -> None:
        result = _project([])
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertIsNone(result["value"])

    def test_same_time_different_values_are_conflict(self) -> None:
        left = _observation(observation_id="obs-left")
        right = _observation(observation_id="obs-right", value={"status": "at_risk"},
                             source_assertion_ids=["assertion-2"])
        result = _project([left, right])
        self.assertEqual("CONFLICT", result["state"])
        self.assertEqual(["obs-left", "obs-right"], result["observation_ids"])

    def test_superseded_observation_is_never_current(self) -> None:
        item = _observation(superseded_at="2026-08-08T18:00:00Z", superseded_by="obs-new")
        self.assertEqual("SUPERSEDED", observation_state(item, as_of="2026-08-09T00:00:00Z"))
        result = _project([item])
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertEqual([item["observation_id"]], result["observation_ids"])

    def test_future_observation_is_exposed_without_fabricating_current_value(self) -> None:
        item = _observation(observed_at="2026-08-15T12:00:00Z", valid_from="2026-08-15T12:00:00Z",
                            recorded_at="2026-08-15T12:05:00Z", source_as_of="2026-08-15T12:00:00Z",
                            stale_after="2026-08-30T00:00:00Z")
        self.assertEqual("FUTURE", observation_state(item, as_of="2026-08-09T00:00:00Z"))
        result = _project([item])
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertEqual([], result["future_observation_ids"])

    def test_invalid_temporal_order_is_rejected(self) -> None:
        with self.assertRaises(TemporalObservationError):
            _observation(recorded_at="2026-08-08T11:00:00Z")

    def test_validation_does_not_mutate_input(self) -> None:
        raw = _observation(source_assertion_ids=["b", "a"], audiences=["z", "a"])
        copy = dict(raw)
        copy["source_assertion_ids"] = ["b", "a"]
        copy["audiences"] = ["z", "a"]
        validated = validate_observation(copy)
        self.assertEqual(["b", "a"], copy["source_assertion_ids"])
        self.assertEqual(["a", "b"], validated["source_assertion_ids"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
