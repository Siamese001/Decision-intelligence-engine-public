"""
tests/test_graph_consolidation_resolver.py

Unit and regression tests for Graph Consolidation Wave 3 supersession policy and resolver.
Validates:
1. Parameter bounds and policy validation.
2. Multi-valued predicate preservation vs exclusive cardinality supersession.
3. Winner selection hierarchy (evidence count -> confidence score -> recency -> lexical tie-break).
4. Ambiguity margin gating (15% confidence threshold).
5. Evidence consolidation onto survivor assertions.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_consolidation_core.policy import (
    DEFAULT_CONFIDENCE_MARGIN,
    MAX_CONFIDENCE_MARGIN,
    MIN_CONFIDENCE_MARGIN,
    SupersessionPolicy,
    validate_supersession_policy,
)
from dual_pipeline.graph_consolidation_core.resolver import (
    find_governed_supersessions,
    resolve_graph_supersessions,
    resolve_pairwise_supersession,
)


class TestGraphConsolidationResolver(unittest.TestCase):
    """Unit tests for SupersessionPolicy and SupersessionResolver."""

    def test_policy_bounds_and_validation(self) -> None:
        """Validates parameter bounding on confidence_margin_threshold."""
        valid_policy = SupersessionPolicy(confidence_margin_threshold=0.20)
        validate_supersession_policy(valid_policy)
        self.assertEqual(valid_policy.confidence_margin_threshold, 0.20)

        # Minimum bound violation
        with self.assertRaises(ValueError):
            SupersessionPolicy(confidence_margin_threshold=MIN_CONFIDENCE_MARGIN - 0.01)

        # Maximum bound violation
        with self.assertRaises(ValueError):
            SupersessionPolicy(confidence_margin_threshold=MAX_CONFIDENCE_MARGIN + 0.01)

    def test_multi_valued_predicate_preservation(self) -> None:
        """Ensures multi_to_many predicates with different objects are NOT superseded."""
        policy = SupersessionPolicy()
        a1 = {
            "assertion_id": "a1",
            "subject_node_id": "sub1",
            "predicate": "has_attendee",
            "object_node_id": "person_alice",
            "confidence_score": 0.9,
        }
        a2 = {
            "assertion_id": "a2",
            "subject_node_id": "sub1",
            "predicate": "has_attendee",
            "object_node_id": "person_bob",
            "confidence_score": 0.6,
        }

        # Multi-valued predicate (many_to_many) must not supersede distinct attendees
        decision = resolve_pairwise_supersession(a1, a2, policy=policy, predicate_cardinality="many_to_many")
        self.assertIsNone(decision)

    def test_exclusive_cardinality_supersession(self) -> None:
        """Ensures exclusive predicates (many_to_one) with different objects ARE resolved."""
        policy = SupersessionPolicy()
        a1 = {
            "assertion_id": "a1",
            "subject_node_id": "proj1",
            "predicate": "has_status",
            "object_node_id": "status_active",
            "confidence_score": 0.95,
        }
        a2 = {
            "assertion_id": "a2",
            "subject_node_id": "proj1",
            "predicate": "has_status",
            "object_node_id": "status_pending",
            "confidence_score": 0.70,
        }

        decision = resolve_pairwise_supersession(a1, a2, policy=policy, predicate_cardinality="many_to_one")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.winner_id, "a1")
        self.assertEqual(decision.loser_id, "a2")
        self.assertFalse(decision.hitl_required)
        self.assertAlmostEqual(decision.confidence_delta, 0.25)

    def test_exact_triple_duplicate_consolidation_and_evidence(self) -> None:
        """Exact triple duplicates consolidate evidence onto winner regardless of cardinality."""
        policy = SupersessionPolicy()
        a1 = {
            "assertion_id": "a1",
            "subject_node_id": "sub1",
            "predicate": "uses_technology",
            "object_node_id": "tech_python",
            "confidence_score": 0.85,
        }
        a2 = {
            "assertion_id": "a2",
            "subject_node_id": "sub1",
            "predicate": "uses_technology",
            "object_node_id": "tech_python",
            "confidence_score": 0.85,
        }
        ev_map = {
            "a1": ["ev_1", "ev_2"],
            "a2": ["ev_2", "ev_3"],
        }

        decision = resolve_pairwise_supersession(
            a1, a2, policy=policy, predicate_cardinality="many_to_many", ev_links_map=ev_map
        )
        self.assertIsNotNone(decision)
        # Ties fall back to lexical (a1 < a2)
        self.assertEqual(decision.winner_id, "a1")
        self.assertEqual(decision.loser_id, "a2")
        # Evidence ev_3 from loser is transferred
        self.assertIn("ev_3", decision.evidence_ids_to_transfer)
        self.assertNotIn("ev_2", decision.evidence_ids_to_transfer)  # Already on winner

    def test_ambiguity_margin_gating_15_percent(self) -> None:
        """Verifies hitl_required is True when confidence delta < 15% and False when >= 15%."""
        policy = SupersessionPolicy(confidence_margin_threshold=DEFAULT_CONFIDENCE_MARGIN)

        # Delta = 0.10 (< 0.15) -> HITL required
        a_low_margin_1 = {"assertion_id": "a1", "subject_node_id": "s", "predicate": "p", "object_node_id": "o", "confidence_score": 0.85}
        a_low_margin_2 = {"assertion_id": "a2", "subject_node_id": "s", "predicate": "p", "object_node_id": "o", "confidence_score": 0.75}
        d_hitl = resolve_pairwise_supersession(a_low_margin_1, a_low_margin_2, policy=policy)
        self.assertIsNotNone(d_hitl)
        self.assertTrue(d_hitl.hitl_required)
        self.assertAlmostEqual(d_hitl.confidence_delta, 0.10)

        # Delta = 0.20 (>= 0.15) -> Autonomous resolution
        a_high_margin_1 = {"assertion_id": "a1", "subject_node_id": "s", "predicate": "p", "object_node_id": "o", "confidence_score": 0.90}
        a_high_margin_2 = {"assertion_id": "a2", "subject_node_id": "s", "predicate": "p", "object_node_id": "o", "confidence_score": 0.70}
        d_auto = resolve_pairwise_supersession(a_high_margin_1, a_high_margin_2, policy=policy)
        self.assertIsNotNone(d_auto)
        self.assertFalse(d_auto.hitl_required)
        self.assertAlmostEqual(d_auto.confidence_delta, 0.20)

    def test_database_graph_resolution_and_adapter(self) -> None:
        """Tests resolve_graph_supersessions and find_governed_supersessions in SQLite."""
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "test.sqlite"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE assertion_predicate_registry (
                predicate TEXT PRIMARY KEY,
                cardinality TEXT NOT NULL
            );
            INSERT INTO assertion_predicate_registry VALUES ('PARTICIPATES_IN', 'many_to_many');

            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                object_literal TEXT,
                confidence_score REAL NOT NULL DEFAULT 1.0,
                primary_source_id TEXT,
                recorded_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z',
                created_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z',
                currentness_state TEXT NOT NULL DEFAULT 'current',
                canonical_status TEXT NOT NULL DEFAULT 'canonical',
                superseded_by_assertion_id TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                PRIMARY KEY (assertion_id, evidence_id)
            );

            -- Two exact triple duplicates
            INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score)
            VALUES ('asrt_1', 'sub1', 'PARTICIPATES_IN', 'obj1', 0.90);
            INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score)
            VALUES ('asrt_2', 'sub1', 'PARTICIPATES_IN', 'obj1', 0.60);
            """
        )
        conn.commit()

        decisions = resolve_graph_supersessions(conn)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].winner_id, "asrt_1")
        self.assertEqual(decisions[0].loser_id, "asrt_2")

        # Test compatibility adapter
        dicts = find_governed_supersessions(conn)
        self.assertEqual(len(dicts), 1)
        self.assertEqual(dicts[0]["winner_id"], "asrt_1")
        self.assertIn("hitl_required", dicts[0])

        conn.close()
        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
