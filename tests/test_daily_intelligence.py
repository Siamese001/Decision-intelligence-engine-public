"""Provider-free regression tests for W4 daily intelligence projections."""

from __future__ import annotations

import copy
import unittest

from dual_pipeline.daily_intelligence import (
    DailyIntelligenceError,
    project_change_brief,
    project_daily_intelligence,
    project_decision_inbox,
    project_dependency_warnings,
    project_meeting_brief,
    project_readiness_gaps,
    validate_daily_artifact,
)
from dual_pipeline.temporal_observations import build_observation
from tests.decision_intelligence_support import authorization, monitor_for

AS_OF = "2026-08-09T00:00:00Z"
PREVIOUS_AS_OF = "2026-08-05T00:00:00Z"


def _observation(**overrides):
    values = {
        "scope_id": "initiative-a",
        "dimension": "delivery",
        "value": {"status": "on_track"},
        "observed_at": "2026-08-08T12:00:00Z",
        "recorded_at": "2026-08-08T12:05:00Z",
        "source_as_of": "2026-08-08T12:00:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "source_assertion_ids": ["assertion-current"],
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return build_observation(**values)


def _decision(**overrides):
    values = {
        "decision_id": "decision-a",
        "scope_id": "initiative-a",
        "title": "Approve pilot scope",
        "decision_state": "OPEN",
        "owner": "Delivery lead",
        "consequence_of_delay": "Delays the pilot start",
        "source_assertion_ids": ["assertion-decision"],
        "source_as_of": "2026-08-08T00:00:00Z",
        "recorded_at": "2026-08-08T00:05:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return values


def _dependency(**overrides):
    values = {
        "dependency_id": "dependency-a",
        "scope_id": "initiative-a",
        "depends_on_scope_id": "initiative-b",
        "title": "Needs data-platform access",
        "dependency_state": "OPEN",
        "impact": "Blocks pilot integration",
        "source_assertion_ids": ["assertion-dependency"],
        "source_as_of": "2026-08-08T00:00:00Z",
        "recorded_at": "2026-08-08T00:05:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return values


def _guard():
    return {
        "known_at": AS_OF,
        "authorization_context": authorization(),
        "reference_monitor": monitor_for(
            [
                "assertion-current",
                "assertion-older",
                "assertion-expired",
                "assertion-left",
                "assertion-right",
                "assertion-decision",
                "assertion-dependency",
            ]
        ),
    }


class DailyIntelligenceTests(unittest.TestCase):
    def test_change_brief_is_candidate_only_and_source_bound(self) -> None:
        older = _observation(
            value={"status": "at_risk"},
            observed_at="2026-08-01T12:00:00Z",
            recorded_at="2026-08-01T12:05:00Z",
            source_as_of="2026-08-01T12:00:00Z",
            stale_after="2026-08-20T00:00:00Z",
            source_assertion_ids=["assertion-older"],
            superseded_at="2026-08-08T12:00:00Z",
            superseded_by="obs-current",
        )
        result = project_change_brief(
            [older, _observation()],
            previous_as_of=PREVIOUS_AS_OF,
            as_of=AS_OF,
            audience="internal_consulting",
            **_guard(),
        )
        artifact = result["artifact"]
        self.assertEqual("READY_FOR_REVIEW", result["state"])
        self.assertEqual("DERIVED_CANDIDATE_ONLY", artifact["authority"])
        self.assertFalse(artifact["graph_mutation_authorized"])
        self.assertEqual({"assertion-current", "assertion-older"}, set(artifact["source_assertion_ids"]))
        self.assertEqual(1, len(artifact["findings"]))

    def test_readiness_gap_is_stale_not_healthy(self) -> None:
        expired = _observation(
            dimension="readiness",
            value={"status": "ready"},
            stale_after="2026-08-08T23:00:00Z",
            source_assertion_ids=["assertion-expired"],
        )
        result = project_readiness_gaps(
            [expired],
            scope_ids=["initiative-a"],
            required_dimensions=["readiness"],
            audience="internal_consulting",
            as_of=AS_OF,
            **_guard(),
        )
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertEqual("STALE_OR_UNKNOWN", result["artifact"]["state"])
        self.assertIn("missing_or_stale_readiness_evidence", result["gaps"])

    def test_missing_only_readiness_evidence_abstains(self) -> None:
        result = project_readiness_gaps(
            [],
            scope_ids=["initiative-a"],
            required_dimensions=["readiness"],
            audience="internal_consulting",
            as_of=AS_OF,
            **_guard(),
        )
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertIsNone(result["artifact"])
        self.assertIn("missing_required_observations", result["gaps"])

    def test_conflicting_readiness_requires_hitl(self) -> None:
        left = _observation(
            dimension="readiness",
            observation_id="readiness-left",
            value={"status": "ready"},
            source_assertion_ids=["assertion-left"],
        )
        right = _observation(
            dimension="readiness",
            observation_id="readiness-right",
            value={"status": "not_ready"},
            source_assertion_ids=["assertion-right"],
        )
        result = project_readiness_gaps(
            [left, right],
            scope_ids=["initiative-a"],
            required_dimensions=["readiness"],
            audience="internal_consulting",
            as_of=AS_OF,
            **_guard(),
        )
        self.assertEqual("HITL_REQUIRED", result["state"])
        self.assertEqual("HITL_REQUIRED", result["artifact"]["review_state"])
        self.assertIn("conflicting_readiness_evidence", result["gaps"])

    def test_unowned_or_blocked_decision_requires_hitl(self) -> None:
        result = project_decision_inbox(
            [_decision(owner=None, decision_state="BLOCKED")],
            audience="internal_consulting",
            as_of=AS_OF,
            **_guard(),
        )
        self.assertEqual("HITL_REQUIRED", result["state"])
        artifact = result["artifact"]
        self.assertEqual("decision_inbox", artifact["artifact_type"])
        self.assertEqual(["assertion-decision"], artifact["source_assertion_ids"])
        self.assertIn("unowned_or_blocked_decision", result["gaps"])

    def test_dependency_warning_preserves_cross_scope_provenance(self) -> None:
        result = project_dependency_warnings(
            [_dependency()], audience="internal_consulting", as_of=AS_OF, **_guard()
        )
        artifact = result["artifact"]
        self.assertEqual("READY_FOR_REVIEW", result["state"])
        self.assertEqual({"initiative-a", "initiative-b"}, set(artifact["scope_ids"]))
        self.assertEqual(["assertion-dependency"], artifact["source_assertion_ids"])

    def test_mixed_client_and_restricted_input_fails_closed(self) -> None:
        mixed = _observation(audiences=["nee-engineering", "internal_working_team"])
        with self.assertRaises(DailyIntelligenceError):
            project_change_brief(
                [mixed],
                previous_as_of=PREVIOUS_AS_OF,
                as_of=AS_OF,
                audience="nee-engineering",
                **_guard(),
            )

    def test_meeting_brief_stays_candidate_only(self) -> None:
        change = project_change_brief(
            [_observation()],
            previous_as_of=PREVIOUS_AS_OF,
            as_of=AS_OF,
            audience="internal_consulting",
            **_guard(),
        )
        meeting = project_meeting_brief(
            [change], audience="internal_consulting", as_of=AS_OF, **_guard()
        )
        artifact = meeting["artifact"]
        self.assertEqual("meeting_brief", artifact["artifact_type"])
        self.assertEqual("DERIVED_CANDIDATE_ONLY", artifact["authority"])
        self.assertFalse(artifact["graph_mutation_authorized"])
        self.assertEqual("UNREVIEWED", artifact["review_state"])

    def test_full_projection_does_not_mutate_input(self) -> None:
        observations = [_observation()]
        decisions = [_decision()]
        dependencies = [_dependency()]
        before = copy.deepcopy((observations, decisions, dependencies))
        result = project_daily_intelligence(
            observations=observations,
            decisions=decisions,
            dependencies=dependencies,
            scope_ids=["initiative-a"],
            required_dimensions=["delivery"],
            audience="internal_consulting",
            previous_as_of=PREVIOUS_AS_OF,
            as_of=AS_OF,
            **_guard(),
        )
        self.assertEqual(
            {"change_brief", "decision_inbox", "readiness_gap", "dependency_warning", "meeting_brief"},
            set(result),
        )
        self.assertEqual(before, (observations, decisions, dependencies))
        self.assertEqual("DERIVED_CANDIDATE_ONLY", result["meeting_brief"]["artifact"]["authority"])

    def test_artifact_rejects_graph_mutation_authority(self) -> None:
        change = project_change_brief(
            [_observation()],
            previous_as_of=PREVIOUS_AS_OF,
            as_of=AS_OF,
            audience="internal_consulting",
            **_guard(),
        )["artifact"]
        bad = dict(change)
        bad["graph_mutation_authorized"] = True
        with self.assertRaises(DailyIntelligenceError):
            validate_daily_artifact(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
