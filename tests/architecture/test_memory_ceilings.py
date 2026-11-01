"""
tests/architecture/test_memory_ceilings.py

Architectural test suite validating Phase 1 memory safety and bounded cache invariants:
1. Bounded capacity and LRU eviction (Agent 4)
2. On-demand lazy hydration without eager whole-graph BFS (Agent 4)
3. Hard resident RAM ceiling enforcement (Agent 4)
"""
from __future__ import annotations

import collections
import time
from typing import Any, Dict, List

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)


class MockSimpleTopology:
    """Minimal graph topology with nodes, forward adjacency, and assertions for unit testing."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {
            "entity_sap": {"canonical_name": "SAP ERP", "node_type": "concept"},
            "entity_daniel": {"canonical_name": "Daniel Brakin", "node_type": "person"},
            "entity_sharon": {"canonical_name": "Sharon", "node_type": "person"},
            "entity_nexus": {"canonical_name": "Project Nexus", "node_type": "engagement"},
        }
        self.forward_adjacency: Dict[str, List[Dict[str, Any]]] = {
            "entity_sap": [
                {"to_node_id": "entity_daniel", "relationship_family": "OWNED_BY", "claim_text": "SAP owned by Daniel"},
            ],
            "entity_daniel": [
                {"to_node_id": "entity_nexus", "relationship_family": "CONTRIBUTES_TO", "claim_text": "Daniel worked on Nexus"},
            ],
            "entity_sharon": [
                {"to_node_id": "entity_sap", "relationship_family": "MANAGES", "claim_text": "Sharon manages SAP"},
            ],
            "entity_nexus": [],
        }
        self.assertions: Dict[str, Any] = {}


class TestMemoryCeilingsAndLRUEviction:
    """Verifies bounded capacity, LRU eviction order, lazy loading, and RAM ceiling."""

    def test_bounded_capacity_and_lru_eviction(self) -> None:
        # Initialize store with strict max_entries ceiling = 4
        store = CopilotNeighborhoodStore(max_entries=4)
        assert store.get_cache_stats()["entries"] == 0

        # Add 2 initial chains (total 4 nodes: seed_1, target_1, seed_2, target_2)
        c1 = {"seed_id": "seed_1", "target_id": "target_1", "chain_str": "1->1", "hops": 1}
        c2 = {"seed_id": "seed_2", "target_id": "target_2", "chain_str": "2->2", "hops": 1}
        store.add_chains([c1, c2])
        assert store.get_cache_stats()["entries"] == 4

        # Access seed_1 to mark it recently used (moves seed_1 to most recently used)
        chains_1 = store.get_chains_for_node("seed_1")
        assert len(chains_1) == 1
        assert store.get_cache_stats()["hits"] == 1

        # Add 3rd chain (2 new nodes: seed_3, target_3) - must trigger LRU eviction
        c3 = {"seed_id": "seed_3", "target_id": "target_3", "chain_str": "3->3", "hops": 1}
        store.add_chains([c3])

        stats = store.get_cache_stats()
        # Ensure entries count never exceeds max_entries ceiling
        assert stats["entries"] <= 4
        assert stats["evictions"] >= 2

        # seed_1 should still be present because it was accessed recently
        assert "seed_1" in store._lru_nodes or len(store.get_chains_for_node("seed_1")) > 0

    def test_on_demand_lazy_hydration_without_eager_bfs(self) -> None:
        topology = MockSimpleTopology()
        # Initialize store completely empty
        store = CopilotNeighborhoodStore(chains=[], max_entries=50)
        assert len(store.chains) == 0

        # First query: cache miss with on-demand auto-hydration
        chains_sap = store.get_chains_for_node("entity_sap", topology=topology, auto_hydrate=True)
        assert len(chains_sap) >= 1
        assert chains_sap[0]["target_id"] == "entity_daniel"

        stats = store.get_cache_stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0
        assert stats["chains_count"] >= 1

        # Second query: cache hit without re-running BFS
        chains_sap_cached = store.get_chains_for_node("entity_sap", topology=topology, auto_hydrate=True)
        assert len(chains_sap_cached) >= 1

        stats_after = store.get_cache_stats()
        assert stats_after["hits"] == 1
        assert stats_after["misses"] == 1

    def test_resident_ram_footprint_ceiling(self) -> None:
        store = CopilotNeighborhoodStore(max_entries=1000)

        # Generate 100 chains
        chains = [
            {
                "seed_id": f"node_{i}",
                "target_id": f"target_{i}",
                "hops": 1,
                "chain_str": f"node_{i} --[OWNS]--> target_{i}",
                "last_claim": f"Entity {i} owns target {i} under project governance",
                "path": [f"node_{i}", f"target_{i}"],
                "predicates": ["OWNS"],
            }
            for i in range(100)
        ]
        store.add_chains(chains)

        footprint_bytes = store.get_memory_footprint_bytes()
        assert footprint_bytes > 0
        # Hard ceiling check: 100 entries must consume far less than the 50MB ceiling (< 1MB)
        assert footprint_bytes < 50 * 1024 * 1024
        assert footprint_bytes < 1 * 1024 * 1024

        # Clear reclaims resident structures
        store.clear()
        assert store.get_memory_footprint_bytes() < footprint_bytes
        assert store.get_cache_stats()["entries"] == 0
        assert store.get_cache_stats()["chains_count"] == 0
