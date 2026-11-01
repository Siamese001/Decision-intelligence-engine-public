"""
tests/test_copilot_neighborhood.py

Unit tests for Copilot Bounded Multi-Hop Neighborhoods (Wave 3).
Verifies:
1. Bounded BFS depth traversal (1-hop vs 2-hop).
2. Governed hub suppression and fanout caps (prioritizing SSOT priority predicates).
3. Cycle termination and path validity.
4. Deterministic chain ordering and SHA-256 digest reproducibility.
5. Sub-millisecond lookup latency benchmark (p95 < 1.0 ms).
6. Incremental refresh for newly salient seed entities.
"""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any, Dict

from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    compute_neighborhood_chains,
    precompute_neighborhoods,
)


def _build_test_graph() -> SimpleNamespace:
    """Builds a deterministic multi-hop test graph."""
    nodes: Dict[str, Dict[str, Any]] = {
        "person:mandy": {
            "canonical_name": "harper Hoffman",
            "node_type": "person",
            "organization": "APEX_TRANS",
        },
        "org:apex_trans": {
            "canonical_name": "APEX_TRANS",
            "node_type": "organization",
            "organization": "Apex Global",
        },
        "system:scada": {
            "canonical_name": "SCADA EMS Network",
            "node_type": "system",
            "organization": "APEX_TRANS",
        },
        "system:pi_historian": {
            "canonical_name": "OSIsoft PI Historian",
            "node_type": "system",
            "organization": "APEX_TRANS",
        },
        "project:nexus": {
            "canonical_name": "Project Nexus",
            "node_type": "project",
            "organization": "Apex Global",
        },
        "workstream:telemetry": {
            "canonical_name": "Telemetry Workstream",
            "node_type": "workstream",
            "organization": "APEX_TRANS",
        },
    }

    forward_adjacency = {
        "person:mandy": [
            {"to_node_id": "org:apex_trans", "predicate": "REPORTS_TO", "to_node_name": "APEX_TRANS"},
            {"to_node_id": "project:nexus", "predicate": "OWNS_OR_LEADS", "to_node_name": "Project Nexus"},
        ],
        "project:nexus": [
            {"to_node_id": "workstream:telemetry", "predicate": "CONTAINS", "to_node_name": "Telemetry Workstream"},
            {"to_node_id": "system:scada", "predicate": "DEPENDS_ON", "to_node_name": "SCADA EMS Network"},
        ],
        "workstream:telemetry": [
            {"to_node_id": "system:pi_historian", "predicate": "USES_PATTERN", "to_node_name": "OSIsoft PI Historian"},
        ],
        "system:scada": [
            {"to_node_id": "system:pi_historian", "predicate": "CONSTRAINS", "to_node_name": "OSIsoft PI Historian"},
        ],
        "system:pi_historian": [],
        "org:apex_trans": [],
    }

    assertions = {
        "ast-1": {
            "subject_node_id": "person:mandy",
            "predicate": "OWNS_OR_LEADS",
            "object_node_id": "project:nexus",
            "claim_text": "Mandy leads Project Nexus executive steering",
        },
        "ast-2": {
            "subject_node_id": "project:nexus",
            "predicate": "DEPENDS_ON",
            "object_node_id": "system:scada",
            "claim_text": "Project Nexus depends on SCADA EMS network integration",
        },
    }

    return SimpleNamespace(
        nodes=nodes,
        forward_adjacency=forward_adjacency,
        assertions=assertions,
    )


class TestCopilotNeighborhood(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _build_test_graph()

    def test_bfs_depth_1_vs_2(self) -> None:
        """Verifies that 1-hop traversal stays at depth 1, while 2-hop traverses deeper."""
        chains_1hop, status_1 = compute_neighborhood_chains(
            self.graph,
            seed_node_ids=["person:mandy"],
            max_hops=1,
        )
        self.assertEqual(status_1, "SUCCESS")
        self.assertTrue(all(c["hops"] == 1 for c in chains_1hop))
        targets_1hop = {c["target_id"] for c in chains_1hop}
        self.assertIn("org:apex_trans", targets_1hop)
        self.assertIn("project:nexus", targets_1hop)
        self.assertNotIn("system:scada", targets_1hop)

        chains_2hop, status_2 = compute_neighborhood_chains(
            self.graph,
            seed_node_ids=["person:mandy"],
            max_hops=2,
        )
        self.assertEqual(status_2, "SUCCESS")
        targets_2hop = {c["target_id"] for c in chains_2hop}
        self.assertIn("system:scada", targets_2hop)
        self.assertIn("workstream:telemetry", targets_2hop)

        # Check chain string formatting
        scada_chain = next(c for c in chains_2hop if c["target_id"] == "system:scada")
        self.assertIn("harper Hoffman", scada_chain["chain_str"])
        self.assertIn("Project Nexus", scada_chain["chain_str"])
        self.assertIn("SCADA EMS Network", scada_chain["chain_str"])
        self.assertEqual(scada_chain["hops"], 2)

    def test_hub_suppression_and_fanout_cap(self) -> None:
        """Verifies that nodes exceeding hub threshold have fanout capped and priority predicates promoted."""
        # Create a hub node with 25 outgoing edges (above threshold 12)
        hub_node_id = "system:central_hub"
        nodes = dict(self.graph.nodes)
        nodes[hub_node_id] = {"canonical_name": "Central Hub", "node_type": "system"}

        edges = []
        # Add 5 priority edges
        for i in range(5):
            target_id = f"node:prio_{i}"
            nodes[target_id] = {"canonical_name": f"Prio Node {i}", "node_type": "system"}
            edges.append({
                "to_node_id": target_id,
                "predicate": "HAS_RISK",
                "to_node_name": f"Prio Node {i}",
            })
        # Add 20 non-priority edges
        for i in range(20):
            target_id = f"node:bulk_{i}"
            nodes[target_id] = {"canonical_name": f"Bulk Node {i}", "node_type": "entity"}
            edges.append({
                "to_node_id": target_id,
                "predicate": "RELATED_TO",
                "to_node_name": f"Bulk Node {i}",
            })

        fwd = dict(self.graph.forward_adjacency)
        fwd[hub_node_id] = edges
        hub_graph = SimpleNamespace(nodes=nodes, forward_adjacency=fwd, assertions={})

        chains, status = compute_neighborhood_chains(
            hub_graph,
            seed_node_ids=[hub_node_id],
            max_hops=1,
            max_fanout=12,
        )
        self.assertEqual(status, "SUCCESS")
        # Fanout must be capped at 12 despite 25 edges
        self.assertEqual(len(chains), 12)

        # All 5 priority edges must be present due to priority predicate promotion
        prio_chains = [c for c in chains if c["predicates"] == ["HAS_RISK"]]
        self.assertEqual(len(prio_chains), 5)

    def test_cycle_termination(self) -> None:
        """Verifies that cyclic graphs (A -> B -> C -> A) terminate gracefully without repeating nodes in path."""
        nodes = {
            "node:a": {"canonical_name": "Node A", "node_type": "system"},
            "node:b": {"canonical_name": "Node B", "node_type": "system"},
            "node:c": {"canonical_name": "Node C", "node_type": "system"},
        }
        fwd = {
            "node:a": [{"to_node_id": "node:b", "predicate": "DEPENDS_ON", "to_node_name": "Node B"}],
            "node:b": [{"to_node_id": "node:c", "predicate": "DEPENDS_ON", "to_node_name": "Node C"}],
            "node:c": [{"to_node_id": "node:a", "predicate": "DEPENDS_ON", "to_node_name": "Node A"}],
        }
        cycle_graph = SimpleNamespace(nodes=nodes, forward_adjacency=fwd, assertions={})

        chains, status = compute_neighborhood_chains(
            cycle_graph,
            seed_node_ids=["node:a"],
            max_hops=3,
        )
        self.assertEqual(status, "SUCCESS")
        for c in chains:
            # No duplicate nodes in any path
            self.assertEqual(len(c["path"]), len(set(c["path"])))

    def test_determinism_and_hash_reproducibility(self) -> None:
        """Verifies that running precomputation on the same graph produces identical chains and hash."""
        store1 = precompute_neighborhoods(self.graph, seed_node_ids=["person:mandy"], max_hops=2)
        store2 = precompute_neighborhoods(self.graph, seed_node_ids=["person:mandy"], max_hops=2)

        self.assertEqual(store1.compute_hash(), store2.compute_hash())
        self.assertEqual(len(store1.chains), len(store2.chains))
        self.assertEqual(
            [c["chain_str"] for c in store1.chains],
            [c["chain_str"] for c in store2.chains],
        )

    def test_lookup_performance_p95_sub_millisecond(self) -> None:
        """Acceptance Criteria 1: fast-tier lookup p95 is well below 1 ms."""
        store = precompute_neighborhoods(self.graph, seed_node_ids=["person:mandy"], max_hops=2)

        latencies = []
        for _ in range(1000):
            t0 = time.perf_counter()
            _ = store.get_chains_for_node("project:nexus", limit=3)
            latencies.append((time.perf_counter() - t0) * 1000)

        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]
        self.assertLess(p95, 0.5, f"Lookup p95 latency {p95:.4f} ms exceeds 0.5 ms limit")

    def test_incremental_refresh_salient_seeds(self) -> None:
        """Verifies that new salient seeds can be refreshed off the hot path."""
        store = precompute_neighborhoods(self.graph, seed_node_ids=["person:mandy"], max_hops=1)
        initial_count = len(store.chains)

        added = store.refresh_salient_seeds(["system:scada"], self.graph, max_hops=1)
        self.assertGreater(added, 0)
        self.assertGreater(len(store.chains), initial_count)
        scada_chains = store.get_chains_for_node("system:scada")
        self.assertTrue(len(scada_chains) > 0)


if __name__ == "__main__":
    unittest.main()
