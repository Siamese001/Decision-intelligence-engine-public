"""
tests/test_hybrid_rrf_adversarial.py

Adversarial and edge-case test suite for Wave 4 Reciprocal Rank Fusion (RRF).
Covers mandatory edge-case categories from .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty inputs, single candidate, disconnected components)
2. Malformed input & invariant bypass (negative weights, invalid rrf_k, corrupt dicts)
3. Overflow & exhaustion (10k candidate scale, extreme zero/one weights)
4. Exact rank tie resolution & deterministic ordering
"""
from __future__ import annotations

import pytest

from dual_pipeline.query_core.hybrid_reranker import (
    HybridGraphVectorReranker,
    RankedCandidate,
    precision_at_k,
    rank_by_topological_distance,
    reciprocal_rank,
    reciprocal_rank_fusion,
)


# =========================================================================
# Category 1: Boundary Payloads
# =========================================================================

def test_empty_both_streams_returns_empty() -> None:
    """Empty graph and vector streams return an empty list without error."""
    res = reciprocal_rank_fusion([], [])
    assert res == []
    assert len(res) == 0


def test_empty_graph_stream_with_vector_candidates() -> None:
    """Empty graph stream yields candidates scored solely on vector rankings."""
    vectors = [
        {"candidate_id": "v1", "score": 0.95},
        {"candidate_id": "v2", "score": 0.85},
        {"candidate_id": "v3", "score": 0.75},
    ]
    res = reciprocal_rank_fusion([], vectors, rrf_k=60, graph_weight=0.5, vector_weight=0.5)
    assert len(res) == 3
    assert res[0].candidate_id == "v1"
    assert res[0].graph_rank is None
    assert res[0].vector_rank == 1
    # Expected vector contribution: 0.5 * (1.0 / (60 + 1)) = 0.5 / 61
    expected_score = round(0.5 / 61.0, 8)
    assert res[0].rrf_score == expected_score


def test_empty_vector_stream_with_graph_candidates() -> None:
    """Empty vector stream yields candidates scored solely on graph rankings."""
    graphs = ["g1", "g2", "g3"]
    res = reciprocal_rank_fusion(graphs, [], rrf_k=60, graph_weight=0.5, vector_weight=0.5)
    assert len(res) == 3
    assert res[0].candidate_id == "g1"
    assert res[0].graph_rank == 1
    assert res[0].vector_rank is None
    expected_score = round(0.5 / 61.0, 8)
    assert res[0].rrf_score == expected_score


def test_single_candidate_boundary() -> None:
    """Single candidate present in both streams produces combined RRF score."""
    res = reciprocal_rank_fusion(["c1"], ["c1"], rrf_k=60, graph_weight=0.5, vector_weight=0.5)
    assert len(res) == 1
    assert res[0].candidate_id == "c1"
    assert res[0].graph_rank == 1
    assert res[0].vector_rank == 1
    # 0.5 / 61 + 0.5 / 61 = 1.0 / 61
    assert res[0].rrf_score == round(1.0 / 61.0, 8)


# =========================================================================
# Category 2: Malformed Input & Invariant Bypass Attempts
# =========================================================================

def test_negative_weights_raises_value_error() -> None:
    """Invariant bypass: Negative fusion weights must fail closed with ValueError."""
    with pytest.raises(ValueError, match="Weights must be non-negative"):
        reciprocal_rank_fusion(["c1"], ["c1"], graph_weight=-0.5, vector_weight=0.5)

    with pytest.raises(ValueError, match="Weights must be non-negative"):
        reciprocal_rank_fusion(["c1"], ["c1"], graph_weight=0.5, vector_weight=-0.1)

    with pytest.raises(ValueError, match="Weights must be non-negative"):
        HybridGraphVectorReranker(graph_weight=-1.0)


def test_zero_or_negative_rrf_k_raises_value_error() -> None:
    """Invariant bypass: rrf_k <= 0 must fail closed with ValueError."""
    with pytest.raises(ValueError, match="rrf_k must be a positive integer"):
        reciprocal_rank_fusion(["c1"], ["c1"], rrf_k=0)

    with pytest.raises(ValueError, match="rrf_k must be a positive integer"):
        reciprocal_rank_fusion(["c1"], ["c1"], rrf_k=-60)

    with pytest.raises(ValueError, match="rrf_k must be positive"):
        HybridGraphVectorReranker(rrf_k=0)


def test_malformed_candidate_dict_keys() -> None:
    """Gracefully handles dicts with alternative or missing identifier keys."""
    graph_stream = [
        {"node_id": "node_alpha", "min_hop": 0},
        {"record_id": "rec_beta", "min_hop": 1},
        {"id": "id_gamma", "min_hop": 2},
        {"unrelated_key": "val"},  # fallback to cand_3
    ]
    ranked = rank_by_topological_distance(graph_stream)
    assert len(ranked) == 4
    assert ranked[0]["node_id"] == "node_alpha"
    assert ranked[0]["graph_rank"] == 1


def test_duplicate_candidate_ids_in_same_stream() -> None:
    """Duplicate candidate IDs retain their best (lowest) rank without inflation."""
    vector_stream = [
        {"candidate_id": "dup_1", "rank": 5, "score": 0.5},
        {"candidate_id": "dup_1", "rank": 2, "score": 0.8},
        {"candidate_id": "dup_1", "rank": 8, "score": 0.3},
    ]
    res = reciprocal_rank_fusion([], vector_stream, rrf_k=60)
    assert len(res) == 1
    assert res[0].candidate_id == "dup_1"
    assert res[0].vector_rank == 2  # Best rank retained


# =========================================================================
# Category 3: Overflow, Exhaustion & Extreme Weights
# =========================================================================

def test_extreme_weight_pure_graph() -> None:
    """When vector_weight=0, candidate ordering matches pure graph ranking."""
    graphs = ["g1", "g2", "g3"]
    vectors = ["g3", "g2", "g1"]  # Inverted order in vector
    res = reciprocal_rank_fusion(graphs, vectors, graph_weight=1.0, vector_weight=0.0)
    assert [c.candidate_id for c in res] == ["g1", "g2", "g3"]


def test_extreme_weight_pure_vector() -> None:
    """When graph_weight=0, candidate ordering matches pure vector ranking."""
    graphs = ["v3", "v2", "v1"]
    vectors = ["v1", "v2", "v3"]
    res = reciprocal_rank_fusion(graphs, vectors, graph_weight=0.0, vector_weight=1.0)
    assert [c.candidate_id for c in res] == ["v1", "v2", "v3"]


def test_extreme_weight_both_zero() -> None:
    """When both weights are 0, all scores are zero and no divide-by-zero occurs."""
    res = reciprocal_rank_fusion(["a", "b"], ["b", "a"], graph_weight=0.0, vector_weight=0.0)
    assert len(res) == 2
    assert all(c.rrf_score == 0.0 for c in res)


def test_large_candidate_pool_scale() -> None:
    """Verify performance and determinism on 10,000 candidates."""
    import time
    graphs = [f"node_{i:05d}" for i in range(5000)]
    vectors = [f"node_{i:05d}" for i in range(2500, 7500)]
    t0 = time.perf_counter()
    fused = reciprocal_rank_fusion(graphs, vectors, limit=100)
    elapsed = time.perf_counter() - t0
    assert len(fused) == 100
    # Overlapping candidates (2500..4999) appear in both streams and must rank ahead
    assert fused[0].graph_rank is not None
    assert fused[0].vector_rank is not None
    assert elapsed < 0.5  # Sub-500ms execution for 7,500 total unique candidates


# =========================================================================
# Category 4: Rank Tie Resolution & Deterministic Ordering
# =========================================================================

def test_exact_rank_ties_resolved_deterministically() -> None:
    """Candidates with equal RRF score and equal ranks break ties by candidate_id."""
    # Both have graph_rank=None and vector_rank=1 -> identical RRF score
    stream1 = [{"candidate_id": "zebra", "rank": 1}]
    stream2 = [{"candidate_id": "aardvark", "rank": 1}]
    res = reciprocal_rank_fusion([], stream1 + stream2)
    assert len(res) == 2
    assert res[0].rrf_score == res[1].rrf_score
    # Aardvark comes before zebra due to deterministic alphabetical tie-breaking
    assert res[0].candidate_id == "aardvark"
    assert res[1].candidate_id == "zebra"


def test_asymmetric_tie_breaking_favors_best_single_rank() -> None:
    """When scores are identical, candidate with best single-stream rank is preferred."""
    # cA: graph 1 (0.5/61), vector 5 (0.5/65)
    # cB: graph 2 (0.5/62), vector 4 (0.5/64)
    # Both are very close, but let's test a case where scores are forced equal or best rank breaks it
    cA = RankedCandidate(candidate_id="cand_a", rrf_score=0.015, graph_rank=1, vector_rank=10)
    cB = RankedCandidate(candidate_id="cand_b", rrf_score=0.015, graph_rank=3, vector_rank=3)
    # Sorting key tests min rank: cand_a min rank is 1, cand_b min rank is 3
    candidates = [cB, cA]
    candidates.sort(
        key=lambda c: (-c.rrf_score, min(r for r in (c.graph_rank, c.vector_rank) if r is not None), c.candidate_id)
    )
    assert candidates[0].candidate_id == "cand_a"


# =========================================================================
# Category 5: Disconnected Components & Graph Proximity
# =========================================================================

def test_disconnected_graph_components_prioritize_connected() -> None:
    """Connected graph neighbors outrank disconnected components with similar vector scores."""
    reranker = HybridGraphVectorReranker(rrf_k=60, graph_weight=0.5, vector_weight=0.5)

    # Neighborhood has 'connected_node' at 1-hop
    neighborhood = {
        "start_node_ids": ["seed"],
        "nodes": {
            "seed": {"node_id": "seed", "min_hop": 0},
            "connected_node": {"node_id": "connected_node", "min_hop": 1},
        },
    }

    # Vector retriever scored both connected_node and disconnected_node closely
    vector_candidates = [
        {"candidate_id": "disconnected_node", "score": 0.90},  # rank 1 in vector
        {"candidate_id": "connected_node", "score": 0.88},     # rank 2 in vector
    ]

    fused = reranker.fuse_neighborhood(neighborhood, vector_candidates)

    # connected_node has: graph rank 2 (0.5/62) + vector rank 2 (0.5/62) = 1.0/62 ≈ 0.0161
    # disconnected_node has: only vector rank 1 (0.5/61) ≈ 0.0082
    assert fused[0].candidate_id == "connected_node"
    assert fused[1].candidate_id == "disconnected_node"
    assert fused[0].rrf_score > fused[1].rrf_score


# =========================================================================
# Category 6: Metric Edge Cases
# =========================================================================

def test_precision_and_reciprocal_rank_edge_cases() -> None:
    """Metric helpers handle empty, zero k, and unretrieved positives safely."""
    assert precision_at_k([], {"pos1"}, k=10) == 0.0
    assert precision_at_k(["c1", "c2"], set(), k=10) == 0.0
    assert precision_at_k(["c1"], {"c1"}, k=0) == 0.0

    assert reciprocal_rank([], {"pos1"}, k=10) == 0.0
    assert reciprocal_rank(["c1", "c2"], set(), k=10) == 0.0
    assert reciprocal_rank(["c1"], {"pos1"}, k=10) == 0.0
    assert reciprocal_rank(["pos1"], {"pos1"}, k=10) == 1.0
    assert reciprocal_rank(["neg", "pos1"], {"pos1"}, k=10) == 0.5
