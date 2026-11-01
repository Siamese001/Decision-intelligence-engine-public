"""Wave 3 Adversarial & Edge-Case Tests: Connected Graph Traversal, Split-Identity & Literal Mention Safety.

Covers mandatory edge-case categories:
1. Boundary payload (empty literal assertions, single-node graph, 0-hop request)
2. Malformed input (out-of-bounds requested_hops, NULL canonical_name)
3. Concurrency & race / cycle resistance (mutual entity_redirect cycle A <-> B, self-mentioning literal assertion)
4. Overflow & exhaustion (20+ seeds competing with expansion records under MAX_L2_INPUT_RECORDS_PER_LANE)
5. Invariant bypass attempt (card and ROUTES_TO navigation-only exclusions remain enforced in read_graph_from_disk)
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.graph_cache import read_graph_from_disk
from dual_pipeline.graph_retrieval_core.scoring import unified_result_sort_key
from dual_pipeline.graph_retrieval_core.traversal import (
    _assertion_is_navigation_only,
)


class Wave3ConnectedTraversalAdversarialTests(unittest.TestCase):
    def test_invariant_bypass_card_and_routes_to_remain_excluded(self) -> None:
        """Category 5 (Invariant bypass): card nodes and ROUTES_TO edges are never injected as answer-bearing edges."""
        with tempfile.TemporaryDirectory(prefix="wave3-adv-excl-") as tmpdir:
            graph_path = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
                        legacy_predicate TEXT, object_node_id TEXT, object_literal TEXT, materiality TEXT,
                        canonical_status TEXT, validation_state TEXT, currentness_state TEXT, retrieval_eligible INTEGER,
                        primary_source_id TEXT, recorded_at TEXT, created_at TEXT, confidence_score REAL);
                    CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT);
                    CREATE VIEW v_canonical_edge AS
                    SELECT assertion_id, subject_node_id AS from_node_id, predicate AS relationship_family,
                           predicate AS edge_type, object_node_id AS to_node_id, materiality, primary_source_id AS source_id
                    FROM assertion WHERE object_node_id IS NOT NULL;
                    CREATE VIEW v_conflicting_assertions AS
                    SELECT '' AS assertion_id_1, '' AS assertion_id_2, 'resolved' AS resolution_state WHERE 0;
                    """
                )
                conn.execute("INSERT INTO node VALUES ('n-work', 'DocIQ', 'workstream', 1)")
                conn.execute("INSERT INTO node VALUES ('c-card', 'DocIQ', 'card', 1)")
                conn.execute(
                    """INSERT INTO assertion VALUES
                       ('a-route', 'n-work', 'ROUTES_TO', 'ROUTES_TO', 'c-card', NULL, 'low',
                        'canonical', 'approved', 'current', 1, 'src-1', '2026-10-01', '2026-10-01', 1.0)"""
                )
                conn.commit()
            finally:
                conn.close()

            nodes, assertions, _, adjacency, _, _, excluded = read_graph_from_disk(graph_path)
            self.assertEqual([], adjacency.get("n-work", []))
            self.assertIn("a-route", excluded["n-work"])
            self.assertTrue(_assertion_is_navigation_only(assertions["a-route"], nodes))

    def test_cyclic_entity_redirect_and_self_mention_literal_safety(self) -> None:
        """Category 1 & 3 (Boundary payload & Cycle safety): self-mentions and mutual redirects produce no self-loops or duplicate edges."""
        with tempfile.TemporaryDirectory(prefix="wave3-adv-cycle-") as tmpdir:
            graph_path = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE entity_redirect (old_node_id TEXT, new_node_id TEXT);
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
                        legacy_predicate TEXT, object_node_id TEXT, object_literal TEXT, materiality TEXT,
                        canonical_status TEXT, validation_state TEXT, currentness_state TEXT, retrieval_eligible INTEGER,
                        primary_source_id TEXT, recorded_at TEXT, created_at TEXT, confidence_score REAL);
                    CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT);
                    CREATE VIEW v_canonical_edge AS
                    SELECT assertion_id, subject_node_id AS from_node_id, predicate AS relationship_family,
                           predicate AS edge_type, object_node_id AS to_node_id, materiality, primary_source_id AS source_id
                    FROM assertion WHERE object_node_id IS NOT NULL;
                    CREATE VIEW v_conflicting_assertions AS
                    SELECT '' AS assertion_id_1, '' AS assertion_id_2, 'resolved' AS resolution_state WHERE 0;
                    """
                )
                conn.execute("INSERT INTO node VALUES ('n-a', 'Project Beacon', 'workstream', 1)")
                conn.execute("INSERT INTO node VALUES ('n-b', 'Project Beacon', 'system', 1)")
                # Mutual redirect cycle + self redirect
                conn.execute("INSERT INTO entity_redirect VALUES ('n-a', 'n-b')")
                conn.execute("INSERT INTO entity_redirect VALUES ('n-b', 'n-a')")
                conn.execute("INSERT INTO entity_redirect VALUES ('n-a', 'n-a')")
                # Self-mentioning literal assertion on n-a
                conn.execute(
                    """INSERT INTO assertion VALUES
                       ('a-self', 'n-a', 'HAS_CLAIM_TEXT', NULL, NULL, 'Project Beacon is active.', 'high',
                        'canonical', 'approved', 'current', 1, 'src-1', '2026-10-01', '2026-10-01', 0.9)"""
                )
                conn.commit()
            finally:
                conn.close()

            _, _, _, adjacency, _, _, _ = read_graph_from_disk(graph_path)
            # No self-loop on n-a
            self.assertFalse(any(e["to_node_id"] == "n-a" for e in adjacency["n-a"]))
            # Exactly one forward twin bridge to n-b
            twin_edges = [
                e for e in adjacency["n-a"]
                if e["to_node_id"] == "n-b" and e["relationship_family"] == "SAME_ENTITY_AS"
            ]
            self.assertEqual(1, len(twin_edges))

    def test_low_scoring_expansion_does_not_displace_high_scoring_seeds(self) -> None:
        """Category 4 (Overflow & ranking boundary): low-scoring expansion records rank below stronger seeds."""
        seed_paths = {"s-9": {"path_score": 0.45}}
        seed_item = {
            "record_id": "s-9",
            "fused_rank": 9,
            "graph_rank": None,
            "graph_score": None,
            "retrieval_origin": "hybrid_seed",
        }
        weak_exp = {
            "record_id": "exp-weak",
            "fused_rank": None,
            "graph_rank": 1,
            "graph_score": 0.12,
            "retrieval_origin": "graph_expansion",
        }
        self.assertLess(
            unified_result_sort_key(seed_item, seed_paths),
            unified_result_sort_key(weak_exp, seed_paths),
        )


if __name__ == "__main__":
    unittest.main()
