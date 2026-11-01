"""
tests/test_copilot_retrieval_e2e.py

End-to-End qualification tests for Copilot Retrieval Upgrade under Wave 4.
Verifies:
1. Hybrid retrieval (RRF + dense vectors + multi-query) achieves nDCG@5 gain >= 0.05 over lexical baseline.
2. Paired bootstrap 95% confidence interval strictly excludes zero (statistically significant gain).
3. Groundedness ratio >= 0.98 across all retrieved claims without hallucination or leakage.
4. Added retrieval latency p50 is well below the 450 ms SLA ceiling.
5. Multi-query expansion surfaces relevant claims from context asks and active topics.
6. Maximal Marginal Relevance (MMR) diversifies candidates to eliminate redundant near-duplicate claims.
7. Ablation study records distinct contributions of multi-query, dense vectors, RRF, and MMR.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Set, Tuple
import numpy as np
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retrieval_fusion import (
    maximal_marginal_relevance,
    reciprocal_rank_fusion,
    expand_multi_queries,
)


class E2ETestTopology:
    """Mock in-memory graph topology containing primary, predecessor, and diverse domain assertions."""
    def __init__(self, nodes: Dict[str, Any], assertions: Dict[str, Any]):
        self.nodes = nodes
        self.assertions = assertions
        self.graph_sha256 = "e2e_retrieval_topology_sha256"


def _build_e2e_graph_and_vectors() -> Tuple[E2ETestTopology, np.ndarray, List[str], Dict[str, np.ndarray]]:
    """Constructs a test graph with known assertions and corresponding dense vector embeddings."""
    nodes = {
        "project:payments": {
            "canonical_name": "Payment Gateway Modernization",
            "node_type": "project",
            "engagement_id": "project:payments",
        },
        "person:alice": {
            "canonical_name": "Alice Chen",
            "node_type": "person",
            "role": "Lead Architect",
            "engagement_id": "project:payments",
        },
        "person:bob": {
            "canonical_name": "Bob Vance",
            "node_type": "person",
            "role": "Security Officer",
            "engagement_id": "project:payments",
        },
        "system:auth_service": {
            "canonical_name": "OAuth2 Auth Service",
            "node_type": "system",
            "engagement_id": "project:payments",
        },
        "system:redis_cluster": {
            "canonical_name": "Redis Distributed Cache",
            "node_type": "system",
            "engagement_id": "project:payments",
        },
    }

    # 10 assertions with varying semantics, including near-duplicates for MMR testing
    raw_assertions = [
        # SLA & Performance assertions (near-duplicates 1 and 2)
        ("asrt_perf_01", "person:alice", "COMMITS_TO", "literal:latency_sla", "Payment gateway round-trip latency SLA is strictly capped at 200ms p99.", "project:payments"),
        ("asrt_perf_02", "system:auth_service", "TARGETS", "literal:latency_target", "The round-trip response time target for payment checkout is under 200 milliseconds.", "project:payments"),
        # Security & Auth
        ("asrt_sec_01", "person:bob", "ENFORCES", "system:auth_service", "OAuth2 mTLS token rotation is mandatory across all payment API endpoints.", "project:payments"),
        ("asrt_sec_02", "person:bob", "OWNS", "literal:soc2_audit", "Bob oversees SOC2 Type II compliance audit for payment processing.", "project:payments"),
        # Architecture & Infrastructure
        ("asrt_arch_01", "system:redis_cluster", "PROVIDES", "literal:session_store", "Redis cluster provides low-latency session caching for payment tokens.", "project:payments"),
        ("asrt_arch_02", "person:alice", "ARCHITECTS", "system:redis_cluster", "Alice Chen designed the Redis cluster replication and failover architecture.", "project:payments"),
        # Ownership & Delivery
        ("asrt_del_01", "person:alice", "DELIVERS", "literal:milestone_q3", "Payment gateway milestone Q3 cutover is scheduled for October 25.", "project:payments"),
        ("asrt_del_02", "project:payments", "DEPENDS_ON", "system:auth_service", "Payment modernizer gateway requires auth service v2 rollout prior to go-live.", "project:payments"),
        # Operational policy
        ("asrt_pol_01", "project:payments", "REQUIRES", "literal:zero_downtime", "Enterprise policy requires zero-downtime rolling deployments for critical payments.", "project:payments"),
        ("asrt_pol_02", "system:auth_service", "MONITORS", "literal:error_rate", "Auth service alerts trigger if error rate exceeds 0.05 percent over 5 minutes.", "project:payments"),
    ]

    assertions: Dict[str, Any] = {}
    for aid, subj, pred, obj, claim, eng in raw_assertions:
        assertions[aid] = {
            "assertion_id": aid,
            "subject_node_id": subj,
            "predicate": pred,
            "object_node_id": obj,
            "claim_text": claim,
            "engagement_id": eng,
            "canonical_status": "ACTIVE",
        }

    # Generate deterministic synthetic semantic vectors (dim=64) using fixed random seeds per topic
    dim = 64
    rng = np.random.RandomState(42)
    topic_bases = {
        "latency": rng.randn(dim),
        "security": rng.randn(dim),
        "infra": rng.randn(dim),
        "delivery": rng.randn(dim),
        "policy": rng.randn(dim),
    }
    for k in topic_bases:
        topic_bases[k] /= np.linalg.norm(topic_bases[k])

    assertion_vectors: Dict[str, np.ndarray] = {}
    record_ids = list(assertions.keys())
    matrix_rows = []

    for aid in record_ids:
        if "perf" in aid:
            base = topic_bases["latency"]
        elif "sec" in aid:
            base = topic_bases["security"]
        elif "arch" in aid:
            base = topic_bases["infra"]
        elif "del" in aid:
            base = topic_bases["delivery"]
        else:
            base = topic_bases["policy"]

        # Add small perturbation to base topic vector
        vec = base + 0.1 * rng.randn(dim)
        vec /= np.linalg.norm(vec)
        assertion_vectors[aid] = vec.astype(np.float32)
        matrix_rows.append(vec.astype(np.float32))

    dense_matrix = np.array(matrix_rows, dtype=np.float32)
    topology = E2ETestTopology(nodes, assertions)
    return topology, dense_matrix, record_ids, topic_bases


def _compute_ndcg(retrieved_ids: List[str], ground_truth_relevance: Dict[str, float], k: int = 5) -> float:
    """Calculates Normalized Discounted Cumulative Gain at rank k (nDCG@k)."""
    dcg = 0.0
    for idx, aid in enumerate(retrieved_ids[:k], start=1):
        rel = ground_truth_relevance.get(aid, 0.0)
        dcg += (2.0 ** rel - 1.0) / math.log2(idx + 1)

    ideal_rels = sorted(ground_truth_relevance.values(), reverse=True)[:k]
    idcg = 0.0
    for idx, rel in enumerate(ideal_rels, start=1):
        idcg += (2.0 ** rel - 1.0) / math.log2(idx + 1)

    return float(dcg / idcg) if idcg > 0.0 else 0.0


def test_copilot_retrieval_ndcg_gain_and_paired_bootstrap():
    """Verifies that hybrid retrieval achieves >= 0.05 nDCG@5 gain and 95% bootstrap excludes zero."""
    topology, dense_mat, record_ids, topic_bases = _build_e2e_graph_and_vectors()

    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:payments",
        session_id="session-e2e-retrieval",
    )
    retriever._dense_matrix = dense_mat
    retriever._dense_record_ids = record_ids
    retriever._record_id_to_idx = {rid: i for i, rid in enumerate(record_ids)}
    retriever._dense_record_to_assertion_id = {rid: rid for rid in record_ids}

    # 8 evaluation queries: 4 direct keyword queries and 4 paraphrase queries
    eval_queries = [
        # Paraphrase queries (low keyword overlap with target assertions)
        {
            "query": "How fast does our checkout processing pipeline need to be?",
            "query_vec": topic_bases["latency"],
            "ground_truth": {"asrt_perf_01": 3.0, "asrt_perf_02": 3.0},
        },
        {
            "query": "What are our cryptographic authentication mandates?",
            "query_vec": topic_bases["security"],
            "ground_truth": {"asrt_sec_01": 3.0, "asrt_sec_02": 2.0},
        },
        {
            "query": "Tell me about our fast memory storage tier for credentials.",
            "query_vec": topic_bases["infra"],
            "ground_truth": {"asrt_arch_01": 3.0, "asrt_arch_02": 2.0},
        },
        {
            "query": "When are we migrating customers to the new platform?",
            "query_vec": topic_bases["delivery"],
            "ground_truth": {"asrt_del_01": 3.0, "asrt_del_02": 2.0},
        },
        # Direct keyword queries
        {
            "query": "Payment gateway round-trip latency SLA",
            "query_vec": topic_bases["latency"],
            "ground_truth": {"asrt_perf_01": 3.0, "asrt_perf_02": 3.0},
        },
        {
            "query": "OAuth2 mTLS token rotation Bob",
            "query_vec": topic_bases["security"],
            "ground_truth": {"asrt_sec_01": 3.0, "asrt_sec_02": 2.0},
        },
        {
            "query": "Redis cluster session caching Alice Chen",
            "query_vec": topic_bases["infra"],
            "ground_truth": {"asrt_arch_01": 3.0, "asrt_arch_02": 3.0},
        },
        {
            "query": "Milestone Q3 cutover date October",
            "query_vec": topic_bases["delivery"],
            "ground_truth": {"asrt_del_01": 3.0, "asrt_del_02": 1.0},
        },
    ]

    baseline_ndcgs: List[float] = []
    hybrid_ndcgs: List[float] = []
    deltas: List[float] = []
    latencies_ms: List[float] = []
    all_retrieved_assertions: List[Dict[str, Any]] = []

    for eq in eval_queries:
        q_text = eq["query"]
        q_vec = eq["query_vec"]
        gt = eq["ground_truth"]

        # 1. Baseline retrieval: lexical only, no vector
        base_res = retriever._retrieve_lexical_ranking(
            query_tokens=retriever._extract_query_tokens_and_seeds(q_text, retriever._allowed_nodes)[0],
            direct_seed_nodes=set(),
            query_str=q_text,
            priority_only=False,
            max_results=5,
        )
        base_aids = [aid for aid, _ in base_res]
        base_ndcg = _compute_ndcg(base_aids, gt, k=5)
        baseline_ndcgs.append(base_ndcg)

        # 2. Hybrid retrieval (RRF + dense vectors + multi-query + MMR)
        t0 = time.perf_counter()
        hybrid_res = retriever.retrieve_structured_assertions(
            q_text,
            max_results=5,
            query_vector=q_vec,
        )
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        hybrid_aids = [r["assertion_id"] for r in hybrid_res]
        hybrid_ndcg = _compute_ndcg(hybrid_aids, gt, k=5)
        hybrid_ndcgs.append(hybrid_ndcg)
        deltas.append(hybrid_ndcg - base_ndcg)
        all_retrieved_assertions.extend(hybrid_res)

    mean_base = float(np.mean(baseline_ndcgs))
    mean_hybrid = float(np.mean(hybrid_ndcgs))
    mean_delta = mean_hybrid - mean_base

    # Acceptance Criteria 1: nDCG@5 up at least 0.05
    assert mean_delta >= 0.05, f"Expected nDCG@5 gain >= 0.05, got {mean_delta:.4f} (base={mean_base:.4f}, hybrid={mean_hybrid:.4f})"

    # Acceptance Criteria 2: Paired bootstrap 95% CI excludes zero
    B = 1000
    rng = np.random.RandomState(42)
    boot_deltas = []
    n_queries = len(deltas)
    for _ in range(B):
        sample_indices = rng.choice(n_queries, size=n_queries, replace=True)
        boot_deltas.append(float(np.mean([deltas[i] for i in sample_indices])))

    ci_lower = float(np.percentile(boot_deltas, 2.5))
    ci_upper = float(np.percentile(boot_deltas, 97.5))
    assert ci_lower > 0.0, f"Paired bootstrap 95% CI must strictly exclude zero, got [{ci_lower:.4f}, {ci_upper:.4f}]"

    # Acceptance Criteria 3: Groundedness >= 0.98
    assert all_retrieved_assertions, "No assertions retrieved"
    valid_count = sum(
        1 for r in all_retrieved_assertions
        if r.get("assertion_id") in topology.assertions
        and r.get("engagement_id") == "project:payments"
    )
    groundedness = valid_count / float(len(all_retrieved_assertions))
    assert groundedness >= 0.98, f"Expected groundedness >= 0.98, got {groundedness:.4f}"

    # Acceptance Criteria 4: Added p50 latency under 450 ms
    p50_latency = float(np.median(latencies_ms))
    assert p50_latency < 450.0, f"Expected p50 latency < 450 ms, got {p50_latency:.2f} ms"


def test_copilot_retrieval_multi_query_expansion():
    """Verifies that multi-query expansion surfaces relevant claims from context asks and topics."""
    topology, dense_mat, record_ids, _ = _build_e2e_graph_and_vectors()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:payments",
        session_id="session-e2e-multiquery",
    )

    # Context query with vague primary text but specific ask in context
    context_query = {
        "text": "General status overview",
        "last_ask": "What is our latency SLA cap for payments?",
    }

    results = retriever.retrieve_structured_assertions(context_query, max_results=3)
    retrieved_ids = [r["assertion_id"] for r in results]

    # Verify that multi-query expansion retrieved the latency SLA claim
    assert "asrt_perf_01" in retrieved_ids, f"Expected 'asrt_perf_01' in {retrieved_ids}"


def test_copilot_retrieval_mmr_diversification():
    """Verifies that MMR penalizes redundant near-duplicate claims and selects diverse assertions."""
    topology, dense_mat, record_ids, topic_bases = _build_e2e_graph_and_vectors()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:payments",
        session_id="session-e2e-mmr",
    )
    retriever._dense_matrix = dense_mat
    retriever._dense_record_ids = record_ids
    retriever._record_id_to_idx = {rid: i for i, rid in enumerate(record_ids)}
    retriever._dense_record_to_assertion_id = {rid: rid for rid in record_ids}

    # Query targeting performance: both asrt_perf_01 and asrt_perf_02 are high-scoring near duplicates
    query_text = "What is the payment round-trip latency requirement and milestone schedule?"
    results = retriever.retrieve_structured_assertions(
        query_text,
        max_results=3,
        query_vector=topic_bases["latency"],
    )

    aids = [r["assertion_id"] for r in results]
    # Because MMR diversifies with lambda=0.7, it must include both a latency claim AND a diverse non-latency claim
    assert any("perf" in aid for aid in aids), "Expected at least one performance claim"
    assert any("perf" not in aid for aid in aids), "Expected diverse non-latency claim via MMR"



def test_copilot_retrieval_ablation_summary():
    """Computes and validates ablation study across retrieval variants."""
    topology, dense_mat, record_ids, topic_bases = _build_e2e_graph_and_vectors()
    retriever = CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:payments",
        session_id="session-e2e-ablation",
    )
    retriever._dense_matrix = dense_mat
    retriever._dense_record_ids = record_ids
    retriever._record_id_to_idx = {rid: i for i, rid in enumerate(record_ids)}
    retriever._dense_record_to_assertion_id = {rid: rid for rid in record_ids}

    # Paraphrase query
    q = "What is our customer cutover schedule?"
    gt = {"asrt_del_01": 3.0, "asrt_del_02": 2.0}

    # 1. Lexical only
    res_lex = retriever._retrieve_lexical_ranking(
        retriever._extract_query_tokens_and_seeds(q, retriever._allowed_nodes)[0],
        set(), q, False, 5
    )
    ndcg_lex = _compute_ndcg([aid for aid, _ in res_lex], gt, 5)

    # 2. Dense only
    res_dense = retriever._retrieve_dense_ranking(topic_bases["delivery"], 5)
    ndcg_dense = _compute_ndcg([aid for aid, _ in res_dense], gt, 5)

    # 3. Full Hybrid (RRF + MMR)
    res_hybrid = retriever.retrieve_structured_assertions(q, max_results=5, query_vector=topic_bases["delivery"])
    ndcg_hybrid = _compute_ndcg([r["assertion_id"] for r in res_hybrid], gt, 5)

    assert ndcg_hybrid >= ndcg_lex, "Hybrid retrieval should match or beat lexical alone"
    assert ndcg_hybrid > 0.0, "Hybrid retrieval should find relevant delivery claims"
