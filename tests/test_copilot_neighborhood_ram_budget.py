"""
tests/test_copilot_neighborhood_ram_budget.py

Stress tests for CopilotNeighborhoodStore verifying:
1. Hard capacity ceiling strictly capping at max_chains (250 by default).
2. LRU eviction order preserving accessed items while discarding oldest unaccessed chains.
3. RAM memory footprint remains well under the 8MB ceiling when loaded to capacity.
4. Bounded parameter clamping for hops and max_chains.
"""
from __future__ import annotations

import sys
import tracemalloc
from typing import Any, Dict, List

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    DEFAULT_MAX_CHAINS,
    HARD_MAX_CHAINS,
    CopilotNeighborhoodStore,
    compute_neighborhood_chains,
)


def _generate_synthetic_chain(i: int) -> Dict[str, Any]:
    """Generates a realistic multi-hop chain dictionary with rich text and metadata."""
    return {
        "seed_id": f"person:engineer_{i}",
        "target_id": f"system:microservice_{i}",
        "nodes": [f"person:engineer_{i}", f"project:core_{i}", f"system:microservice_{i}"],
        "predicates": ["LEADS", "DEPLOYS"],
        "hops": 2,
        "is_priority": (i % 3 == 0),
        "chain_str": f"Engineer {i} -> Leads Project {i} -> Deploys Microservice {i}",
        "assertions": [
            {
                "assertion_id": f"asrt_{i}_1",
                "claim_text": f"Engineer {i} leads core development workstream {i}",
                "predicate": "LEADS",
            },
            {
                "assertion_id": f"asrt_{i}_2",
                "claim_text": f"Project {i} deploys backend microservice {i} to Kubernetes cluster",
                "predicate": "DEPLOYS",
            },
        ],
    }


def test_neighborhood_store_caps_at_max_chains() -> None:
    """Verifies that injecting 1,200 chains strictly caps capacity at default max_chains (250)."""
    store = CopilotNeighborhoodStore(max_chains=DEFAULT_MAX_CHAINS)
    assert store.max_chains == 250
    assert store.chain_count == 0

    # Inject 1,200 chains in batches
    batch = [_generate_synthetic_chain(i) for i in range(1200)]
    store.add_chains(batch)

    assert store.chain_count == 250
    assert len(store.chains) == 250
    assert store.chain_count <= DEFAULT_MAX_CHAINS


def test_neighborhood_store_lru_eviction_policy() -> None:
    """Verifies that accessing an older chain preserves it while oldest unaccessed chain is evicted."""
    store = CopilotNeighborhoodStore(max_chains=5)

    initial_chains = [_generate_synthetic_chain(i) for i in range(5)]
    store.add_chains(initial_chains)
    assert store.chain_count == 5

    # Access chain 0 (seed: person:engineer_0, target: system:microservice_0)
    touched = store.get_chains_for_node("system:microservice_0")
    assert len(touched) == 1
    assert touched[0]["seed_id"] == "person:engineer_0"

    # Add a 6th chain (engineer_5)
    store.add_chains([_generate_synthetic_chain(5)])
    assert store.chain_count == 5

    # Chain 0 was recently accessed, so it MUST survive
    assert len(store.get_chains_for_node("system:microservice_0")) == 1

    # Chain 1 was the oldest untouched, so it MUST be evicted
    assert len(store.get_chains_for_node("system:microservice_1")) == 0

    # Chain 5 (just inserted) must be present
    assert len(store.get_chains_for_node("system:microservice_5")) == 1


def test_neighborhood_store_ram_budget_under_8mb() -> None:
    """
    Stress-measures heap memory consumption of a fully saturated CopilotNeighborhoodStore.
    Guarantees heap growth remains well below the 8MB RAM budget limit.
    """
    tracemalloc.start()
    tracemalloc.reset_peak()

    store = CopilotNeighborhoodStore(max_chains=DEFAULT_MAX_CHAINS)
    chains = [_generate_synthetic_chain(i) for i in range(1000)]
    store.add_chains(chains)

    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak_bytes / (1024 * 1024)
    # 8.0 MB is the hard governance ceiling; store should comfortably use < 1.0 MB
    assert peak_mb < 8.0, f"Memory peak {peak_mb:.2f}MB exceeded 8.0MB RAM ceiling"
    assert store.chain_count == 250


def test_neighborhood_parameter_clamping() -> None:
    """Verifies bounds clamping on store max_chains and neighborhood expansion hops."""
    # Store max_chains ceiling clamped to HARD_MAX_CHAINS (500)
    store_oversized = CopilotNeighborhoodStore(max_chains=5000)
    assert store_oversized.max_chains == HARD_MAX_CHAINS

    # Store max_chains floor clamped to 1
    store_undersized = CopilotNeighborhoodStore(max_chains=-10)
    assert store_undersized.max_chains == 1

    # compute_neighborhood_chains empty/none topology resilience
    empty_chains, status = compute_neighborhood_chains(
        topology=None,
        seed_node_ids=["person:test"],
        max_hops=10,  # exceeds allowed 2 hops
    )
    assert empty_chains == []
    assert status == "EMPTY"
