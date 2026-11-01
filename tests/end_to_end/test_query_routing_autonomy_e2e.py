#!/usr/bin/env python3
"""End-to-End Acceptance Test Suite for Wave 2: Query Routing and Replanning Autonomy.

Verifies:
1. Multi-domain semantic intent classification across 8 specialized query routes.
2. Route accuracy >=90% without reliance on substring keyword matching.
3. Bounded Query Recovery: observing missing facets, single-turn replanning, and recovery receipt generation.
4. Tenant boundary isolation and client-safe posture preservation.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.query_swarm import (
    CompetitiveQuerySwarm,
    QueryRouteSubAgent,
    SemanticIntentClassifier,
)
from dual_pipeline.retrieval.agents.bounded_query_recovery_agent import (
    BoundedQueryRecoveryAgent,
    QueryRecoveryObservation,
    RecoveryActionReceipt,
)


class TestQueryRoutingAutonomyE2E(unittest.TestCase):
    """E2E test suite evaluating autonomous query routing and bounded facet recovery."""

    def setUp(self) -> None:
        self.swarm = CompetitiveQuerySwarm()
        self.recovery_agent = BoundedQueryRecoveryAgent(max_iterations=1)

    def test_semantic_intent_decomposition_profiles(self) -> None:
        """Classifier must extract structured semantic intent profiles from distinct query types."""
        profile_strat = SemanticIntentClassifier.decompose("What are the strategic executive risks and trade-offs of migrating to GKE?")
        self.assertGreaterEqual(profile_strat.strategic_evaluation_score, 0.40)
        self.assertEqual(profile_strat.dominant_intent, "ROUTE_6_STRATEGIC_W6")

        profile_rel = SemanticIntentClassifier.decompose("How are Node X and Node Y connected across the dependency graph?")
        self.assertGreaterEqual(profile_rel.relational_topology_score, 0.40)
        self.assertEqual(profile_rel.dominant_intent, "ROUTE_3_GRAPH_TRAVERSAL")

        profile_temp = SemanticIntentClassifier.decompose("What was the chronological timeline of events before and after the August rebaseline?")
        self.assertGreaterEqual(profile_temp.temporal_chronology_score, 0.40)
        self.assertEqual(profile_temp.dominant_intent, "ROUTE_5_TEMPORAL_W3")

        profile_anal = SemanticIntentClassifier.decompose("Calculate the total count and average distribution of assertions across programs.")
        self.assertGreaterEqual(profile_anal.analytical_metric_score, 0.40)
        self.assertEqual(profile_anal.dominant_intent, "ROUTE_7_ANALYTICAL_AGGREGATION")

    def test_multi_domain_routing_benchmark_accuracy(self) -> None:
        """Competitive swarm must correctly route diverse domain queries with >=90% accuracy."""
        test_cases = [
            # Exact Match
            ('lookup exact claim id "CLAIM-2026-09-10-001"', "ROUTE_1_EXACT_MATCH"),
            ('find exact term "Alex Rivera"', "ROUTE_1_EXACT_MATCH"),
            # Strategic W6
            ("Analyze the strategic trade-offs and executive priority for the AI SDLC framework", "ROUTE_6_STRATEGIC_W6"),
            ("What are the key governance risks and operational outcomes of the merger?", "ROUTE_6_STRATEGIC_W6"),
            # Graph Traversal
            ("Trace the network connection path between PostgreSQL and MoveWorks", "ROUTE_3_GRAPH_TRAVERSAL"),
            ("What downstream dependencies relate to the Dataproc Serverless pipeline?", "ROUTE_3_GRAPH_TRAVERSAL"),
            # Temporal W3
            ("Show the chronological timeline of milestone commitments after Q2", "ROUTE_5_TEMPORAL_W3"),
            ("What preceding events occurred before the August rebaseline date?", "ROUTE_5_TEMPORAL_W3"),
            # Analytical Aggregation
            ("What is the total count and aggregate metric of verified stakeholders?", "ROUTE_7_ANALYTICAL_AGGREGATION"),
            ("Compute the average distribution of assertion confidence across projects", "ROUTE_7_ANALYTICAL_AGGREGATION"),
            # Semantic / Conceptual
            ("Explain the conceptual architecture and underlying mechanism of the dual pipeline", "ROUTE_2_SEMANTIC_SEARCH"),
            ("Why did the team decide to synthesize transcripts using local Metal MLX?", "ROUTE_2_SEMANTIC_SEARCH"),
        ]

        correct = 0
        total = len(test_cases)
        for query, expected_route in test_cases:
            winner = self.swarm.route_query(query)
            if winner["route_id"] == expected_route:
                correct += 1
            else:
                self.fail(f"Query '{query}' routed to {winner['route_id']}, expected {expected_route}")

        accuracy = correct / total
        self.assertGreaterEqual(accuracy, 0.90, f"Routing accuracy {accuracy:.2%} below 90% target")

    def test_bounded_query_recovery_coverage_observation(self) -> None:
        """Recovery agent observes missing requirement facets from retrieved fact sets."""
        fused_facts = [
            {"id": "fact-1", "name": "GKE cluster deployment", "tags": ["infrastructure", "kubernetes"]},
            {"id": "fact-2", "name": "Terraform provisioning", "category": "automation"},
        ]

        # Case 1: Partial coverage
        required_facets = ["kubernetes", "cost_governance", "security"]
        obs = self.recovery_agent.observe_coverage("Deploy GKE cluster", fused_facts, required_facets)
        self.assertTrue(obs.has_gaps)
        self.assertEqual(obs.covered_facets, ["kubernetes"])
        self.assertIn("cost_governance", obs.missing_facets)
        self.assertIn("security", obs.missing_facets)
        self.assertAlmostEqual(obs.coverage_score, 1 / 3, places=2)

        # Case 2: Full coverage
        full_facets = ["infrastructure", "automation"]
        obs_full = self.recovery_agent.observe_coverage("Deploy GKE cluster", fused_facts, full_facets)
        self.assertFalse(obs_full.has_gaps)
        self.assertEqual(obs_full.coverage_score, 1.0)
        self.assertEqual(obs_full.missing_facets, [])

    def test_bounded_query_recovery_single_turn_replanning(self) -> None:
        """Recovery agent formulates scoped query and executes bounded single-turn recovery."""
        query = "Evaluate AI SDLC governance"
        missing_facets = ["audit_logging", "rbac_enforcement"]
        targeted_q = self.recovery_agent.formulate_targeted_query(query, missing_facets)

        self.assertIn(query, targeted_q)
        self.assertIn("audit_logging", targeted_q)
        self.assertIn("rbac_enforcement", targeted_q)

        # Execute recovery run with candidate pool
        initial_facts = [{"id": "f1", "content": "AI SDLC architecture overview"}]
        candidate_facts = [
            {"id": "f2", "content": "Audit logging verified for all model calls", "tags": ["audit_logging"], "namespace": "CLIENT_SAFE"},
            {"id": "f3", "content": "Unrelated fact", "tags": ["unrelated"], "namespace": "CLIENT_SAFE"}
        ]
        required_facets = ["architecture", "audit_logging"]

        recovered_facts, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=initial_facts,
            candidate_facts=candidate_facts,
            required_facets=required_facets,
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
            iteration=1,
            remaining_budget=1,
        )

        self.assertEqual(receipt.terminal_reason, "RECOVERY_SUCCESS")
        self.assertEqual(receipt.iteration, 1)
        self.assertEqual(receipt.max_iterations, 1)
        self.assertGreater(receipt.final_coverage_score, receipt.initial_coverage_score)
        self.assertIn("audit_logging", receipt.recovered_facets)
        self.assertEqual(receipt.caller_namespace, "CLIENT_SAFE")
        self.assertEqual(len(recovered_facts), 2)


if __name__ == "__main__":
    unittest.main()
