"""
tests/test_copilot_retrieval_adversarial.py

Adversarial and boundary qualification tests for Copilot Retrieval Upgrade under Wave 4.
Verifies:
1. Complete elimination of no-overlap 1.0 bug: zero token overlap queries return 0 results with 0.0 score.
2. Cross-engagement isolation: unpermitted foreign tenant assertions are never leaked, even with 1-hop expansion.
3. Rule C-19 career firewall: quarantined claims fail closed with 0.0 score even with high dense vector similarity.
4. Graceful fallback without sidecar / query vector: no synthetic SHA fake vectors are generated.
5. Malformed, empty, and adversarial boundary payloads handle cleanly without exception.
6. Feature flags: cross-encoder and 1-hop graph expansion default to False, and provide measured gain when enabled.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple
import numpy as np
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retrieval_fusion import (
    cross_encoder_rerank,
    expand_1hop_graph_neighbors,
)


class AdversarialTopology:
    def __init__(self, nodes: Dict[str, Any], assertions: Dict[str, Any]):
        self.nodes = nodes
        self.assertions = assertions
        self.graph_sha256 = "adv_retrieval_sha256"


def _build_adversarial_graph() -> AdversarialTopology:
    nodes = {
        "project:alpha": {
            "canonical_name": "Project Alpha",
            "node_type": "project",
            "engagement_id": "project:alpha",
        },
        "person:lead_alpha": {
            "canonical_name": "Sarah Connor",
            "node_type": "person",
            "engagement_id": "project:alpha",
        },
        "system:core_engine": {
            "canonical_name": "Core Engine",
            "node_type": "system",
            "engagement_id": "project:alpha",
        },
        # Foreign tenant: Project Beta (unpermitted)
        "project:beta": {
            "canonical_name": "Project Beta",
            "node_type": "project",
            "engagement_id": "project:beta",
        },
        "system:beta_secret_vault": {
            "canonical_name": "Beta Secret Vault",
            "node_type": "system",
            "engagement_id": "project:beta",
        },
    }

    assertions = {
        "asrt_alpha_01": {
            "assertion_id": "asrt_alpha_01",
            "subject_node_id": "person:lead_alpha",
            "predicate": "LEADS",
            "object_node_id": "project:alpha",
            "claim_text": "Sarah Connor leads core engine design for Project Alpha.",
            "engagement_id": "project:alpha",
            "canonical_status": "ACTIVE",
        },
        "asrt_alpha_02": {
            "assertion_id": "asrt_alpha_02",
            "subject_node_id": "system:core_engine",
            "predicate": "REQUIRES",
            "object_node_id": "literal:gpu_cluster",
            "claim_text": "Core Engine requires high-throughput Apple Silicon GPU cluster for local inference.",
            "engagement_id": "project:alpha",
            "canonical_status": "ACTIVE",
        },
        # 1-hop connected candidate inside alpha
        "asrt_alpha_neighbor": {
            "assertion_id": "asrt_alpha_neighbor",
            "subject_node_id": "system:core_engine",
            "predicate": "MONITORED_BY",
            "object_node_id": "literal:prom_telemetry",
            "claim_text": "Prometheus telemetry continuously monitors GPU cluster temperature and memory bandwidth.",
            "engagement_id": "project:alpha",
            "canonical_status": "ACTIVE",
        },
        # Foreign tenant assertion (MUST NEVER LEAK)
        "asrt_beta_leak_trap": {
            "assertion_id": "asrt_beta_leak_trap",
            "subject_node_id": "system:beta_secret_vault",
            "predicate": "CONTAINS",
            "object_node_id": "literal:confidential_keys",
            "claim_text": "Beta Secret Vault holds confidential client encryption keys and trading algorithms.",
            "engagement_id": "project:beta",
            "canonical_status": "ACTIVE",
        },
        # Rule C-19 Quarantined Assertion
        "asrt_c19_quarantined": {
            "assertion_id": "asrt_c19_quarantined",
            "subject_node_id": "person:lead_alpha",
            "predicate": "HAS_ADVISORY",
            "object_node_id": "literal:career_leverage",
            "claim_text": "INTERNAL_ADVISORY: Sarah Connor salary trajectory target is 350k with executive stock options.",
            "engagement_id": "project:alpha",
            "namespace": "INTERNAL_ADVISORY",
            "is_quarantined": 1,
            "stealth_score": 1.0,
            "canonical_status": "ACTIVE",
        },
    }
    return AdversarialTopology(nodes, assertions)


def test_no_overlap_query_returns_zero_score_and_empty():
    """Confirms no-overlap bug fix: queries with zero token overlap return 0 results and 0.0 score."""
    topology = _build_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:alpha",
        session_id="session-adv-001",
    )

    # Completely disjoint query vocabulary
    zero_overlap_query = "astrophysics supernova black hole event horizon"
    results = retriever.retrieve_structured_assertions(zero_overlap_query, max_results=5)
    assert results == [], f"Expected empty results for zero-overlap query, got {results}"

    # Verify score assertion explicitly returns 0.0 for zero overlap
    tokens = {"supernova", "black", "hole"}
    score = retriever._score_assertion(topology.assertions["asrt_alpha_01"], tokens, zero_overlap_query)
    assert score == 0.0, f"Expected 0.0 score for zero overlap, got {score}"


def test_cross_engagement_leakage_prevented_under_1hop_expansion():
    """Confirms cross-tenant assertions (project:beta) are strictly excluded even with 1-hop expansion."""
    topology = _build_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:alpha",
        session_id="session-adv-002",
        enable_1hop_graph_expansion=True,
    )

    # Solar / Beta nodes must not enter allowed nodes
    assert "project:beta" not in retriever._allowed_nodes
    assert "system:beta_secret_vault" not in retriever._allowed_nodes

    # Query attempting to elicit Beta vault data
    trap_query = "Where are confidential client encryption keys and trading algorithms stored in Beta vault?"
    results = retriever.retrieve_structured_assertions(trap_query, max_results=10)

    # Must contain zero results from project:beta
    for r in results:
        assert r.get("engagement_id") != "project:beta", f"Cross-tenant leak detected: {r}"
        assert "beta" not in str(r.get("assertion_id")), f"Cross-tenant leak detected: {r}"


def test_c19_career_firewall_blocks_quarantined_claims_even_with_dense_match():
    """Confirms Rule C-19 career firewall blocks quarantined claims even when dense similarity is 1.0."""
    topology = _build_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:alpha",
        session_id="session-adv-003",
    )

    # Wire a dense vector for C-19 that perfectly matches query vector
    dim = 16
    q_vec = np.ones(dim, dtype=np.float32) / np.sqrt(dim)
    retriever._dense_matrix = np.array([q_vec], dtype=np.float32)
    retriever._dense_record_ids = ["asrt_c19_quarantined"]
    retriever._record_id_to_idx = {"asrt_c19_quarantined": 0}
    retriever._dense_record_to_assertion_id = {"asrt_c19_quarantined": "asrt_c19_quarantined"}

    # Query targeting executive compensation
    query = "What is Sarah Connor salary trajectory and executive stock compensation?"
    results = retriever.retrieve_structured_assertions(query, max_results=5, query_vector=q_vec)

    # C-19 quarantined assertion must not be retrieved
    aids = [r["assertion_id"] for r in results]
    assert "asrt_c19_quarantined" not in aids, "C-19 quarantined claim leaked into copilot retrieval!"


def test_missing_sidecar_and_none_query_vector_graceful_fallback():
    """Confirms graceful fallback to lexical RRF without crashing and zero synthetic SHA fake vectors."""
    topology = _build_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:alpha",
        session_id="session-adv-004",
        sidecar_path="/tmp/non_existent_sidecar.sqlite",
    )
    # Ensure no vectors are loaded
    assert retriever._dense_matrix is None
    assert retriever._dense_record_ids == []


    # Retrieval works purely lexically without error
    results = retriever.retrieve_structured_assertions("Sarah Connor core engine", max_results=3, query_vector=None)
    assert len(results) >= 1
    assert results[0]["assertion_id"] == "asrt_alpha_01"


def test_empty_and_malformed_queries():
    """Confirms retriever handles empty, whitespace, and boundary queries safely."""
    topology = _build_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:alpha",
        session_id="session-adv-005",
    )

    assert retriever.retrieve_structured_assertions("", max_results=5) == []
    assert retriever.retrieve_structured_assertions("   ", max_results=5) == []
    assert retriever.retrieve_structured_assertions("???!!!", max_results=5) == []
    assert retriever.retrieve_structured_assertions(None, max_results=5) == []
    assert retriever.retrieve_structured_assertions({"text": ""}, max_results=5) == []


def test_feature_flags_default_false_and_measured_gain_when_enabled():
    """Confirms feature flags default to False and exhibit measured gain when enabled."""
    topology = _build_adversarial_graph()

    # 1. Defaults are False
    default_retriever = CopilotGraphRetriever(topology=topology, engagement_id="project:alpha")
    assert default_retriever.enable_cross_encoder is False
    assert default_retriever.enable_1hop_graph_expansion is False

    # 2. 1-Hop Graph Expansion provides measured gain (surfaces neighbor)
    expanded_retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:alpha",
        enable_1hop_graph_expansion=True,
    )
    # Query targets core engine GPU cluster: initial candidates contain asrt_alpha_02
    results = expanded_retriever.retrieve_structured_assertions(
        "Core Engine Apple Silicon GPU cluster",
        max_results=5,
    )
    retrieved_ids = [r["assertion_id"] for r in results]
    # In-scope 1-hop neighbor Prometheus telemetry should be discovered
    assert "asrt_alpha_neighbor" in retrieved_ids, f"Expected 1-hop neighbor in {retrieved_ids}"

    # 3. Cross-Encoder reranking provides measured gain (reorders candidates)
    def mock_cross_encoder(query: str, doc: str) -> float:
        # Give higher score if 'telemetry' is mentioned
        return 10.0 if "telemetry" in doc.lower() else 1.0

    ce_retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:alpha",
        enable_1hop_graph_expansion=True,
        enable_cross_encoder=True,
        cross_encoder_fn=mock_cross_encoder,
    )
    ce_results = ce_retriever.retrieve_structured_assertions(
        "Core Engine Apple Silicon GPU cluster",
        max_results=5,
    )
    # The telemetry assertion should now be ranked #1 by the cross encoder
    assert ce_results[0]["assertion_id"] == "asrt_alpha_neighbor"
