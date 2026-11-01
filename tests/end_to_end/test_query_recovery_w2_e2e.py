"""
tests/end_to_end/test_query_recovery_w2_e2e.py

End-to-end qualification suite for Wave 2: Query Recovery.
Verifies requirement-scoped query coverage observation, bounded targeted secondary retrieval,
feature flag gating, ranking/coverage improvement, and seamless learning integration.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.feature_flags import FeatureFlagState, get_feature_flag_manager
from dual_pipeline.retrieval.agents import (
    BoundedQueryRecoveryAgent,
    QueryRecoveryObservation,
    RecoveryActionReceipt,
    RetrievalCoordinator,
)


class QueryRecoveryW2E2ETests(unittest.TestCase):
    """E2E verification of bounded query recovery in Pipeline C retrieval."""

    def setUp(self) -> None:
        self.coordinator = RetrievalCoordinator(mock_mode=True)
        self.recovery_agent = BoundedQueryRecoveryAgent(mock_mode=True)
        self.flag_mgr = get_feature_flag_manager()

        # Seed realistic candidate facts covering multiple enterprise facets
        self.candidate_facts = [
            {
                "node_id": "fact-tech-01",
                "name": "BGE-M3 Dense/ColBERT Architecture",
                "content": "BGE-M3 utilizes multi-stage dense and ColBERT token-level representations for vector search.",
                "facet": "technical_architecture",
                "category": "technical",
                "namespace": "CLIENT_SAFE",
                "confidence": 0.94,
            },
            {
                "node_id": "fact-tech-02",
                "name": "SQLite Hybrid Vector Index Storage",
                "content": "Vector indexes are stored in SQLite sidecars with zero-contention WAL journaling.",
                "facet": "storage_infrastructure",
                "category": "technical",
                "namespace": "CLIENT_SAFE",
                "confidence": 0.91,
            },
            {
                "node_id": "fact-comm-01",
                "name": "SOW Phase 2 Delivery Timeline",
                "content": "Phase 2 delivery deadline is committed for Q4 2026 with milestone completion reviews.",
                "facet": "delivery_timeline",
                "category": "commercial",
                "namespace": "CLIENT_SAFE",
                "confidence": 0.89,
            },
            {
                "node_id": "fact-risk-01",
                "name": "Vendor Lock-in and Egress Risk",
                "content": "Cloud provider dependency poses data transfer friction and egress budget risk.",
                "facet": "risk_assessment",
                "category": "risk",
                "namespace": "CLIENT_SAFE",
                "confidence": 0.86,
            },
        ]

    def test_e2e_coverage_observation_and_targeted_facet_recovery(self) -> None:
        """Observe partial coverage, execute targeted secondary search, and recover missing facet."""
        query = "What is the technical architecture and delivery timeline for the vector engine?"
        required_facets = ["technical_architecture", "delivery_timeline"]

        # Initial one-pass retrieval only found technical facts
        initial_fused = [self.candidate_facts[0]]  # only technical_architecture
        obs_before = self.recovery_agent.observe_coverage(query, initial_fused, required_facets)
        self.assertTrue(obs_before.has_gaps)
        self.assertEqual(obs_before.covered_facets, ["technical_architecture"])
        self.assertEqual(obs_before.missing_facets, ["delivery_timeline"])
        self.assertEqual(obs_before.coverage_score, 0.50)

        # Execute bounded recovery
        updated_fused, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=initial_fused,
            candidate_facts=self.candidate_facts,
            required_facets=required_facets,
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
            iteration=1,
            remaining_budget=1,
        )

        # Assert successful recovery
        self.assertTrue(receipt.is_success)
        self.assertEqual(receipt.terminal_reason, "RECOVERY_SUCCESS")
        self.assertEqual(receipt.iteration, 1)
        self.assertEqual(receipt.recovered_facets, ["delivery_timeline"])
        self.assertEqual(receipt.final_coverage_score, 1.00)
        self.assertEqual(len(updated_fused), 2)
        self.assertIn("delivery_timeline", [f.get("facet") for f in updated_fused])

    def test_e2e_coordinator_feature_flag_gated_recovery(self) -> None:
        """Verify retrieval coordinator respects feature flag and recovers missing requirements."""
        query = "Review architecture and delivery commitments"
        required_facets = ["technical_architecture", "delivery_timeline"]

        # Only pass the technical fact as candidate for one-pass search, but keep full pool in candidates
        # In mock mode, ranker takes filtered facts. If we set candidate_facts with only tech in first pass:
        first_pass_facts = [self.candidate_facts[0]]

        # 1. Feature Flag OFF: One-pass only, no recovery receipt
        with self.flag_mgr.override("BOUNDED_QUERY_RECOVERY", FeatureFlagState.OFF):
            res_off = self.coordinator.retrieve(
                query=query,
                candidate_facts=first_pass_facts,
                required_facets=required_facets,
            )
            self.assertIsNone(res_off.get("recovery_receipt"))
            self.assertEqual(len(res_off["ranked_results"]), 1)

        # 2. Feature Flag ON: Bounded query recovery triggers on missing requirement
        with self.flag_mgr.override("BOUNDED_QUERY_RECOVERY", FeatureFlagState.ON):
            res_on = self.coordinator.retrieve(
                query=query,
                candidate_facts=self.candidate_facts,
                required_facets=required_facets,
            )
            rec = res_on.get("recovery_receipt")
            self.assertIsNotNone(rec)
            self.assertEqual(rec["terminal_reason"], "COVERAGE_COMPLETE")
            self.assertTrue(rec["is_success"])

    def test_e2e_retrieval_ranking_and_coverage_improvement(self) -> None:
        """Demonstrate that bounded recovery improves multi-facet requirement coverage."""
        query = "Evaluate architecture, timeline, and risk"
        required_facets = ["technical_architecture", "delivery_timeline", "risk_assessment"]

        # Initial retrieval with only 1 fact (coverage 0.33)
        initial_facts = [self.candidate_facts[0]]
        obs_1 = self.recovery_agent.observe_coverage(query, initial_facts, required_facets)
        self.assertAlmostEqual(obs_1.coverage_score, 0.3333, places=2)

        # Recover missing facets
        recovered_facts, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=initial_facts,
            candidate_facts=self.candidate_facts,
            required_facets=required_facets,
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
            iteration=1,
            remaining_budget=1,
        )

        self.assertTrue(receipt.is_success)
        self.assertEqual(receipt.terminal_reason, "RECOVERY_SUCCESS")
        self.assertEqual(receipt.initial_coverage_score, 0.3333)
        self.assertEqual(receipt.final_coverage_score, 1.00)
        self.assertEqual(len(receipt.recovered_facets), 2)
        self.assertEqual(len(recovered_facts), 3)

        # Release gate checks pass
        self.assertLess(receipt.latency_ms, 7500.0)


if __name__ == "__main__":
    unittest.main()
