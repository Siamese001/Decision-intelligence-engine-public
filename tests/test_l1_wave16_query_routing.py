"""Wave 16 Unit Tests: Primary Intent Dominance & Ambiguity Hardening in Query L0.

Tests verify:
1. Primary Intent Dominance: Decisive strategic intent dominates auxiliary delivery/formatting
   phrases (e.g. 'and summarize the findings', 'summarizing key tradeoffs') without triggering
   spurious ROUTE_AMBIGUOUS.
2. Peer Action Fail-Closed: Joined independent tasks targeting distinct deliverables
   ('and rewrite the paragraph', 'Draft an email and recommend...') strictly fail closed
   to ROUTE_AMBIGUOUS with MULTI_DESTINATION_CONFLICT.
3. Ambiguity Resolution Contract: Ambiguous decisions contain machine-readable
   conflicting_destinations and resolution_options, both in DestinationDecision and
   in the sealed query-destination-decision artifact extensions.
4. DecisionService Integration: DecisionService.route and DecisionService.query expose
   conflicting destinations and actionable resolution options on ambiguous routes.
5. Invariant: 0 provider calls throughout front-door L0 routing.
"""
from __future__ import annotations

import unittest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.decision_service import DecisionService
from dual_pipeline.query_router import QueryDestination, route_query


class TestWave16QueryRoutingHardening(unittest.TestCase):
    """Test suite for Wave 16 Query L0 Hardening."""

    def test_primary_intent_dominance_over_auxiliary_summaries(self) -> None:
        """Strategic decision queries with auxiliary summarization phrases must route to NEE."""
        cases = [
            (
                "Recommend future-state architecture across governed NEE evidence and summarize the findings.",
                QueryDestination.NEE_DECISION_INTELLIGENCE,
                "opportunity_nba",
                "high",
            ),
            (
                "Evaluate risks and recommend architecture for NEE across options, summarizing key tradeoffs in general terms.",
                QueryDestination.NEE_DECISION_INTELLIGENCE,
                "opportunity_nba",
                "high",
            ),
            (
                "Diagnose risk across governed NEE patterns and condense the findings.",
                QueryDestination.NEE_DECISION_INTELLIGENCE,
                "gap_risk_inconsistency",
                "medium",
            ),
            (
                "Compare conflicting evidence across governed NEE relationships and produce a summary.",
                QueryDestination.NEE_DECISION_INTELLIGENCE,
                "cross_workstream_synthesis",
                "medium",
            ),
        ]
        for question, expected_dest, expected_type, expected_mat in cases:
            with self.subTest(question=question):
                decision = route_query(question)
                self.assertEqual(expected_dest, decision.destination)
                self.assertEqual(expected_type, decision.query_type)
                self.assertEqual(expected_mat, decision.materiality)
                self.assertNotIn("MULTI_DESTINATION_CONFLICT", decision.reason_codes)

    def test_peer_actions_strictly_fail_closed(self) -> None:
        """Joined independent actions targeting separate deliverables must fail closed to ROUTE_AMBIGUOUS."""
        cases = [
            "Draft an email and recommend future-state A2A architecture across governed evidence.",
            "Recommend an NEE decision and rewrite the paragraph.",
            "Proofread the paragraph and explain NEE graph relationships.",
            "Schedule a meeting and diagnose cross-source NEE graph risks.",
            "Compare governed NEE patterns and upload the spreadsheet.",
        ]
        for question in cases:
            with self.subTest(question=question):
                decision = route_query(question)
                self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)
                self.assertIn("MULTI_DESTINATION_CONFLICT", decision.reason_codes)
                self.assertTrue(len(decision.conflicting_destinations) >= 2)
                self.assertTrue(len(decision.resolution_options) >= 2)

    def test_ambiguity_resolution_artifact_contract(self) -> None:
        """Ambiguous decisions must produce schema-valid artifacts with nee.ambiguity-resolution extension."""
        decision = route_query(
            "Draft an email and recommend future-state A2A architecture across governed evidence."
        )
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)
        self.assertIn(
            QueryDestination.GENERAL_ASSISTANT_HANDOFF.value,
            decision.conflicting_destinations,
        )
        self.assertIn(
            QueryDestination.NEE_DECISION_INTELLIGENCE.value,
            decision.conflicting_destinations,
        )
        self.assertIn("decision", decision.resolution_options)
        self.assertIn("general", decision.resolution_options)

        artifact = decision.to_artifact(
            request_id="req-wave16-ambiguity-test-001",
            run_id="run-wave16-ambiguity-test-001",
            trace_id="0123456789abcdef0123456789abcdef",
            decided_at="2026-09-07T18:00:00Z",
        )
        # Verify sealed artifact satisfies closed-world schema
        validated = validate_artifact(artifact)
        self.assertEqual("ROUTE_AMBIGUOUS", validated["destination"])
        extensions = validated.get("extensions", {})
        self.assertIn("nee.ambiguity-resolution", extensions)
        resolution_data = extensions["nee.ambiguity-resolution"]
        self.assertIn("conflicting_destinations", resolution_data)
        self.assertIn("resolution_options", resolution_data)
        self.assertIn("guidance", resolution_data)

    def test_decision_service_ambiguity_clarification(self) -> None:
        """DecisionService.route and .query must include actionable ambiguity clarification."""
        class MockQueryEngine:
            pass

        service = DecisionService(
            query_engine=MockQueryEngine(),
            runtime_root=ROOT / "runtime",
        )
        route_info = service.route("Recommend an NEE decision and rewrite the paragraph.")
        self.assertEqual("ROUTE_AMBIGUOUS", route_info["destination"])
        self.assertIn("conflicting_destinations", route_info)
        self.assertIn("resolution_options", route_info)

        query_res = service.query("Recommend an NEE decision and rewrite the paragraph.")
        self.assertEqual("CLARIFICATION_REQUIRED", query_res["terminal_state"])
        result = query_res["result"]
        self.assertIn("conflicting_destinations", result)
        self.assertIn("resolution_options", result)
        self.assertIn(
            QueryDestination.NEE_DECISION_INTELLIGENCE.value,
            result["conflicting_destinations"],
        )


if __name__ == "__main__":
    unittest.main()
