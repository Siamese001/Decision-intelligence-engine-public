"""
tests/adversarial/test_query_recovery_w2_adversarial.py

Adversarial and edge-case test suite for Wave 2: Bounded Query Recovery.
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Overflow & exhaustion (budget exhaustion, attempt caps)
2. Recursion traps & multi-step loops (hard ceiling at 1 iteration, infinite loop prevention)
3. Invariant bypass & unauthorized evidence egress (namespace security boundaries, stealth quarantine)
4. Boundary payloads & malformed inputs (empty facets, missing dictionary keys, corrupted candidates)
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.retrieval.agents import (
    BoundedQueryRecoveryAgent,
    RetrievalCoordinator,
)


class QueryRecoveryW2AdversarialTests(unittest.TestCase):
    """Adversarial security and edge-case verification for bounded query recovery."""

    def setUp(self) -> None:
        self.coordinator = RetrievalCoordinator(mock_mode=True)
        self.recovery_agent = BoundedQueryRecoveryAgent(mock_mode=True)

    # --- CATEGORY 1: OVERFLOW & EXHAUSTION ---

    def test_exhaustion_01_zero_budget_blocks_recovery(self) -> None:
        """When remaining recovery budget is 0, recovery fails closed immediately."""
        query = "Evaluate compliance and delivery"
        fused = [{"name": "Fact A", "facet": "compliance"}]
        candidates = [
            {"name": "Fact A", "facet": "compliance"},
            {"name": "Fact B", "facet": "delivery"},
        ]

        updated, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=fused,
            candidate_facts=candidates,
            required_facets=["compliance", "delivery"],
            remaining_budget=0,
        )

        self.assertFalse(receipt.is_success)
        self.assertEqual(receipt.terminal_reason, "BUDGET_EXHAUSTED")
        self.assertEqual(receipt.recovered_facts_count, 0)
        self.assertEqual(len(updated), 1)

    def test_exhaustion_02_negative_budget_fails_closed(self) -> None:
        """Negative remaining budget fails closed without attempting recovery."""
        query = "Check budget limits"
        updated, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=[],
            candidate_facts=[{"name": "Fact B", "facet": "delivery"}],
            required_facets=["delivery"],
            remaining_budget=-5,
        )
        self.assertFalse(receipt.is_success)
        self.assertEqual(receipt.terminal_reason, "BUDGET_EXHAUSTED")

    # --- CATEGORY 2: RECURSION TRAPS & ITERATION CEILING ---

    def test_recursion_01_iteration_strictly_capped_at_one(self) -> None:
        """Attempts to invoke iteration > 1 fail closed with MAX_ITERATIONS_REACHED."""
        query = "Prevent infinite loop"
        fused = [{"name": "Fact 1", "facet": "f1"}]
        candidates = [{"name": "Fact 2", "facet": "f2"}]

        updated, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=fused,
            candidate_facts=candidates,
            required_facets=["f1", "f2"],
            iteration=2,  # Exceeds max allowed iterations of 1
            remaining_budget=1,
        )

        self.assertFalse(receipt.is_success)
        self.assertEqual(receipt.terminal_reason, "MAX_ITERATIONS_REACHED")
        self.assertEqual(len(updated), 1)

    def test_recursion_02_no_progress_stops_cleanly(self) -> None:
        """When candidate pool contains no facts for missing facets, terminates with NO_PROGRESS_STOP."""
        query = "Query for unavailable information"
        fused = [{"name": "Tech Architecture", "facet": "technical"}]
        # Candidate pool only has irrelevant facts
        candidates = [
            {"name": "Irrelevant Fact 1", "facet": "random_a"},
            {"name": "Irrelevant Fact 2", "facet": "random_b"},
        ]

        updated, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=fused,
            candidate_facts=candidates,
            required_facets=["technical", "nonexistent_facet"],
            iteration=1,
            remaining_budget=1,
        )

        self.assertFalse(receipt.is_success)
        self.assertEqual(receipt.terminal_reason, "NO_PROGRESS_STOP")
        self.assertEqual(receipt.recovered_facts_count, 0)
        self.assertEqual(len(updated), 1)

    # --- CATEGORY 3: INVARIANT BYPASS & UNAUTHORIZED EVIDENCE EGRESS ---

    def test_security_01_namespace_leakage_prevented(self) -> None:
        """Candidate facts belonging to unauthorized namespaces are strictly filtered out."""
        query = "Commercial contract review"
        fused = []
        # Candidates include an unauthorized internal advisory fact
        candidates = [
            {
                "node_id": "sec-01",
                "name": "Public Commercial Terms",
                "facet": "commercial_terms",
                "namespace": "CLIENT_SAFE",
            },
            {
                "node_id": "sec-02",
                "name": "Confidential Advisory Power Map",
                "facet": "stakeholder_power",
                "namespace": "INTERNAL_ADVISORY",  # Unauthorized for CLIENT_SAFE caller
            },
        ]

        updated, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=fused,
            candidate_facts=candidates,
            required_facets=["commercial_terms", "stakeholder_power"],
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
        )

        # Only the CLIENT_SAFE fact should be recovered
        recovered_ids = [f.get("node_id") for f in updated]
        self.assertIn("sec-01", recovered_ids)
        self.assertNotIn("sec-02", recovered_ids)
        self.assertNotIn("stakeholder_power", receipt.recovered_facets)

    def test_security_02_stealth_mode_quarantined_facts_blocked(self) -> None:
        """Quarantined facts are strictly blocked from recovery in stealth mode."""
        query = "Evaluate architecture"
        candidates = [
            {
                "node_id": "q-01",
                "name": "Quarantined Secret Spec",
                "facet": "secret_architecture",
                "namespace": "CLIENT_SAFE",
                "is_quarantined": True,
            }
        ]

        updated, receipt = self.recovery_agent.recover(
            query=query,
            fused_facts=[],
            candidate_facts=candidates,
            required_facets=["secret_architecture"],
            stealth_mode=True,
        )

        self.assertFalse(receipt.is_success)
        self.assertEqual(len(updated), 0)

    # --- CATEGORY 4: BOUNDARY PAYLOADS & MALFORMED INPUTS ---

    def test_boundary_01_empty_required_facets_is_noop(self) -> None:
        """Empty required facets list returns complete immediately."""
        obs = self.recovery_agent.observe_coverage("query", [], [])
        self.assertFalse(obs.has_gaps)
        self.assertEqual(obs.coverage_score, 1.0)

        updated, receipt = self.recovery_agent.recover(
            query="test",
            fused_facts=[],
            candidate_facts=[],
            required_facets=[],
        )
        self.assertEqual(receipt.terminal_reason, "COVERAGE_COMPLETE")
        self.assertTrue(receipt.is_success)

    def test_boundary_02_malformed_and_missing_keys_in_candidate(self) -> None:
        """Candidate facts with missing or weird fields do not crash matching."""
        malformed_candidates = [
            {},  # completely empty
            {"name": None, "facet": None},  # null values
            {"content": 12345, "tags": "not_a_list"},  # wrong types
            {"name": "Valid Fact", "facet": "target_facet"},
        ]

        updated, receipt = self.recovery_agent.recover(
            query="test",
            fused_facts=[],
            candidate_facts=malformed_candidates,
            required_facets=["target_facet"],
        )
        self.assertTrue(receipt.is_success)
        self.assertEqual(receipt.terminal_reason, "RECOVERY_SUCCESS")
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["name"], "Valid Fact")


if __name__ == "__main__":
    unittest.main()
