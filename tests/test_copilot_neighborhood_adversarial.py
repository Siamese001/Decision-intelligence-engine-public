"""
tests/test_copilot_neighborhood_adversarial.py

Adversarial and boundary stress tests for Copilot Bounded Multi-Hop Neighborhoods (Wave 3).
Verifies:
1. Hub explosion (100+ dense edges) terminates cleanly under fanout caps.
2. Complex multi-cycle topologies terminate without infinite recursion.
3. Timeout budget overrun returns status 'PARTIAL' gracefully without raising exceptions.
4. Rule C-19 Career Firewall: C-19 markers in node IDs, names, orgs, and edge claims are strictly quarantined.
5. Empty, null, and disconnected graphs behave predictably without crashing.
6. Dangling edge references to nonexistent nodes do not crash BFS.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, Dict

from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
    compute_neighborhood_chains,
)


class TestCopilotNeighborhoodAdversarial(unittest.TestCase):
    def test_hub_explosion_stress(self) -> None:
        """Adversarial stress: Hub node with 200 outgoing edges terminates under fanout cap."""
        nodes: Dict[str, Dict[str, Any]] = {
            "node:hub": {"canonical_name": "Mega Hub", "node_type": "system"},
        }
        edges = []
        for i in range(200):
            nid = f"node:leaf_{i}"
            nodes[nid] = {"canonical_name": f"Leaf {i}", "node_type": "entity"}
            edges.append({
                "to_node_id": nid,
                "predicate": "RELATED_TO" if i % 2 == 0 else "DEPENDS_ON",
                "to_node_name": f"Leaf {i}",
            })

        graph = SimpleNamespace(
            nodes=nodes,
            forward_adjacency={"node:hub": edges},
            assertions={},
        )

        chains, status = compute_neighborhood_chains(
            graph,
            seed_node_ids=["node:hub"],
            max_hops=2,
            max_fanout=12,
        )
        self.assertEqual(status, "SUCCESS")
        self.assertLessEqual(len(chains), 12)

    def test_complex_multi_cycle_interlocking_mesh(self) -> None:
        """Adversarial: Interlocking cyclic mesh (K4 fully connected) terminates without infinite loops."""
        node_ids = [f"node:k_{i}" for i in range(5)]
        nodes = {nid: {"canonical_name": nid, "node_type": "system"} for nid in node_ids}
        forward_adjacency = {}
        for u in node_ids:
            forward_adjacency[u] = [
                {"to_node_id": v, "predicate": "DEPENDS_ON", "to_node_name": v}
                for v in node_ids
                if v != u
            ]

        graph = SimpleNamespace(nodes=nodes, forward_adjacency=forward_adjacency, assertions={})

        chains, status = compute_neighborhood_chains(
            graph,
            seed_node_ids=["node:k_0"],
            max_hops=3,
        )
        self.assertEqual(status, "SUCCESS")
        for c in chains:
            # Each node appears at most once in path
            self.assertEqual(len(c["path"]), len(set(c["path"])))

    def test_timeout_budget_exhaustion_yields_partial(self) -> None:
        """Acceptance Criteria 4: When timeout budget expires, returns PARTIAL without raising."""
        # Create a deep and wide graph
        nodes = {}
        fwd = {}
        for i in range(100):
            nid = f"node:{i}"
            nodes[nid] = {"canonical_name": f"Node {i}", "node_type": "entity"}
            fwd[nid] = [
                {"to_node_id": f"node:{(i + 1) % 100}", "predicate": "DEPENDS_ON"},
                {"to_node_id": f"node:{(i + 2) % 100}", "predicate": "CONTAINS"},
            ]

        graph = SimpleNamespace(nodes=nodes, forward_adjacency=fwd, assertions={})

        # Near-zero timeout (1 microsecond) forces timeout branch
        chains, status = compute_neighborhood_chains(
            graph,
            seed_node_ids=[f"node:{i}" for i in range(50)],
            max_hops=3,
            timeout_seconds=0.000001,
        )
        self.assertEqual(status, "PARTIAL")
        self.assertIsInstance(chains, list)

    def test_c19_career_firewall_strict_quarantine(self) -> None:
        """Acceptance Criteria 3: Nodes and claims with C-19 markers NEVER appear in any chain."""
        nodes = {
            "node:clean_seed": {"canonical_name": "Clean Seed", "node_type": "person"},
            "node:c19_in_id": {"canonical_name": "Normal Name", "node_type": "system"},
            "node:c19_in_name": {
                "canonical_name": "Internal Advisory Note on career_leverage",
                "node_type": "system",
            },
            "node:c19_in_org": {
                "canonical_name": "Vendor X",
                "organization": "political_rationale committee",
                "node_type": "org",
            },
            "node:target": {"canonical_name": "Legitimate System", "node_type": "system"},
        }

        fwd = {
            "node:clean_seed": [
                {"to_node_id": "node:c19_in_id", "predicate": "OWNS_OR_LEADS"},
                {"to_node_id": "node:c19_in_name", "predicate": "DELIVERS"},
                {"to_node_id": "node:c19_in_org", "predicate": "AFFILIATED_WITH"},
                {
                    "to_node_id": "node:target",
                    "predicate": "DEPENDS_ON",
                    "claim_text": "$1.2m consulting budget as networking_wedge",
                },
            ],
            "node:target": [],
        }

        # Inject marker directly into ID
        nodes["node:internal_advisory_secret"] = {"canonical_name": "Secret", "node_type": "system"}
        fwd["node:clean_seed"].append({
            "to_node_id": "node:internal_advisory_secret",
            "predicate": "DEPENDS_ON",
        })

        graph = SimpleNamespace(nodes=nodes, forward_adjacency=fwd, assertions={})

        chains, status = compute_neighborhood_chains(
            graph,
            seed_node_ids=["node:clean_seed"],
            max_hops=2,
        )
        self.assertEqual(status, "SUCCESS")

        # Every chain must be 100% C-19 free
        for c in chains:
            self.assertNotIn("career_leverage", c["chain_str"])
            self.assertNotIn("political_rationale", c["chain_str"])
            self.assertNotIn("$1.2m", c["chain_str"])
            self.assertNotIn("networking_wedge", c["chain_str"])
            self.assertNotIn("internal_advisory", c["chain_str"])
            for nid in c["path"]:
                self.assertNotIn("internal_advisory", nid)
                self.assertNotIn("career_leverage", nid)

        # Precompute store test
        store = CopilotNeighborhoodStore(chains=chains)
        summary = store.format_chains_summary()
        self.assertNotIn("$1.2m", summary)
        self.assertNotIn("career_leverage", summary)

    def test_empty_and_null_graph_handling(self) -> None:
        """Verifies graceful handling of empty or None graphs."""
        chains, status = compute_neighborhood_chains(None, seed_node_ids=["node:1"])
        self.assertEqual(status, "EMPTY")
        self.assertEqual(chains, [])

        empty_graph = SimpleNamespace(nodes={}, forward_adjacency={}, assertions={})
        chains, status = compute_neighborhood_chains(empty_graph, seed_node_ids=[])
        self.assertEqual(status, "EMPTY")
        self.assertEqual(chains, [])

    def test_dangling_edge_targets(self) -> None:
        """Verifies that edges pointing to unknown nodes in topology do not crash the engine."""
        nodes = {"node:root": {"canonical_name": "Root", "node_type": "person"}}
        fwd = {
            "node:root": [
                {"to_node_id": "node:nonexistent_1", "predicate": "DEPENDS_ON"},
                {"to_node_id": "node:nonexistent_2", "predicate": "DELIVERS"},
            ]
        }
        graph = SimpleNamespace(nodes=nodes, forward_adjacency=fwd, assertions={})

        chains, status = compute_neighborhood_chains(graph, seed_node_ids=["node:root"], max_hops=2)
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(len(chains), 2)
        self.assertTrue(all(c["hops"] == 1 for c in chains))


if __name__ == "__main__":
    unittest.main()
