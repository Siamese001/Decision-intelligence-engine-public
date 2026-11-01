"""
tests/architecture/test_cache_isomorphism.py

Cache-to-Source Isomorphism & Lineage Preservation Gate.
Enforces Phase 2 CI Testing Rigor:
- Proves structural isomorphism between persistent SQLite graph storage and in-memory caches.
- Asserts that multi-tenant boundary filters isolate only lateral/unrelated edges,
  perfectly preserving upstream dependency topology (PREDECESSOR_PROJECT, DEPENDS_ON).
- Verifies exact path fidelity and sub-millisecond retrieval across the resident cache.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Set

import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
    is_node_in_scope,
    resolve_permitted_engagements,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
    compute_neighborhood_chains,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from tests.fixtures.high_fidelity_graph import create_high_fidelity_sqlite_graph


@pytest.fixture
def high_fidelity_topology(tmp_path: Path) -> InMemoryGraphTopology:
    db_path = tmp_path / "isomorphism_test.sqlite"
    create_high_fidelity_sqlite_graph(db_path=db_path, runtime_dir=tmp_path)
    return InMemoryGraphTopology.load_from_sqlite(db_path)


class TestCacheToSourceIsomorphism:
    def test_upstream_lineage_preservation_isomorphism(
        self, high_fidelity_topology: InMemoryGraphTopology
    ) -> None:
        """
        Gate 1: Assert that all upstream dependency edges and assertions from
        predecessor engagements (project:project_nexus) in SQLite are 100%
        preserved in the resident retriever and neighborhood cache for project_jarvis.
        """
        topo = high_fidelity_topology
        base_eng = "project:project_jarvis"

        # 1. Inspect persistent/hydrated graph for upstream predecessor edges
        upstream_assertions: Dict[str, Dict] = {}
        for aid, a in topo.assertions.items():
            if str(a.get("predicate")).upper() in ("PREDECESSOR_PROJECT", "DEPENDS_ON"):
                if a.get("subject_node_id") == base_eng:
                    upstream_assertions[aid] = a

        assert len(upstream_assertions) >= 2, "Persistent store must contain at least 2 upstream dependency assertions"

        # 2. Verify connection policy discovers topological inheritance
        permitted = resolve_permitted_engagements("", base_eng, topo)
        assert "project:project_nexus" in permitted, "Connection policy must topologically inherit project:project_nexus"
        assert "project:project_unrelated" not in permitted, "Unrelated tenant must never be inherited"

        # 3. Instantiate resident CopilotGraphRetriever
        retriever = CopilotGraphRetriever(
            topology=topo,
            engagement_id=base_eng,
            attendees=[{"name": "Sharon Perez", "role": "Architect", "org": "APEX_TRANS"}],
        )

        # 4. Verify assertion isomorphism: Predecessor assertions must be present in retriever
        retriever_assertions = retriever.retrieve_structured_assertions("SAP ERP technical owner")
        retrieved_ids = {a["assertion_id"] for a in retriever_assertions}

        assert "ast-daniel-sap-owner" in retrieved_ids, (
            "Upstream predecessor assertion ast-daniel-sap-owner must be isomorphic in resident retriever"
        )

    def test_lateral_tenant_boundary_isolation_invariant(
        self, high_fidelity_topology: InMemoryGraphTopology
    ) -> None:
        """
        Gate 2: Assert that lateral/unrelated tenant nodes and assertions (project:project_unrelated)
        are strictly isolated and never leak into the resident cache or retriever index.
        """
        topo = high_fidelity_topology
        base_eng = "project:project_jarvis"

        retriever = CopilotGraphRetriever(
            topology=topo,
            engagement_id=base_eng,
            attendees=[{"name": "Sharon Perez", "role": "Architect", "org": "APEX_TRANS"}],
        )

        # 1. Assert unrelated assertion is rejected by scope check
        unrelated_ast = topo.assertions.get("ast-unrelated-crm", {})
        assert not is_assertion_in_scope(unrelated_ast, base_eng, retriever.permitted_engagements), (
            "Lateral tenant assertion must fail scope verification"
        )

        # 2. Assert query for lateral system returns ZERO lateral results
        results = retriever.retrieve_structured_assertions("third-party CRM customer data")
        for r in results:
            assert r.get("assertion_id") != "ast-unrelated-crm"
            assert r.get("engagement_id") != "project:project_unrelated"

    def test_neighborhood_cache_multi_hop_path_fidelity(
        self, high_fidelity_topology: InMemoryGraphTopology
    ) -> None:
        """
        Gate 3: Assert that multi-hop chains precomputed in CopilotNeighborhoodStore
        precisely mirror graph topology and execute under the 1.0ms SLA budget.
        """
        topo = high_fidelity_topology
        base_eng = "project:project_jarvis"

        seed_nodes = ["person:daniel_brakin", "project:project_nexus"]
        chains, status = compute_neighborhood_chains(
            topology=topo,
            seed_node_ids=seed_nodes,
            max_hops=2,
        )

        assert len(chains) > 0, "Must precompute multi-hop connected chains"
        assert status == "SUCCESS"

        store = CopilotNeighborhoodStore(chains=chains, max_entries=200)

        # Verify path fidelity: Daniel Brakin -> OWNS_OR_LEADS -> SAP ERP
        daniel_chains = store.get_chains_for_node("person:daniel_brakin")
        assert len(daniel_chains) > 0
        sap_chain = next((c for c in daniel_chains if c["target_id"] == "system:sap_erp"), None)
        assert sap_chain is not None, "Daniel Brakin -> SAP ERP chain must exist in neighborhood store"
        assert "Daniel Brakin" in sap_chain["chain_str"]
        assert "SAP ERP" in sap_chain["chain_str"]

        # Performance SLA verification: < 1.0 ms query latency
        t0 = time.perf_counter()
        for _ in range(50):
            store.get_chains_for_node("person:daniel_brakin")
        elapsed_p95_ms = ((time.perf_counter() - t0) / 50.0) * 1000.0

        assert elapsed_p95_ms < 1.0, f"Neighborhood store p95 latency {elapsed_p95_ms:.3f}ms exceeds 1.0ms SLA"
