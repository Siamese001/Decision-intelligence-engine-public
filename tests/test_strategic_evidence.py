#!/usr/bin/env python3
"""Provider-free regression tests for the W6 strategic evidence workspace."""

from __future__ import annotations

import unittest

from dual_pipeline.strategic_evidence import (
    StrategicEvidenceError,
    build_evidence_packet,
    project_synthesis_workspace,
    validate_evidence_packet,
)
from tests.decision_intelligence_support import authorization, monitor_for, source_assertion


def _packet(**overrides):
    values = {
        "claim_id": "claim-ai-pdlc",
        "evidence_role": "supporting",
        "content": {"finding": "pilot reduced rework"},
        "source_assertion_ids": ["assertion-1"],
        "source_as_of": "2026-08-01T00:00:00Z",
        "recorded_at": "2026-08-01T00:05:00Z",
        "expires_at": "2026-09-01T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "review_state": "REVIEWED",
        "reviewed_by": "Amit Ayer",
        "reviewed_at": "2026-08-02T00:00:00Z",
        "falsification_tests": [{"test_id": "test-1", "question": "Does the result replicate across two teams?"}],
    }
    values.update(overrides)
    return build_evidence_packet(**values)


def _project(packets, *, audience="internal_consulting"):
    rows = list(packets)
    source_ids = [
        source_id for item in rows for source_id in item["source_assertion_ids"]
    ]
    return project_synthesis_workspace(
        rows,
        claim_id="claim-ai-pdlc",
        audience=audience,
        as_of="2026-08-09T00:00:00Z",
        known_at="2026-08-09T00:00:00Z",
        authorization_context=authorization(audiences=[audience]),
        reference_monitor=monitor_for(source_ids),
    )


class StrategicEvidenceTests(unittest.TestCase):
    def test_reviewed_support_and_counterevidence_are_ready(self) -> None:
        support = _packet()
        counter = _packet(evidence_id="counter-1", evidence_role="counterevidence", content={"finding": "benefit did not replicate"}, source_assertion_ids=["assertion-2"])
        result = _project([support, counter])
        self.assertEqual("READY_WITH_COUNTEREVIDENCE", result["state"])
        self.assertEqual("CANDIDATE_ONLY", result["recommendation_posture"])
        self.assertEqual(["counter-1"], result["counterevidence_ids"])

    def test_support_only_exposes_counterevidence_gap(self) -> None:
        result = _project([_packet()])
        self.assertEqual("READY_SUPPORT_ONLY", result["state"])
        self.assertIn("counterevidence_not_found", result["gaps"])

    def test_expired_evidence_is_not_active(self) -> None:
        packet = _packet(expires_at="2026-08-08T00:00:00Z")
        result = _project([packet])
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertIn(packet["evidence_id"], result["expired_evidence_ids"])

    def test_unreviewed_evidence_requires_hitl(self) -> None:
        packet = _packet(review_state="UNREVIEWED", reviewed_by=None, reviewed_at=None)
        result = _project([packet])
        self.assertEqual("HITL_REQUIRED", result["state"])
        self.assertIn("unreviewed_or_conflicted_evidence", result["gaps"])

    def test_conflicted_evidence_requires_hitl(self) -> None:
        packet = _packet(review_state="CONFLICTED", reviewed_by=None, reviewed_at=None)
        result = _project([packet])
        self.assertEqual("HITL_REQUIRED", result["state"])

    def test_missing_falsification_test_requires_hitl(self) -> None:
        packet = _packet(falsification_tests=[])
        result = _project([packet])
        self.assertEqual("HITL_REQUIRED", result["state"])
        self.assertIn("missing_falsification_test", result["gaps"])

    def test_mixed_client_and_restricted_audience_is_blocked(self) -> None:
        packet = _packet(audiences=["client_approved_external", "internal_working_team"])
        with self.assertRaises(StrategicEvidenceError):
            _project([packet], audience="client_approved_external")

    def test_missing_supporting_evidence_is_unknown(self) -> None:
        context = _packet(evidence_role="context", evidence_id="context-1")
        result = _project([context])
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertIn("missing_supporting_evidence", result["gaps"])

    def test_reviewed_state_requires_reviewer_metadata(self) -> None:
        with self.assertRaises(StrategicEvidenceError):
            _packet(reviewed_by=None, reviewed_at=None)

    def test_projection_derives_exact_immutable_citation_bindings(self) -> None:
        result = _project([_packet()])
        self.assertEqual(1, len(result["citation_binding_digests"]))
        self.assertEqual(["assertion-1"], result["supporting_source_assertion_ids"])

    def test_unknown_lineage_fails_before_projection(self) -> None:
        packet = _packet(source_assertion_ids=["unknown-source"])
        with self.assertRaises(StrategicEvidenceError):
            project_synthesis_workspace(
                [packet],
                claim_id="claim-ai-pdlc",
                audience="internal_consulting",
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
                authorization_context=authorization(),
                reference_monitor=monitor_for(["assertion-1"]),
            )

    def test_tampered_source_digest_and_locator_fail_closed(self) -> None:
        monitor = monitor_for(["assertion-1"])
        packet = _packet()
        packet["citation_bindings"] = [
            {
                "source_assertion_id": "assertion-1",
                "snapshot_digest": "0" * 64,
                "content_digest": source_assertion("assertion-1")["content_digest"],
                "locator": source_assertion("assertion-1")["locator"],
                "claim_digest": "0" * 64,
            }
        ]
        with self.assertRaises(StrategicEvidenceError):
            validate_evidence_packet(packet, reference_monitor=monitor)

    def test_claim_content_digest_mismatch_fails_closed(self) -> None:
        monitor = monitor_for(["assertion-1"])
        packet = _packet()
        packet["citation_bindings"] = [
            {
                "source_assertion_id": "assertion-1",
                "snapshot_digest": source_assertion("assertion-1")["snapshot_digest"],
                "content_digest": source_assertion("assertion-1")["content_digest"],
                "locator": source_assertion("assertion-1")["locator"],
                "claim_digest": "f" * 64,
            }
        ]
        with self.assertRaises(StrategicEvidenceError):
            validate_evidence_packet(packet, reference_monitor=monitor)


if __name__ == "__main__":
    unittest.main(verbosity=2)
