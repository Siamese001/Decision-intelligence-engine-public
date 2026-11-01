"""
tests/test_copilot_predicate_ssot_e2e.py

End-to-End integration test suite for Copilot & Query Predicate SSOT Alignment (Wave 1).
Verifies:
1. Acceptance Criterion 3: Fixture graph with HAS_RISK and CANDIDATE_FOR yields those
   assertions in both next-questions and graph-context.
2. Prefetch bridge end-to-end hydration respects SSOT predicates and categorizes
   HAS_RISK into open_risks and CANDIDATE_FOR into open_decisions.
3. Query planning pipeline generates a valid query-plan artifact for cross_workstream_synthesis
   incorporating DEPENDS_ON, HAS_RISK, and CONTAINS.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from dual_pipeline.l2 import load_l2_config
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
)
from dual_pipeline.query import QueryDestination, build_query_plan


class TestCopilotPredicateSSOTE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_nodes = {
            "proj_alpha": {
                "canonical_name": "Project Alpha",
                "display_name": "Alpha",
                "node_type": "project",
            },
            "risk_budget": {
                "canonical_name": "Budget Overrun",
                "display_name": "Overrun Risk",
                "node_type": "risk",
            },
            "opp_scaling": {
                "canonical_name": "Scaling Partner",
                "display_name": "Scale Opp",
                "node_type": "opportunity",
            },
            "comp_subsystem": {
                "canonical_name": "Subsystem B",
                "display_name": "Subsystem",
                "node_type": "system",
            },
            "src_sow": {
                "canonical_name": "SOW Doc",
                "display_name": "SOW",
                "node_type": "source",
            },
        }

        self.mock_assertions = {
            "asrt_risk_1": {
                "subject_node_id": "proj_alpha",
                "predicate": "HAS_RISK",
                "object_node_id": "risk_budget",
                "claim_text": "Project Alpha faces severe budget overrun risk in Q4.",
                "canonical_status": "ACTIVE",
            },
            "asrt_opp_1": {
                "subject_node_id": "proj_alpha",
                "predicate": "CANDIDATE_FOR",
                "object_node_id": "opp_scaling",
                "claim_text": "Project Alpha is a prime candidate for scaling partner engagement.",
                "canonical_status": "ACTIVE",
            },
            "asrt_contains_1": {
                "subject_node_id": "proj_alpha",
                "predicate": "CONTAINS",
                "object_node_id": "comp_subsystem",
                "claim_text": "Project Alpha contains mission-critical Subsystem B.",
                "canonical_status": "ACTIVE",
            },
            "asrt_supp_1": {
                "subject_node_id": "proj_alpha",
                "predicate": "SUPPORTED_BY",
                "object_node_id": "src_sow",
                "claim_text": "Project Alpha is supported by SOW Doc delivery agreement.",
                "canonical_status": "ACTIVE",
            },
        }

        self.mock_topology = SimpleNamespace(
            nodes=self.mock_nodes,
            assertions=self.mock_assertions,
            forward_adjacency={
                "proj_alpha": ["risk_budget", "opp_scaling", "comp_subsystem", "src_sow"]
            },
            reverse_adjacency={},
        )
        self.retriever = CopilotGraphRetriever(self.mock_topology)

    def test_e2e_fixture_graph_yields_has_risk_and_candidate_for(self) -> None:
        """Acceptance Criterion 3: HAS_RISK and CANDIDATE_FOR are returned in next-questions and priority context."""
        dialogue = "Let us discuss Project Alpha budget and scaling partner plans."

        # 1. Verify next-questions synthesis
        questions = self.retriever.get_next_questions(dialogue, max_questions=5)
        self.assertGreaterEqual(len(questions), 2)
        question_predicates = [q.get("predicate") for q in questions]
        self.assertIn("HAS_RISK", question_predicates)
        self.assertIn("CANDIDATE_FOR", question_predicates)

        # 2. Verify graph context marks priority predicates
        context = self.retriever.get_graph_context(dialogue)
        self.assertEqual(len(context), 4)

        priority_by_pred = {c.get("predicate"): c.get("is_priority") for c in context}
        self.assertTrue(priority_by_pred.get("HAS_RISK"))
        self.assertTrue(priority_by_pred.get("CANDIDATE_FOR"))
        self.assertTrue(priority_by_pred.get("CONTAINS"))
        self.assertTrue(priority_by_pred.get("SUPPORTED_BY"))

    def test_e2e_prefetch_bridge_categorization(self) -> None:
        """Verifies CopilotPrefetchBridge populates open_risks with HAS_RISK and open_decisions with CANDIDATE_FOR."""
        bridge = CopilotPrefetchBridge(retriever=self.retriever)
        packet = bridge.prefetch(
            project_name="alpha",
            meeting_name="Budget and Scaling Strategy",
            attendees=[],
        )

        self.assertEqual(packet.project_scope, "alpha")
        self.assertEqual(packet.prefetch_status, "SUCCESS")

        risk_claims = [r.get("text") for r in packet.open_risks]
        self.assertTrue(
            any("budget overrun" in c.lower() for c in risk_claims),
            f"Expected HAS_RISK in open_risks, got: {packet.open_risks}",
        )

        decision_claims = [d.get("text") for d in packet.open_decisions]
        self.assertTrue(
            any("candidate for scaling" in c.lower() for c in decision_claims),
            f"Expected CANDIDATE_FOR in open_decisions, got: {packet.open_decisions}",
        )

    def test_e2e_query_planning_cross_workstream_artifact(self) -> None:
        """Verifies build_query_plan for cross_workstream_synthesis generates valid artifact with new relationships."""
        l2_cfg = load_l2_config()
        destination_decision = {
            "canonical_digest": {"value": "d" * 64},
            "destination": QueryDestination.NEE_DECISION_INTELLIGENCE.value,
            "delivery_profile": "internal_technical",
            "artifact_type": "decision_brief",
        }

        plan = build_query_plan(
            question="Synthesize cross-workstream risks and dependencies across Alpha and Subsystems.",
            query_type="cross_workstream_synthesis",
            request_id="req-wave1-e2e",
            run_id="run-wave1-e2e",
            current_trace_id="0123456789abcdef0123456789abcdef",
            l2_config=l2_cfg,
            provider_egress_allowed=False,
            destination_decision=destination_decision,
            assurance_profile="standard",
        )

        self.assertEqual(plan["query_type"], "cross_workstream_synthesis")
        relationships = plan["relationships_to_inspect"]
        self.assertIn("DEPENDS_ON", relationships)
        self.assertIn("HAS_RISK", relationships)
        self.assertIn("CONTAINS", relationships)
        self.assertIn("CONTRIBUTES_TO", relationships)
        self.assertIn("USES_PATTERN", relationships)
        self.assertIn("SUPPORTED_BY", relationships)
        self.assertIn("canonical_digest", plan)
        self.assertEqual(len(plan["canonical_digest"]["value"]), 64)


if __name__ == "__main__":
    unittest.main()
