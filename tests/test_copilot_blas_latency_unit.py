"""
tests/test_copilot_blas_latency_unit.py

Wave 2 unit tests:
1. Vectorized single-pass BLAS matrix cosine similarity execution latency (< 5.0ms p95).
2. Continuous engagement affinity bonus (+0.25 for active engagement, 1.0 for cross-project, 0.0 for C-19).
3. Live sidecar vector loading and real-world candidate scoring.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    calculate_engagement_affinity,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


class MockTopology:
    def __init__(self, nodes: Dict[str, Any] = None, assertions: Dict[str, Any] = None):
        self.nodes = nodes or {}
        self.assertions = assertions or {}
        self.graph_sha256 = "mock_topology_sha256"


def test_blas_dot_product_latency_sub_5ms():
    """Verifies that vectorized BLAS scan across 2,865 dense vectors executes in < 5.0ms p95."""
    rng = np.random.RandomState(42)
    num_vectors = 2865
    dims = 1024
    num_queries = 100

    raw_mat = rng.randn(num_vectors, dims).astype(np.float32)
    norms = np.linalg.norm(raw_mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat_norm = raw_mat / norms

    record_ids = [f"asrt-rec-{i:05d}" for i in range(num_vectors)]

    retriever = CopilotGraphRetriever(topology=MockTopology())
    retriever._dense_matrix = mat_norm
    retriever._dense_record_ids = record_ids
    retriever._record_id_to_idx = {rid: i for i, rid in enumerate(record_ids)}

    queries = rng.randn(num_queries, dims).astype(np.float32)
    latencies_ms = []

    for i in range(num_queries):
        q = queries[i]
        t0 = time.perf_counter()
        top_k = retriever.score_dense_assertions(q, top_k=50)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        assert len(top_k) == 50
        assert top_k[0][1] >= top_k[-1][1]

    p95_latency = float(np.percentile(latencies_ms, 95))
    p50_latency = float(np.percentile(latencies_ms, 50))
    max_latency = float(max(latencies_ms))

    print(
        f"\n[BLAS Benchmark] 2,865 vectors x 100 queries: p50={p50_latency:.4f}ms, "
        f"p95={p95_latency:.4f}ms, max={max_latency:.4f}ms"
    )
    assert p95_latency < 5.0, f"p95 latency {p95_latency:.4f}ms exceeded 5.0ms ceiling"


def test_calculate_engagement_affinity_bonus_and_firewall():
    """Verifies affinity multiplier behavior: active=+0.25 bonus, cross=1.0x, C-19=0.0x."""
    bound = "project:project_nexus"
    permitted = {"project:nea"}

    # 1. Active engagement match: 1.25x
    active_item = {
        "assertion_id": "a1",
        "engagement_id": "project:project_nexus",
        "claim_text": "Active Nexus workstream deployment plan",
    }
    assert calculate_engagement_affinity(active_item, bound, permitted) == 1.25

    # 2. Scope text match: 1.25x
    scope_text_item = {
        "assertion_id": "a2",
        "scope_text": "project_nexus architecture rollout",
        "claim_text": "Nexus infrastructure scaling plan",
    }
    assert calculate_engagement_affinity(scope_text_item, bound, permitted) == 1.25

    # 3. Enterprise policy: 1.15x
    policy_item = {
        "assertion_id": "a3",
        "is_enterprise_policy": True,
        "claim_text": "Global operational security standard for all services",
    }
    assert calculate_engagement_affinity(policy_item, bound, permitted) == 1.15

    # 4. Permitted alias/topological predecessor (NEA): 1.10x
    permitted_item = {
        "assertion_id": "a4",
        "engagement_id": "project:nea",
        "claim_text": "NEA predecessor telemetry schema",
    }
    assert calculate_engagement_affinity(permitted_item, bound, permitted) == 1.10

    # 5. Cross-project (Conduit): 1.0x (retained, never excluded!)
    cross_item = {
        "assertion_id": "a5",
        "engagement_id": "project:conduit",
        "claim_text": "harper Hoffman leads Conduit unified tooling umbrella",
    }
    assert calculate_engagement_affinity(cross_item, bound, permitted) == 1.0

    # 6. Rule C-19 Career Firewall: Quarantined item must fail closed to 0.0x
    c19_quarantined = {
        "assertion_id": "a6",
        "engagement_id": "project:project_nexus",
        "is_quarantined": True,
        "claim_text": "Confidential advisory note",
    }
    assert calculate_engagement_affinity(c19_quarantined, bound, permitted) == 0.0

    # 7. Rule C-19 Marker in text: Must fail closed to 0.0x
    c19_marker_item = {
        "assertion_id": "a7",
        "engagement_id": "project:project_nexus",
        "claim_text": "Executive compensation package and executive compensation band roadmap",
    }
    assert calculate_engagement_affinity(c19_marker_item, bound, permitted) == 0.0


def test_retriever_score_assertion_incorporates_affinity():
    """Verifies that CopilotGraphRetriever._score_assertion modulates score by affinity."""
    nodes = {
        "nexus_node": {"canonical_name": "Nexus Node", "display_name": "Nexus Node"},
        "conduit_node": {"canonical_name": "Conduit Node", "display_name": "Conduit Node"},
    }
    topo = MockTopology(nodes=nodes)
    retriever = CopilotGraphRetriever(
        topology=topo,
        engagement_id="project:project_nexus",
    )

    query_tokens = {"nexus", "conduit", "deployment"}
    retriever._token_idf = {"nexus": 2.0, "conduit": 2.0, "deployment": 1.5}

    active_asrt = {
        "assertion_id": "asrt_nexus_1",
        "engagement_id": "project:project_nexus",
        "subject_node_id": "nexus_node",
        "predicate": "DEPLOYED_TO",
        "claim_text": "Nexus deployment status active",
        "provenance_scope": "PRIMARY",
        "is_primary": True,
    }
    cross_asrt = {
        "assertion_id": "asrt_conduit_1",
        "engagement_id": "project:conduit",
        "subject_node_id": "conduit_node",
        "predicate": "OPERATES",
        "claim_text": "Conduit deployment status active",
        "provenance_scope": "CROSS",
        "is_primary": False,
    }
    c19_asrt = {
        "assertion_id": "asrt_c19_1",
        "engagement_id": "project:project_nexus",
        "subject_node_id": "nexus_node",
        "predicate": "CONTAINS",
        "claim_text": "Personal promotion salary leverage executive compensation band",
        "is_quarantined": True,
    }

    score_active = retriever._score_assertion(active_asrt, query_tokens, "nexus deployment")
    score_cross = retriever._score_assertion(cross_asrt, query_tokens, "conduit deployment")
    score_c19 = retriever._score_assertion(c19_asrt, query_tokens, "salary leverage")

    assert score_active > 0.0
    assert score_cross > 0.0
    # Active gets 1.25x affinity bonus; cross gets 1.0x affinity
    assert score_active > score_cross
    # C-19 quarantined assertion must score strictly 0.0
    assert score_c19 == 0.0


def test_live_sidecar_vector_caching_and_scoring():
    """Verifies that active generation sidecar vectors load correctly into memory."""
    repo_root = Path(__file__).resolve().parent.parent
    sidecar_path = repo_root / ".local" / "dual_pipeline" / "generations" / "generation-4294649f027622625be85a9ec510" / "sidecar.sqlite"

    if not sidecar_path.is_file():
        pytest.skip(f"Sidecar not present at {sidecar_path}")

    retriever = CopilotGraphRetriever(topology=MockTopology(), sidecar_path=sidecar_path)

    assert retriever._dense_matrix is not None
    assert len(retriever._dense_record_ids) > 0
    assert retriever._dense_matrix.shape[0] == len(retriever._dense_record_ids)
    assert retriever._dense_matrix.shape[1] == 1024

    # Run arbitrary query vector
    q_vec = np.ones(1024, dtype=np.float32)
    t0 = time.perf_counter()
    top_results = retriever.score_dense_assertions(q_vec, top_k=20)
    dur_ms = (time.perf_counter() - t0) * 1000.0

    assert len(top_results) == 20
    assert dur_ms < 5.0
    for rid, sim in top_results:
        assert isinstance(rid, str)
        assert -1.01 <= sim <= 1.01
