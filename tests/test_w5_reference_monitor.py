"""Adversarial runtime tests for the W5.0 Decision Intelligence trust boundary."""

from __future__ import annotations

import json
import unittest

from dual_pipeline.daily_intelligence import project_change_brief
from dual_pipeline.decision_context import project_decision_context
from dual_pipeline.reference_monitor import (
    ReferenceMonitor,
    ReferenceMonitorError,
    SourceAssertionRegistry,
)
from dual_pipeline.strategic_evidence import (
    StrategicEvidenceError,
    build_evidence_packet,
    project_synthesis_workspace,
)
from dual_pipeline.temporal_observations import (
    TemporalObservationError,
    build_observation,
    project_observation_state,
)
from tests.decision_intelligence_support import authorization, source_assertion

AS_OF = "2026-08-10T00:00:00Z"
CLIENT = "client_approved_external"


def _monitor(*, captured_at: str = "2026-08-01T00:00:00Z") -> ReferenceMonitor:
    return ReferenceMonitor(
        SourceAssertionRegistry(
            [
                source_assertion(
                    "public-source-1",
                    classification="public",
                    audiences=[CLIENT],
                    captured_at=captured_at,
                ),
                source_assertion(
                    "private-source-1",
                    classification="restricted",
                    audiences=["partner_advisory_account_leadership"],
                    captured_at=captured_at,
                ),
            ]
        )
    )


def _client_auth():
    return authorization(audiences=[CLIENT], classification_clearance="restricted")


def _observation(
    source_assertion_id: str,
    *,
    value: object,
    classification: str,
    audiences: list[str],
    recorded_at: str = "2026-08-08T12:05:00Z",
    observation_id: str | None = None,
):
    return build_observation(
        observation_id=observation_id,
        scope_id="pilot-1",
        dimension="delivery",
        value=value,
        observed_at="2026-08-08T12:00:00Z",
        recorded_at=recorded_at,
        source_as_of="2026-08-01T00:00:00Z",
        stale_after="2026-08-20T00:00:00Z",
        source_assertion_ids=[source_assertion_id],
        classification=classification,
        audiences=audiences,
    )


def _packet(
    source_assertion_id: str,
    *,
    classification: str,
    audiences: list[str],
    recorded_at: str = "2026-08-08T00:05:00Z",
):
    return build_evidence_packet(
        claim_id="claim-1",
        evidence_role="supporting",
        content={"detail": "private budget review"},
        source_assertion_ids=[source_assertion_id],
        source_as_of="2026-08-01T00:00:00Z",
        recorded_at=recorded_at,
        expires_at="2026-09-01T00:00:00Z",
        classification=classification,
        audiences=audiences,
        review_state="REVIEWED",
        reviewed_by="reviewer",
        reviewed_at="2026-08-09T00:00:00Z",
        falsification_tests=[{"test_id": "f-1", "question": "Can it be disproved?"}],
    )


class W5ReferenceMonitorTests(unittest.TestCase):
    def test_client_decision_context_never_contains_private_status_or_source_id(self) -> None:
        monitor = _monitor()
        public = _observation(
            "public-source-1",
            value={"status": "ON_TRACK"},
            classification="public",
            audiences=[CLIENT],
            observation_id="public-observation",
        )
        private = _observation(
            "private-source-1",
            value={"status": "BLOCKED", "detail": "private budget review"},
            classification="restricted",
            audiences=["partner_advisory_account_leadership"],
            observation_id="private-observation",
        )
        public_packet = _packet(
            "public-source-1", classification="public", audiences=[CLIENT]
        )
        result = project_decision_context(
            scope_id="pilot-1",
            audience=CLIENT,
            as_of=AS_OF,
            known_at=AS_OF,
            authorization_context=_client_auth(),
            reference_monitor=monitor,
            observations=[public, private],
            required_dimensions=["delivery"],
            evidence_packets=[public_packet],
            claim_ids=["claim-1"],
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertEqual({"status": "ON_TRACK"}, result["status_projections"][0]["value"])
        self.assertNotIn("private budget review", rendered)
        self.assertNotIn("private-source-1", rendered)
        self.assertNotIn("private-observation", rendered)

    def test_client_daily_brief_filters_before_findings_counts_and_source_lists(self) -> None:
        monitor = _monitor()
        public = _observation(
            "public-source-1",
            value={"status": "ON_TRACK"},
            classification="public",
            audiences=[CLIENT],
            observation_id="public-observation",
        )
        private = _observation(
            "private-source-1",
            value={"status": "BLOCKED", "detail": "private budget review"},
            classification="restricted",
            audiences=["partner_advisory_account_leadership"],
            observation_id="private-observation",
        )
        result = project_change_brief(
            [public, private],
            previous_as_of="2026-08-05T00:00:00Z",
            as_of=AS_OF,
            audience=CLIENT,
            known_at=AS_OF,
            authorization_context=_client_auth(),
            reference_monitor=monitor,
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("private", rendered)
        self.assertEqual(["public-source-1"], result["artifact"]["source_assertion_ids"])

    def test_label_tampering_fails_closed_before_client_projection(self) -> None:
        monitor = _monitor()
        tampered = _observation(
            "private-source-1",
            value={"status": "BLOCKED"},
            classification="public",
            audiences=[CLIENT],
        )
        with self.assertRaises(TemporalObservationError):
            project_observation_state(
                [tampered],
                scope_id="pilot-1",
                dimension="delivery",
                as_of=AS_OF,
                known_at=AS_OF,
                audience=CLIENT,
                authorization_context=_client_auth(),
                reference_monitor=monitor,
            )

    def test_restricted_evidence_cannot_be_relabelled_for_client(self) -> None:
        monitor = _monitor()
        tampered = _packet(
            "private-source-1", classification="restricted", audiences=[CLIENT]
        )
        with self.assertRaises(StrategicEvidenceError):
            project_synthesis_workspace(
                [tampered],
                claim_id="claim-1",
                audience=CLIENT,
                as_of=AS_OF,
                known_at=AS_OF,
                authorization_context=_client_auth(),
                reference_monitor=monitor,
            )

    def test_future_recorded_observation_is_absent_from_historical_projection(self) -> None:
        monitor = _monitor()
        future_known = _observation(
            "public-source-1",
            value={"status": "future-known status"},
            classification="public",
            audiences=[CLIENT],
            recorded_at="2026-08-20T12:00:00Z",
            observation_id="future-known-observation",
        )
        result = project_observation_state(
            [future_known],
            scope_id="pilot-1",
            dimension="delivery",
            as_of=AS_OF,
            known_at=AS_OF,
            audience=CLIENT,
            authorization_context=_client_auth(),
            reference_monitor=monitor,
        )
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertNotIn("future-known-observation", json.dumps(result))
        self.assertNotIn("future-known status", json.dumps(result))

    def test_future_capture_and_revoked_source_are_unavailable(self) -> None:
        future_monitor = _monitor(captured_at="2026-08-20T00:00:00Z")
        future_source = _observation(
            "public-source-1",
            value={"status": "future source"},
            classification="public",
            audiences=[CLIENT],
        )
        result = project_observation_state(
            [future_source],
            scope_id="pilot-1",
            dimension="delivery",
            as_of=AS_OF,
            known_at=AS_OF,
            audience=CLIENT,
            authorization_context=_client_auth(),
            reference_monitor=future_monitor,
        )
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])

        revoked_monitor = ReferenceMonitor(
            SourceAssertionRegistry(
                [
                    {
                        **source_assertion(
                            "public-source-1",
                            classification="public",
                            audiences=[CLIENT],
                        ),
                        "revoked_at": "2026-08-09T00:00:00Z",
                    }
                ]
            )
        )
        revoked = project_observation_state(
            [future_source],
            scope_id="pilot-1",
            dimension="delivery",
            as_of=AS_OF,
            known_at=AS_OF,
            audience=CLIENT,
            authorization_context=_client_auth(),
            reference_monitor=revoked_monitor,
        )
        self.assertEqual("STALE_OR_UNKNOWN", revoked["state"])

    def test_retrieval_adapter_filters_private_sidecar_and_multihop_content(self) -> None:
        monitor = _monitor()
        records = [
            {
                "record_id": "public-record",
                "source_assertion_ids": ["public-source-1"],
                "classification": "public",
                "audiences": [CLIENT],
                "retrieval_text": "safe client delivery evidence",
            },
            {
                "record_id": "private-record",
                "source_assertion_ids": ["private-source-1"],
                "classification": "restricted",
                "audiences": ["partner_advisory_account_leadership"],
                "retrieval_text": "private budget review",
                "multi_hop_source_ids": ["private-source-1"],
            },
        ]
        visible = monitor.filter_retrieval_records(
            records,
            audience=CLIENT,
            authorization_context=_client_auth(),
            known_at=AS_OF,
        )
        self.assertEqual(["public-record"], [item["record_id"] for item in visible])
        self.assertNotIn("private", json.dumps(visible, sort_keys=True))

    def test_unknown_lineage_and_missing_principal_grant_fail_closed(self) -> None:
        monitor = _monitor()
        with self.assertRaises(ReferenceMonitorError):
            monitor.filter_retrieval_records(
                [
                    {
                        "record_id": "unknown",
                        "source_assertion_ids": ["unknown-source"],
                        "classification": "public",
                        "audiences": [CLIENT],
                    }
                ],
                audience=CLIENT,
                authorization_context=_client_auth(),
                known_at=AS_OF,
            )
        with self.assertRaises(ReferenceMonitorError):
            monitor.filter_retrieval_records(
                [
                    {
                        "record_id": "public",
                        "source_assertion_ids": ["public-source-1"],
                        "classification": "public",
                        "audiences": [CLIENT],
                    }
                ],
                audience=CLIENT,
                authorization_context=authorization(audiences=["internal_consulting"]),
                known_at=AS_OF,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
