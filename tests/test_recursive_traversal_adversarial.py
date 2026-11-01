#!/usr/bin/env python3
"""Adversarial and stress tests for recursive graph traversal.

Mandatory Edge-Case Categories Covered:
1. Boundary Payload: Empty node list, nonexistent nodes, max_hops boundaries (<= 0, > 5).
2. Concurrency & Cycles: Highly cyclic graphs (A -> B -> C -> A) and self-loops.
3. Overflow & Exhaustion: Dense cliques (K_20 complete graph) depth-limit exhaustion.
4. Malformed Input: Missing database file, nonexistent edge types, empty database.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.graph_retrieval import traverse_k_hop_neighborhood


class TestRecursiveTraversalAdversarial(unittest.TestCase):
    """Adversarial suite for recursive k-hop traversal."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "adversarial.sqlite"
        self._init_schema(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _init_schema(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL
            );
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                description TEXT,
                confidence TEXT,
                materiality TEXT,
                superseded_by TEXT
            );
            """
        )
        conn.close()

    # ── Category 1: Boundary Payload ──────────────────────────────────────────

    def test_boundary_empty_start_node_ids(self) -> None:
        """Verify empty start nodes list returns empty neighborhood structure cleanly."""
        result = traverse_k_hop_neighborhood([], graph_path=self.db_path)
        self.assertEqual(result["node_count"], 0)
        self.assertEqual(result["edge_count"], 0)
        self.assertEqual(result["nodes"], {})
        self.assertEqual(result["edges"], [])

    def test_boundary_nonexistent_start_nodes(self) -> None:
        """Verify nonexistent start node returns empty result without throwing exceptions."""
        result = traverse_k_hop_neighborhood(["phantom_node_999"], graph_path=self.db_path)
        self.assertEqual(result["node_count"], 0)
        self.assertEqual(result["edge_count"], 0)

    def test_boundary_max_hops_bounding(self) -> None:
        """Verify negative or excessive max_hops are safely clamped between 1 and 5."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO node VALUES ('n1', 'Node 1', 'type')")
        conn.commit()
        conn.close()

        # Negative hops clamps to 1
        res_neg = traverse_k_hop_neighborhood(["n1"], max_hops=-5, graph_path=self.db_path)
        self.assertEqual(res_neg["max_hops"], 1)

        # 0 hops clamps to 1
        res_zero = traverse_k_hop_neighborhood(["n1"], max_hops=0, graph_path=self.db_path)
        self.assertEqual(res_zero["max_hops"], 1)

        # 99 hops clamps to 5
        res_huge = traverse_k_hop_neighborhood(["n1"], max_hops=99, graph_path=self.db_path)
        self.assertEqual(res_huge["max_hops"], 5)

    # ── Category 2: Concurrency & Cycles ──────────────────────────────────────

    def test_cyclic_graph_infinite_loop_prevention(self) -> None:
        """Verify that cyclic graph (A -> B -> C -> A) terminates without recursion error."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            INSERT INTO node VALUES ('node_a', 'Node A', 'concept');
            INSERT INTO node VALUES ('node_b', 'Node B', 'concept');
            INSERT INTO node VALUES ('node_c', 'Node C', 'concept');

            -- Cycle: A -> B -> C -> A
            INSERT INTO edge VALUES ('e_ab', 'node_a', 'LINKS', 'node_b', '', 'high', 'high', NULL);
            INSERT INTO edge VALUES ('e_bc', 'node_b', 'LINKS', 'node_c', '', 'high', 'high', NULL);
            INSERT INTO edge VALUES ('e_ca', 'node_c', 'LINKS', 'node_a', '', 'high', 'high', NULL);
            """
        )
        conn.close()

        # With direction="out", path is node_a -> node_b (1) -> node_c (2) -> node_a (stopped by cycle check)
        result_out = traverse_k_hop_neighborhood(["node_a"], max_hops=5, direction="out", graph_path=self.db_path)
        self.assertEqual(result_out["node_count"], 3)
        self.assertEqual(set(result_out["nodes"].keys()), {"node_a", "node_b", "node_c"})
        self.assertEqual(result_out["nodes"]["node_a"]["depth"], 0)
        self.assertEqual(result_out["nodes"]["node_b"]["depth"], 1)
        self.assertEqual(result_out["nodes"]["node_c"]["depth"], 2)

        # With direction="both", node_c is also reachable via reverse of e_ca in 1 hop
        result_both = traverse_k_hop_neighborhood(["node_a"], max_hops=5, direction="both", graph_path=self.db_path)
        self.assertEqual(result_both["node_count"], 3)
        self.assertEqual(result_both["nodes"]["node_c"]["depth"], 1)

    def test_self_loop_edge_handling(self) -> None:
        """Verify that a self-loop edge (A -> A) does not cause recursion stall."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            INSERT INTO node VALUES ('node_self', 'Self Node', 'concept');
            INSERT INTO edge VALUES ('e_self', 'node_self', 'RECURS', 'node_self', '', 'high', 'high', NULL);
            """
        )
        conn.close()

        result = traverse_k_hop_neighborhood(["node_self"], max_hops=3, graph_path=self.db_path)
        self.assertEqual(result["node_count"], 1)
        self.assertEqual(result["nodes"]["node_self"]["depth"], 0)

    # ── Category 3: Overflow & Exhaustion ─────────────────────────────────────

    def test_dense_clique_k20_exhaustion(self) -> None:
        """Stress test: Complete graph K_20 (20 nodes, 190 edges).

        Verifies that traversal across a dense clique completes rapidly (<50ms)
        without exponential path explosion.
        """
        conn = sqlite3.connect(self.db_path)
        n = 20
        for i in range(n):
            conn.execute("INSERT INTO node VALUES (?, ?, ?)", (f"clique_{i}", f"Clique {i}", "clique"))

        edge_id = 0
        for i in range(n):
            for j in range(i + 1, n):
                conn.execute(
                    "INSERT INTO edge VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                    (f"e_{edge_id}", f"clique_{i}", "CONNECTED", f"clique_{j}", "", "high", "high"),
                )
                edge_id += 1
        conn.commit()
        conn.close()

        result = traverse_k_hop_neighborhood(["clique_0"], max_hops=3, graph_path=self.db_path)
        self.assertEqual(result["node_count"], 20)
        self.assertEqual(result["edge_count"], 190)
        self.assertLess(result["latency_ms"], 50.0, "Dense clique traversal must complete under 50ms")

    # ── Category 4: Malformed Input ───────────────────────────────────────────

    def test_malformed_missing_database_file(self) -> None:
        """Verify nonexistent database path raises FileNotFoundError fail-closed."""
        missing = Path(self.tmp_dir.name) / "nonexistent.sqlite"
        with self.assertRaises(FileNotFoundError):
            traverse_k_hop_neighborhood(["n1"], graph_path=missing)

    def test_edge_type_filter_exclusion(self) -> None:
        """Verify edge_types filtering restricts traversal strictly to allowed relationships."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            INSERT INTO node VALUES ('root', 'Root', 'root');
            INSERT INTO node VALUES ('allowed_child', 'Allowed', 'child');
            INSERT INTO node VALUES ('blocked_child', 'Blocked', 'child');

            INSERT INTO edge VALUES ('e_allow', 'root', 'ALLOWED_REL', 'allowed_child', '', 'high', 'high', NULL);
            INSERT INTO edge VALUES ('e_block', 'root', 'FORBIDDEN_REL', 'blocked_child', '', 'high', 'high', NULL);
            """
        )
        conn.close()

        result = traverse_k_hop_neighborhood(
            ["root"],
            max_hops=2,
            edge_types=["ALLOWED_REL"],
            graph_path=self.db_path,
        )
        self.assertIn("allowed_child", result["nodes"])
        self.assertNotIn("blocked_child", result["nodes"])


if __name__ == "__main__":
    unittest.main()
