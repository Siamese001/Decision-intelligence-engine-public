#!/usr/bin/env python3
"""Adversarial & Edge-Case Test Suite for Wave 2: Query Routing and Replanning Autonomy.

Covers mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary Payload: Empty string, whitespace, unicode control characters, massive string payloads.
2. Overflow & Exhaustion: Budget depletion (budget=0), iteration exceeding max, infinite loop prevention.
3. Invariant Bypass Attempt: Tenant isolation and Rule C-19 quarantine leak prevention.
4. Concurrency & Race: Multi-threaded parallel query routing under swarm competition.
"""
from __future__ import annotations

import concurrent.futures
import unittest
from typing import Any, Dict, List

from dual_pipeline.query_swarm import (
    CompetitiveQuerySwarm,
    QueryRouteSubAgent,
    SemanticIntentClassifier,
)
from dual_pipeline.retrieval.agents.bounded_query_recovery_agent import (
    BoundedQueryRecoveryAgent,
)


class TestQueryRoutingAdversarial(unittest.TestCase):
    """Adversarial suite probing query routing and recovery boundaries."""

    def setUp(self) -> None:
        self.swarm = CompetitiveQuerySwarm()
        self.recovery_agent = BoundedQueryRecoveryAgent(max_iterations=1)

    def test_boundary_payloads_empty_and_whitespace(self) -> None:
        """Category 1: Boundary Payload - Empty, whitespace, and null string queries must not crash."""
        for empty_q in ("", "   ", "\t\n\r", "   \n   "):
            winner = self.swarm.route_query(empty_q)
            self.assertIn("route_id", winner)
            self.assertEqual(winner["confidence"], 0.0)

            profile = SemanticIntentClassifier.decompose(empty_q)
            self.assertEqual(profile.token_count, 0)
            self.assertFalse(profile.exact_quote_detected)

    def test_boundary_payloads_massive_and_special_chars(self) -> None:
        """Category 1: Boundary Payload - Giant query with unicode and special symbols."""
        massive_query = ("strategic risk evaluation and decision metrics " * 500) + "\x00\ufffd\u200b"
        winner = self.swarm.route_query(massive_query)
        self.assertIn("route_id", winner)
        self.assertGreater(winner["confidence"], 0.5)

    def test_overflow_budget_exhaustion_halts_recovery(self) -> None:
        """Category 2: Overflow & Exhaustion - Budget=0 must exit immediately with BUDGET_EXHAUSTED."""
        initial_facts = [{"id": "f1", "content": "Basic overview"}]
        required_facets = ["missing_facet_1"]
        candidate_facts = [{"id": "f2", "content": "Recovery content", "tags": ["missing_facet_1"]}]

        recovered, receipt = self.recovery_agent.recover(
            query="test query",
            fused_facts=initial_facts,
            candidate_facts=candidate_facts,
            required_facets=required_facets,
            remaining_budget=0,  # Depleted budget
        )

        self.assertEqual(receipt.terminal_reason, "BUDGET_EXHAUSTED")
        self.assertFalse(receipt.is_success)
        self.assertEqual(receipt.iteration, 1)
        self.assertEqual(len(recovered), 1)  # Zero facts added

    def test_overflow_iteration_cap_strictly_enforced(self) -> None:
        """Category 2: Overflow & Exhaustion - Iteration > max_iterations must exit with MAX_ITERATIONS_REACHED."""
        initial_facts = [{"id": "f1", "content": "Basic overview"}]
        required_facets = ["missing_facet_1"]
        candidate_facts = [{"id": "f2", "content": "Recovery content", "tags": ["missing_facet_1"]}]

        recovered, receipt = self.recovery_agent.recover(
            query="test query",
            fused_facts=initial_facts,
            candidate_facts=candidate_facts,
            required_facets=required_facets,
            iteration=2,  # Exceeds max_iterations=1
            remaining_budget=5,
        )

        self.assertEqual(receipt.terminal_reason, "MAX_ITERATIONS_REACHED")
        self.assertFalse(receipt.is_success)
        self.assertEqual(len(recovered), 1)

    def test_invariant_bypass_quarantine_and_namespace_isolation(self) -> None:
        """Category 3: Invariant Bypass - CLIENT_SAFE caller must NEVER recover INTERNAL_ADVISORY or quarantined facts."""
        initial_facts = [{"id": "f1", "content": "Public cloud architecture"}]
        required_facets = ["compensation", "executive_leverage"]

        # Sensitive facts in quarantined/internal namespace
        candidate_facts = [
            {
                "id": "leak-1",
                "content": "$1.2M compensation trajectory strategy",
                "tags": ["compensation"],
                "namespace": "INTERNAL_ADVISORY",
                "is_quarantined": True,
            },
            {
                "id": "leak-2",
                "content": "Confidential executive career leverage notes",
                "tags": ["executive_leverage"],
                "namespace": "INTERNAL_ADVISORY",
                "is_quarantined": False,
            },
            {
                "id": "safe-1",
                "content": "Standard compensation benchmark public report",
                "tags": ["compensation"],
                "namespace": "CLIENT_SAFE",
                "is_quarantined": False,
            }
        ]

        # Recovery as CLIENT_SAFE caller
        recovered, receipt = self.recovery_agent.recover(
            query="Find compensation data",
            fused_facts=initial_facts,
            candidate_facts=candidate_facts,
            required_facets=required_facets,
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
        )

        recovered_ids = [f["id"] for f in recovered]
        self.assertNotIn("leak-1", recovered_ids, "CRITICAL: Quarantined fact leaked to CLIENT_SAFE caller!")
        self.assertNotIn("leak-2", recovered_ids, "CRITICAL: INTERNAL_ADVISORY fact leaked to CLIENT_SAFE caller!")
        self.assertIn("safe-1", recovered_ids)

    def test_concurrency_race_parallel_swarm_routing(self) -> None:
        """Category 4: Concurrency & Race - Swarm must handle parallel multi-threaded queries without state corruption."""
        queries = [
            'find exact claim "CLAIM-001"',
            "strategic decision trade-offs for Apex Global",
            "connected dependencies in graph traversal",
            "chronological timeline of events before Q3",
            "aggregate total count of all metrics",
            "explain conceptual rationale of the architecture",
        ] * 10  # 60 concurrent queries

        def _route(q: str) -> str:
            res = self.swarm.route_query(q)
            return res["route_id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_route, q) for q in queries]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 60)
        self.assertTrue(all(r.startswith("ROUTE_") for r in results))


if __name__ == "__main__":
    unittest.main()
