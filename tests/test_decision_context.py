#!/usr/bin/env python3
"""Provider-free regression tests for the W14 decision-context projection."""

from __future__ import annotations

import unittest

from dual_pipeline.decision_context import project_decision_context
from dual_pipeline.strategic_evidence import build_evidence_packet
from dual_pipeline.temporal_observations import build_observation
from tests.decision_intelligence_support import authorization, monitor_for


def _observation(**overrides):
    values = {
        "scope_id": "workstream-rewire",
        "dimension": "delivery",
        "value": {"status": "on_track"},
        "observed_at": "2026-08-08T12:00:00Z",
        "recorded_at": "2026-08-08T12:05:00Z",
        "source_as_of": "2026-08-08T12:00:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "source_assertion_ids": ["assertion-status-1"],
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return build_observation(**values)


def _packet(**overrides):
    values = {
        "claim_id": "claim-ai-pdlc",
        "evidence_role": "supporting",
        "content": {"finding": "AI-PDLC opportunity"},
        "source_assertion_ids": ["assertion-claim-1"],
        "source_as_of": "2026-08-08T00:00:00Z",
        "recorded_at": "2026-08-08T00:05:00Z",
        "expires_at": "2026-09-08T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "review_state": "REVIEWED",
        "reviewed_by": "Amit Ayer",
        "reviewed_at": "2026-08-09T00:00:00Z",
        "falsification_tests": [{"test_id": "test-1", "question": "Does it replicate?"}],
    }
    values.update(overrides)
    return build_evidence_packet(**values)


def _context(**overrides):
    values = {
        "scope_id": "workstream-rewire",
        "audience": "internal_consulting",
        "as_of": "2026-08-10T00:00:00Z",
        "known_at": "2026-08-10T00:00:00Z",
        "authorization_context": authorization(),
        "reference_monitor": monitor_for(
            [
                "assertion-status-1",
                "assertion-status-2",
                "assertion-claim-1",
                "assertion-claim-2",
            ]
        ),
        "observations": [_observation()],
        "required_dimensions": ["delivery"],
        "evidence_packets": [
            _packet(),
            _packet(
                evidence_id="counter-1",
                evidence_role="counterevidence",
                source_assertion_ids=["assertion-claim-2"],
            ),
        ],
        "claim_ids": ["claim-ai-pdlc"],
    }
    values.update(overrides)
    return project_decision_context(**values)


class DecisionContextTests(unittest.TestCase):
    def test_current_status_and_counterevidence_are_candidate_ready(self) -> None:
        result = _context()
        self.assertEqual("CANDIDATE_READY_WITH_COUNTEREVIDENCE", result["state"])
        self.assertEqual("CANDIDATE_ONLY", result["recommendation_posture"])
        item_id = result["status_projections"][0]["observation_ids"][0]
        self.assertEqual(["assertion-status-1"], result["source_assertion_ids_by_item"][item_id])

    def test_support_only_evidence_remains_candidate_with_visible_gap(self) -> None:
        result = _context(evidence_packets=[_packet()])
        self.assertEqual("CANDIDATE_WITH_GAPS", result["state"])
        self.assertIn("counterevidence_not_found", result["gaps"])

    def test_missing_required_status_is_never_treated_as_current(self) -> None:
        result = _context(observations=[])
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertIn("observation_delivery_stale_or_unknown", result["gaps"])

    def test_conflicting_status_requires_human_review(self) -> None:
        result = _context(
            observations=[
                _observation(observation_id="left"),
                _observation(
                    observation_id="right",
                    value={"status": "at_risk"},
                    source_assertion_ids=["assertion-status-2"],
                ),
            ]
        )
        self.assertEqual("HITL_REQUIRED", result["state"])
        self.assertIn("observation_delivery_conflict", result["gaps"])

    def test_unreviewed_evidence_requires_human_review(self) -> None:
        result = _context(
            evidence_packets=[
                _packet(review_state="UNREVIEWED", reviewed_by=None, reviewed_at=None)
            ]
        )
        self.assertEqual("HITL_REQUIRED", result["state"])
        self.assertIn("unreviewed_or_conflicted_evidence", result["gaps"])

    def test_required_inputs_are_not_optional(self) -> None:
        with self.assertRaises(ValueError):
            _context(claim_ids=[])


if __name__ == "__main__":
    unittest.main(verbosity=2)
