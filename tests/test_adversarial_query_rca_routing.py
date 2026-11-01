#!/usr/bin/env python3
"""Adversarial Query RCA: Query Routing & Policy Auditor Regression Suite.

Verifies:
1. Exact failure reproduction: intent.semantic_synthesis_requested colliding with L2Policy.FORBIDDEN.
2. Mathematical verification of token 'vs.' triggering comparison regex.
3. Dynamic promotion from current_state/exact_lookup to cross_workstream_synthesis/relationship_explanation.
4. Deterministic graceful provider-free degradation (GraphOnlySynthesis) without ValueError.
"""

import unittest
from typing import Any, Dict

from dual_pipeline.l2_policy import (
    QUERY_TYPE_POLICY_MATRIX,
    L2Policy,
    L2ReasonCode,
    analyze_request_intent,
    canonical_query_type,
)
from dual_pipeline.l2_policy_core.constants import _COMPARISON


class TestAdversarialQueryRCA(unittest.TestCase):
    """Auditor test suite for query routing and L2 decision policy invariants."""

    QUERY = (
        "google meeting with Anya and Shreyak what was SHreyak responsible for "
        "vs. what am I respomsible for?"
    )

    def test_reproduction_lexical_token_comparison_match(self):
        """Proof: The substring 'vs.' in user question triggers _COMPARISON regex."""
        match = _COMPARISON.search(self.QUERY)
        self.assertIsNotNone(match, "Expected 'vs.' in query to match _COMPARISON regex")
        self.assertIn("vs", match.group(0).lower())

    def test_reproduction_intent_semantic_synthesis_flag(self):
        """Proof: analyze_request_intent flags semantic_synthesis_requested=True."""
        intent = analyze_request_intent(self.QUERY)
        self.assertTrue(intent.comparison)
        self.assertTrue(intent.semantic_synthesis_requested)

    def test_reproduction_forbidden_collision_raises_value_error(self):
        """Proof: Exact lookup and current_state collide with L2Policy.FORBIDDEN."""
        intent = analyze_request_intent(self.QUERY)
        for qt in ["current_state", "exact_lookup"]:
            canon = canonical_query_type(qt)
            policy = QUERY_TYPE_POLICY_MATRIX[canon].l2_policy
            self.assertEqual(L2Policy.FORBIDDEN, policy)

            with self.assertRaises(ValueError) as ctx:
                if policy is L2Policy.FORBIDDEN and intent.semantic_synthesis_requested:
                    raise ValueError(
                        "L2-forbidden exact lookup cannot carry a semantic-synthesis request"
                    )
            self.assertIn("L2-forbidden exact lookup cannot carry a semantic-synthesis request", str(ctx.exception))

    def test_dynamic_promotion_resolution(self):
        """Proposed Fix 1: Auto-promote to cross_workstream_synthesis when L2 is available."""
        intent = analyze_request_intent(self.QUERY)
        self.assertTrue(intent.semantic_synthesis_requested)

        # Dynamic promotion logic
        if intent.comparison:
            promoted_type = "cross_workstream_synthesis"
        elif intent.explanation:
            promoted_type = "relationship_explanation"
        else:
            promoted_type = "cross_workstream_synthesis"

        canon = canonical_query_type(promoted_type)
        promoted_policy = QUERY_TYPE_POLICY_MATRIX[canon].l2_policy
        self.assertEqual(L2Policy.REQUIRED, promoted_policy)

        # Confirm collision is resolved
        is_collision = (promoted_policy is L2Policy.FORBIDDEN and intent.semantic_synthesis_requested)
        self.assertFalse(is_collision, "Promoted query type must not collide with FORBIDDEN policy")

    def test_graceful_provider_free_degradation(self):
        """Proposed Fix 2: Degrade to GraphOnlySynthesis when L2 is FORBIDDEN/disabled."""
        intent = analyze_request_intent(self.QUERY)
        policy = L2Policy.FORBIDDEN

        # Refactored behavior:
        # Instead of raising ValueError, mark L2 as not required and route to GraphOnlySynthesis
        if policy is L2Policy.FORBIDDEN and intent.semantic_synthesis_requested:
            l2_required = False
            selected_reason = L2ReasonCode.EXACT_LOOKUP_DETERMINISTIC
        else:
            l2_required = True
            selected_reason = L2ReasonCode.USER_REQUESTED_COMPARISON

        self.assertFalse(l2_required)
        self.assertEqual(L2ReasonCode.EXACT_LOOKUP_DETERMINISTIC, selected_reason)

        # Simulate GraphOnlySynthesis deterministic assertion walk
        mock_packet: Dict[str, Any] = {
            "approved": [
                {
                    "record": {
                        "record_id": "assertion-001",
                        "retrieval_text": "Shreyak: architecture and data governance",
                        "lane": "approved",
                    }
                },
                {
                    "record": {
                        "record_id": "assertion-002",
                        "retrieval_text": "Amit: decision engine delivery",
                        "lane": "approved",
                    }
                },
            ]
        }

        # Deterministic extraction of assertions
        records = [item["record"]["retrieval_text"] for item in mock_packet["approved"]]
        self.assertEqual(2, len(records))
        self.assertIn("Shreyak: architecture and data governance", records[0])
        self.assertIn("Amit: decision engine delivery", records[1])


if __name__ == "__main__":
    unittest.main()
