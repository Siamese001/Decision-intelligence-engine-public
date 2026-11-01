"""Adversarial and edge-case tests for Wave 1 retrieval metric calibration.

Covers mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty inputs, zero-positive expectations, large top_k).
2. Malformed input (duplicate IDs, non-string IDs, whitespace IDs, invalid types).
3. Invariant bypass attempt (inflating metrics via no-answer flooding, nDCG > 1.0 via duplicates).
4. Exact production regression (rank-10 0.5 -> 0.1 MRR collapse, empty expectation 1.0 award).
"""
from __future__ import annotations

import math
import pytest

from dual_pipeline.retrieval_release_core.metrics import (
    _metrics,
    _ndcg_at_k,
    _reciprocal_rank,
    standard_ndcg_at_k,
    standard_recall_at_k,
    standard_reciprocal_rank,
    validate_retrieved_ranking,
)
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, _pack
from dual_pipeline.index_core.embedding_space_contract import EmbeddingSpaceMismatch
from dual_pipeline.index_core.sidecar_validation import run_extended_sidecar_invariants
from dual_pipeline.query_router_core.isotropic_router import IsotropicBgeM3Router
from storage.connection import sqlite3
from tools.independent_standard_evaluator import IndependentStandardEvaluator
from tools.score_qrels_v2 import compute_mrr_at_k, compute_ndcg_at_k


# -----------------------------------------------------------------------------
# 1. Boundary Payload Tests
# -----------------------------------------------------------------------------

def test_adversarial_boundary_empty_and_zero_inputs() -> None:
    """Boundary payload: empty ranking and zero-length judgments must handle safely."""
    # Empty candidate ranking
    rr = standard_reciprocal_rank([], {"doc_1": 2}, top_k=10)
    assert rr == 0.0

    ndcg = standard_ndcg_at_k([], {"doc_1": 2}, top_k=10)
    assert ndcg == 0.0

    recall = standard_recall_at_k([], {"doc_1": 2}, top_k=10)
    assert recall == 0.0

    # Empty expectations in ground truth (no-answer query)
    assert standard_reciprocal_rank(["doc_1"], {}, top_k=10) is None
    assert standard_ndcg_at_k(["doc_1"], {}, top_k=10) is None
    assert standard_recall_at_k(["doc_1"], {}, top_k=10) is None

    # Oversized top_k: top_k=1000 on 2 candidates
    ndcg_large_k = standard_ndcg_at_k(["doc_1", "doc_2"], {"doc_1": 2, "doc_2": 1}, top_k=1000)
    assert ndcg_large_k == pytest.approx(1.0, abs=1e-6)


# -----------------------------------------------------------------------------
# 2. Malformed Input Tests
# -----------------------------------------------------------------------------

def test_adversarial_malformed_inputs_fail_closed() -> None:
    """Malformed input: duplicate IDs, whitespace IDs, invalid types must fail validation."""
    # Duplicate IDs
    with pytest.raises(ValueError, match="Duplicate record IDs"):
        validate_retrieved_ranking(["rec_a", "rec_b", "rec_a"])

    with pytest.raises(ValueError, match="Duplicate record IDs"):
        standard_reciprocal_rank(["rec_a", "rec_a"], {"rec_a": 1})

    with pytest.raises(ValueError, match="Duplicate record IDs"):
        standard_ndcg_at_k(["rec_a", "rec_a"], {"rec_a": 1})

    # Empty string or whitespace record ID
    with pytest.raises(ValueError, match="Invalid record_id"):
        validate_retrieved_ranking(["rec_a", "   ", "rec_b"])

    with pytest.raises(ValueError, match="Invalid record_id"):
        validate_retrieved_ranking(["rec_a", ""])

    # Non-sequence input
    with pytest.raises(TypeError, match="must be a sequence"):
        validate_retrieved_ranking(12345)  # type: ignore

    with pytest.raises(TypeError, match="must be Mapping or Set"):
        standard_reciprocal_rank(["rec_a"], 999)  # type: ignore


# -----------------------------------------------------------------------------
# 3. Invariant Bypass Attempts
# -----------------------------------------------------------------------------

def test_adversarial_bypass_no_answer_flooding_cannot_inflate_positive_metrics() -> None:
    """Invariant bypass: flooding dataset with 1,000 no-answer cases cannot inflate positive metrics."""
    # A positive case with modest quality: MRR = 0.5, nDCG = 0.6, Recall = 0.5
    positive_case = {
        "expected_record_ids": ["doc_truth"],
        "candidate_reciprocal_rank": 0.5,
        "candidate_ndcg_at_k": 0.6,
        "candidate_recall_at_k": 0.5,
    }
    baseline_agg = _metrics([positive_case])
    assert baseline_agg["mrr"] == 0.5
    assert baseline_agg["ndcg_at_k"] == 0.6
    assert baseline_agg["recall_at_k"] == 0.5

    # Attacker injects 1,000 unanswerable / negative cases
    adversarial_no_answer_flooding = [
        {
            "expected_record_ids": [],
            "candidate_reciprocal_rank": None,
            "candidate_ndcg_at_k": None,
            "candidate_recall_at_k": None,
            "candidate_first_relevant_rank": None,
            "forbidden_leakage": [],
        }
        for _ in range(1000)
    ]

    flooded_agg = _metrics([positive_case] + adversarial_no_answer_flooding)
    assert flooded_agg["case_count"] == 1001
    assert flooded_agg["positive_case_count"] == 1
    # Positive metrics MUST NOT have changed!
    assert flooded_agg["mrr"] == baseline_agg["mrr"]
    assert flooded_agg["ndcg_at_k"] == baseline_agg["ndcg_at_k"]
    assert flooded_agg["recall_at_k"] == baseline_agg["recall_at_k"]
    # Rejection rate captures the no-answer pool
    assert flooded_agg["no_answer_case_count"] == 1000
    assert flooded_agg["no_answer_rejection_rate"] == 1.0


def test_adversarial_bypass_duplicate_hits_cannot_exceed_1_ndcg() -> None:
    """Invariant bypass: duplicating the top hit cannot bypass validation to manufacture nDCG > 1.0."""
    evaluator = IndependentStandardEvaluator(top_k=5)
    fraudulent_ranking = ["top_hit", "top_hit", "top_hit", "top_hit", "top_hit"]
    truth = {"top_hit": 2}

    # Attempting to score duplicates fails closed immediately
    with pytest.raises(ValueError, match="Duplicate record IDs"):
        evaluator.ndcg_at_k(fraudulent_ranking, truth)

    with pytest.raises(ValueError, match="Duplicate record IDs"):
        standard_ndcg_at_k(fraudulent_ranking, truth)


# -----------------------------------------------------------------------------
# 4. Exact Production Regressions
# -----------------------------------------------------------------------------

def test_adversarial_regression_score_qrels_v2_rank_10_mrr_drop() -> None:
    """Exact production regression: score_qrels_v2 MRR must not drop from 0.5 to 0.1 when rank 10 improves."""
    # State 1: candidate at rank 1 is Grade 1 (relevant). Candidate at rank 10 is Grade 0.
    judgments_state_1 = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    mrr_1 = compute_mrr_at_k(judgments_state_1, k=10, relevance_threshold=1)
    assert mrr_1 == 1.0  # Rank 1 hit

    # State 2: candidate at rank 10 improves from Grade 0 to Grade 2. Rank 1 is still Grade 1.
    judgments_state_2 = [1, 0, 0, 0, 0, 0, 0, 0, 0, 2]
    mrr_2 = compute_mrr_at_k(judgments_state_2, k=10, relevance_threshold=1)
    # The regression bug caused MRR to drop to 0.1. In the fixed code, MRR is monotonically 1.0.
    assert mrr_2 == 1.0
    assert mrr_2 >= mrr_1


def test_adversarial_regression_empty_expectations_cannot_award_perfect_score() -> None:
    """Exact production regression: empty expectations must not award 1.0 to MRR or nDCG."""
    # _reciprocal_rank with empty expected returns None
    assert _reciprocal_rank(None, set()) is None
    assert _reciprocal_rank(1, set()) is None

    # _ndcg_at_k with empty expected returns None
    assert _ndcg_at_k(["doc_1", "doc_2"], set(), top_k=10) is None


# -----------------------------------------------------------------------------
# 5. Embedding Space Contract & Sidecar Invariant Falsification
# -----------------------------------------------------------------------------

def test_adversarial_isotropic_router_64d_vectors_fail_closed() -> None:
    """Falsification: 64-d centroids or non-1024d vectors fail closed under BGE-M3 contract."""
    # Attempting to construct router with dimension != 1024 fails closed
    with pytest.raises(ValueError, match="1024-d BGE-M3 embedding space under contract"):
        IsotropicBgeM3Router(dimension=64)

    router = IsotropicBgeM3Router(dimension=1024)

    # Attempting to route a 64-d vector fails contract validation
    vec_64d = [0.1] * 64
    with pytest.raises((ValueError, EmbeddingSpaceMismatch)):
        router.route_vector(vec_64d)

    # NaN / Inf vector fails contract validation
    nan_vec = [float("nan")] * 1024
    with pytest.raises((ValueError, EmbeddingSpaceMismatch)):
        router.route_vector(nan_vec)

    # Zero norm vector fails contract validation
    zero_vec = [0.0] * 1024
    with pytest.raises((ValueError, EmbeddingSpaceMismatch)):
        router.route_vector(zero_vec)


def test_adversarial_sidecar_validation_non_contiguous_colbert_tokens() -> None:
    """Falsification: non-contiguous colbert_token positions [0, 2] fail extended sidecar invariants."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, record_type TEXT)")
    conn.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB)")
    conn.execute("CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))")
    conn.execute("CREATE TABLE colbert_token (record_id TEXT, position INTEGER, dimensions INTEGER, vector BLOB, PRIMARY KEY (record_id, position))")

    # Insert valid 1024-d unit norm vector
    norm_val = 1.0 / (DENSE_DIMENSIONS ** 0.5)
    valid_vec = _pack([norm_val] * DENSE_DIMENSIONS)
    conn.execute("INSERT INTO retrieval_record VALUES ('rec_1', 'assertion')")
    conn.execute("INSERT INTO dense_vector VALUES ('rec_1', ?, ?)", (DENSE_DIMENSIONS, valid_vec))
    conn.execute("INSERT INTO sparse_weight VALUES ('rec_1', 'token1', 1.0)")

    # Gapped colbert positions: pos 0, then pos 2 (missing pos 1)
    conn.execute("INSERT INTO colbert_token VALUES ('rec_1', 0, 1024, ?)", (valid_vec,))
    conn.execute("INSERT INTO colbert_token VALUES ('rec_1', 2, 1024, ?)", (valid_vec,))

    with pytest.raises(RuntimeError, match="non-contiguous"):
        run_extended_sidecar_invariants(conn, {"semantic_unit_index": True}, enforce_unit_policy=True)


def test_adversarial_sidecar_validation_l2_norm_breach() -> None:
    """Falsification: dense vector violating [0.85, 1.15] norm bounds fails validation."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, record_type TEXT)")
    conn.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB)")
    conn.execute("CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))")
    conn.execute("CREATE TABLE colbert_token (record_id TEXT, position INTEGER, dimensions INTEGER, vector BLOB, PRIMARY KEY (record_id, position))")

    # Insert vector with norm 1.30 (exceeds 1.15 ceiling)
    breach_val = 1.30 / (DENSE_DIMENSIONS ** 0.5)
    breach_vec = _pack([breach_val] * DENSE_DIMENSIONS)
    valid_colbert = _pack([1.0 / (DENSE_DIMENSIONS ** 0.5)] * DENSE_DIMENSIONS)

    conn.execute("INSERT INTO retrieval_record VALUES ('rec_breach', 'assertion')")
    conn.execute("INSERT INTO dense_vector VALUES ('rec_breach', ?, ?)", (DENSE_DIMENSIONS, breach_vec))
    conn.execute("INSERT INTO sparse_weight VALUES ('rec_breach', 'token1', 1.0)")
    conn.execute("INSERT INTO colbert_token VALUES ('rec_breach', 0, 1024, ?)", (valid_colbert,))

    with pytest.raises(RuntimeError, match="violates L2-norm bounds"):
        run_extended_sidecar_invariants(conn, {"semantic_unit_index": True}, enforce_unit_policy=True)
