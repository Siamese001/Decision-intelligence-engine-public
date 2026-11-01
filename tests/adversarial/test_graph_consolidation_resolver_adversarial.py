"""
tests/adversarial/test_graph_consolidation_resolver_adversarial.py

Adversarial stress tests for Graph Consolidation Wave 3 supersession resolver.
Covers:
1. Boundary confidence margins (floating point precision at exactly 0.15).
2. Invariant bypass attempts (attempting to supersede multi-valued predicates).
3. Deterministic tie-breaks under completely identical timestamps, evidence counts, and scores.
4. Malformed/sparse assertion records missing timestamps, scores, and sources.
"""
from __future__ import annotations

import unittest

from dual_pipeline.graph_consolidation_core.policy import SupersessionPolicy
from dual_pipeline.graph_consolidation_core.resolver import resolve_pairwise_supersession


class TestGraphConsolidationResolverAdversarial(unittest.TestCase):
    """Adversarial and boundary test suite for supersession resolution."""

    def setUp(self) -> None:
        self.policy = SupersessionPolicy(confidence_margin_threshold=0.15)

    def test_floating_point_margin_boundaries(self) -> None:
        """Tests exact floating-point behavior at the 15% ambiguity boundary."""
        # Case A: Delta is 0.149 (< 0.15) -> HITL required
        a1 = {"assertion_id": "a1", "subject_node_id": "s", "predicate": "p", "object_node_id": "o", "confidence_score": 0.85}
        a2 = {"assertion_id": "a2", "subject_node_id": "s", "predicate": "p", "object_node_id": "o", "confidence_score": 0.701}
        d_just_below = resolve_pairwise_supersession(a1, a2, policy=self.policy)
        self.assertIsNotNone(d_just_below)
        self.assertTrue(d_just_below.hitl_required)

        # Case B: Delta is exactly 0.15000 (>= 0.15) -> Autonomous resolution
        a3 = {"assertion_id": "a3", "subject_node_id": "s", "predicate": "p", "object_node_id": "o", "confidence_score": 0.70}
        d_exact = resolve_pairwise_supersession(a1, a3, policy=self.policy)
        self.assertIsNotNone(d_exact)
        self.assertFalse(d_exact.hitl_required)

    def test_multi_valued_bypass_attempts(self) -> None:
        """Verifies that multi-valued predicates cannot be tricked into supersession with different objects."""
        # Predicates with many_to_many cardinality (e.g. PARTICIPATES_IN, CONTAINS, TAGGED_WITH)
        a1 = {"assertion_id": "a1", "subject_node_id": "team_1", "predicate": "CONTAINS", "object_node_id": "member_alice", "confidence_score": 1.0}
        a2 = {"assertion_id": "a2", "subject_node_id": "team_1", "predicate": "CONTAINS", "object_node_id": "member_bob", "confidence_score": 0.1}

        # Attempt to supersede despite different objects
        d = resolve_pairwise_supersession(a1, a2, policy=self.policy, predicate_cardinality="many_to_many")
        self.assertIsNone(d, "Multi-valued predicate must not supersede distinct object nodes")

        # Explicitly checking with literal values
        a_lit1 = {"assertion_id": "a1", "subject_node_id": "team_1", "predicate": "KEYWORD", "object_literal": "ai", "confidence_score": 1.0}
        a_lit2 = {"assertion_id": "a2", "subject_node_id": "team_1", "predicate": "KEYWORD", "object_literal": "cloud", "confidence_score": 0.2}
        d_lit = resolve_pairwise_supersession(a_lit1, a_lit2, policy=self.policy, predicate_cardinality="many_to_many")
        self.assertIsNone(d_lit, "Multi-valued predicate must not supersede distinct literal objects")

    def test_deterministic_lexical_tie_break_symmetry(self) -> None:
        """Verifies that tie-break order is strictly deterministic and order-independent."""
        # Identical scores, timestamps, and evidence counts
        a_alpha = {
            "assertion_id": "asrt_alpha",
            "subject_node_id": "s",
            "predicate": "p",
            "object_node_id": "o",
            "confidence_score": 0.8,
            "created_at": "2026-07-08T00:00:00Z",
        }
        a_beta = {
            "assertion_id": "asrt_beta",
            "subject_node_id": "s",
            "predicate": "p",
            "object_node_id": "o",
            "confidence_score": 0.8,
            "created_at": "2026-07-08T00:00:00Z",
        }

        # Resolving (alpha, beta) -> alpha wins (asrt_alpha < asrt_beta)
        d_fwd = resolve_pairwise_supersession(a_alpha, a_beta, policy=self.policy)
        self.assertIsNotNone(d_fwd)
        self.assertEqual(d_fwd.winner_id, "asrt_alpha")
        self.assertEqual(d_fwd.loser_id, "asrt_beta")
        self.assertEqual(d_fwd.selection_reason, "lexical tie-break")

        # Resolving (beta, alpha) -> alpha still wins
        d_rev = resolve_pairwise_supersession(a_beta, a_alpha, policy=self.policy)
        self.assertIsNotNone(d_rev)
        self.assertEqual(d_rev.winner_id, "asrt_alpha")
        self.assertEqual(d_rev.loser_id, "asrt_beta")
        self.assertEqual(d_rev.selection_reason, "lexical tie-break")

    def test_sparse_and_missing_attributes_graceful_handling(self) -> None:
        """Handles sparse dictionaries without crashing and defaults safely."""
        a_sparse1 = {"assertion_id": "sp1", "subject_node_id": "s", "predicate": "p"}
        a_sparse2 = {"assertion_id": "sp2", "subject_node_id": "s", "predicate": "p"}

        d = resolve_pairwise_supersession(a_sparse1, a_sparse2, policy=self.policy)
        self.assertIsNotNone(d)
        self.assertEqual(d.winner_id, "sp1")
        self.assertEqual(d.loser_id, "sp2")
        self.assertEqual(d.winner_confidence, 0.8)
        self.assertEqual(d.loser_confidence, 0.8)
        self.assertEqual(d.confidence_delta, 0.0)
        self.assertTrue(d.hitl_required)


if __name__ == "__main__":
    unittest.main()
