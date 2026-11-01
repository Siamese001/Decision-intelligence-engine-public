"""Unit tests for Wave 1 standard IR metric implementations and cross-evaluator agreement.

Verifies:
1. Exact arithmetic agreement on all hand-calculated fixtures.
2. Production metrics and IndependentStandardEvaluator numerical parity.
3. Monotonic Reciprocal Rank (fixing the 0.5 -> 0.1 rank-10 regression).
4. Graded nDCG@k with fixed query judgments (missing positives penalized).
5. Duplicate record IDs rejected with ValueError.
6. Empty required datasets fail closed.
7. No-answer queries isolated from positive-query metrics.
8. Decision owner operating boundaries.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.retrieval_release_core.evaluation_contracts import (
    EvidenceRelevanceContract,
    GainMapping,
    NoAnswerRejectionContract,
    RetrievalOperatingLimits,
    RouteCorrectnessContract,
    SafeCacheReuseContract,
)
from dual_pipeline.retrieval_release_core.metrics import (
    _metrics,
    standard_ndcg_at_k,
    standard_recall_at_k,
    standard_reciprocal_rank,
    validate_retrieved_ranking,
)
from tools.independent_standard_evaluator import IndependentStandardEvaluator

FIXTURES_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "hand_calculated_retrieval_fixtures.json"
)


@pytest.fixture
def fixtures_data() -> dict:
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["fixtures"]


def test_hand_calculated_rank_10_improvement_anomaly(fixtures_data: dict) -> None:
    """Verify that improving rank 10 cannot decrease reciprocal rank (fixing 0.5 -> 0.1 bug)."""
    case = fixtures_data["rank_10_improvement_anomaly"]
    r1 = case["retrieved_state_1"]
    r2 = case["retrieved_state_2"]
    j1 = case["judgments_state_1"]
    j2 = case["judgments_state_2"]
    expected = case["expected"]

    evaluator_t1 = IndependentStandardEvaluator(top_k=10, relevance_threshold=1)
    evaluator_t2 = IndependentStandardEvaluator(top_k=10, relevance_threshold=2)

    # Threshold 1: any relevance (>=1)
    rr1_prod_t1 = standard_reciprocal_rank(r1, j1, top_k=10, relevance_threshold=1)
    rr1_ind_t1 = evaluator_t1.reciprocal_rank(r1, j1)
    rr2_prod_t1 = standard_reciprocal_rank(r2, j2, top_k=10, relevance_threshold=1)
    rr2_ind_t1 = evaluator_t1.reciprocal_rank(r2, j2)

    assert rr1_prod_t1 == expected["state_1_rr_threshold_1"]
    assert rr1_ind_t1 == expected["state_1_rr_threshold_1"]
    assert rr2_prod_t1 == expected["state_2_rr_threshold_1"]
    assert rr2_ind_t1 == expected["state_2_rr_threshold_1"]
    assert rr2_prod_t1 >= rr1_prod_t1  # Monotonically non-decreasing

    # Threshold 2: highly relevant (>=2)
    rr1_prod_t2 = standard_reciprocal_rank(r1, j1, top_k=10, relevance_threshold=2)
    rr1_ind_t2 = evaluator_t2.reciprocal_rank(r1, j1)
    rr2_prod_t2 = standard_reciprocal_rank(r2, j2, top_k=10, relevance_threshold=2)
    rr2_ind_t2 = evaluator_t2.reciprocal_rank(r2, j2)

    assert rr1_prod_t2 == expected["state_1_rr_threshold_2"]
    assert rr1_ind_t2 == expected["state_1_rr_threshold_2"]
    assert rr2_prod_t2 == expected["state_2_rr_threshold_2"]
    assert rr2_ind_t2 == expected["state_2_rr_threshold_2"]
    assert rr2_prod_t2 >= rr1_prod_t2  # Monotonically non-decreasing


def test_hand_calculated_graded_ndcg_fixed_query_judgments(fixtures_data: dict) -> None:
    """Verify that graded nDCG uses fixed query judgments and missing positives reduce score."""
    case = fixtures_data["graded_ndcg_fixed_query_judgments"]
    judgments = case["ground_truth_query_judgments"]
    top_k = case["top_k"]

    evaluator = IndependentStandardEvaluator(top_k=top_k, gain_mapping=case["gain_mapping"])

    # Perfect ranking: all 4 documents retrieved in ideal order
    r_perfect = case["candidate_ranking_perfect"]
    ndcg_perfect_prod = standard_ndcg_at_k(r_perfect, judgments, top_k=top_k)
    ndcg_perfect_ind = evaluator.ndcg_at_k(r_perfect, judgments)

    assert ndcg_perfect_prod == pytest.approx(case["expected_ndcg_perfect"], abs=1e-6)
    assert ndcg_perfect_ind == pytest.approx(case["expected_ndcg_perfect"], abs=1e-6)

    # Missing positives: missed top 2 Grade 2 documents, retrieved Grade 1s + irrelevant
    r_missing = case["candidate_ranking_missing_positives"]
    ndcg_missing_prod = standard_ndcg_at_k(r_missing, judgments, top_k=top_k)
    ndcg_missing_ind = evaluator.ndcg_at_k(r_missing, judgments)

    assert ndcg_missing_prod == pytest.approx(case["expected_ndcg_missing_positives"], abs=1e-6)
    assert ndcg_missing_ind == pytest.approx(case["expected_ndcg_missing_positives"], abs=1e-6)

    # Score heavily penalized compared to perfect
    assert ndcg_missing_prod < 0.35
    assert ndcg_missing_ind < 0.35


def test_hand_calculated_duplicate_ids_validation(fixtures_data: dict) -> None:
    """Verify that duplicate record IDs fail validation with ValueError."""
    case = fixtures_data["duplicate_ids_validation"]
    ranking = case["candidate_ranking_with_duplicates"]
    judgments = case["query_judgments"]

    evaluator = IndependentStandardEvaluator(top_k=5)

    with pytest.raises(ValueError, match="Duplicate record IDs detected"):
        validate_retrieved_ranking(ranking)

    with pytest.raises(ValueError, match="Duplicate record IDs detected"):
        standard_ndcg_at_k(ranking, judgments, top_k=5)

    with pytest.raises(ValueError, match="Duplicate record IDs detected"):
        standard_reciprocal_rank(ranking, judgments, top_k=5)

    with pytest.raises(ValueError, match="Duplicate record IDs detected"):
        evaluator.validate_ranking(ranking)

    with pytest.raises(ValueError, match="Duplicate record IDs detected"):
        evaluator.ndcg_at_k(ranking, judgments)


def test_hand_calculated_no_answer_query_isolation(fixtures_data: dict) -> None:
    """Verify that adding no-answer queries does NOT inflate positive-query Recall/MRR/nDCG."""
    case = fixtures_data["no_answer_query_isolation"]
    pos_case = case["positive_case"]
    no_ans_case = case["no_answer_case"]
    expected = case["expected_aggregate"]

    # 1. Baseline with only positive case
    baseline_agg = _metrics([pos_case])
    assert baseline_agg["case_count"] == 1
    assert baseline_agg["positive_case_count"] == 1
    assert baseline_agg["recall_at_k"] == 1.0
    assert baseline_agg["mrr"] == 1.0
    assert baseline_agg["ndcg_at_k"] == 1.0

    # 2. Add no-answer case
    combined_agg = _metrics([pos_case, no_ans_case])
    assert combined_agg["case_count"] == expected["case_count"]
    assert combined_agg["positive_case_count"] == expected["positive_case_count"]
    # Positive metrics MUST remain unchanged!
    assert combined_agg["recall_at_k"] == baseline_agg["recall_at_k"]
    assert combined_agg["mrr"] == baseline_agg["mrr"]
    assert combined_agg["ndcg_at_k"] == baseline_agg["ndcg_at_k"]
    # No-answer rejection reported separately
    assert combined_agg["no_answer_case_count"] == expected["no_answer_case_count"]
    assert combined_agg["no_answer_rejection_rate"] == expected["no_answer_rejection_rate"]


def test_hand_calculated_empty_dataset_rejection(fixtures_data: dict) -> None:
    """Verify that empty required datasets fail closed."""
    case = fixtures_data["empty_dataset_rejection"]
    cases = case["cases"]
    expected = case["expected_aggregate"]

    agg_prod = _metrics(cases)
    assert agg_prod["case_count"] == 0
    assert agg_prod["positive_case_count"] == 0
    assert agg_prod["recall_at_k"] == 0.0
    assert agg_prod["mrr"] == 0.0
    assert agg_prod["ndcg_at_k"] == 0.0
    assert agg_prod["status"] == expected["status"]

    evaluator = IndependentStandardEvaluator()
    agg_ind = evaluator.evaluate_dataset(cases)
    assert agg_ind["case_count"] == 0
    assert agg_ind["positive_case_count"] == 0
    assert agg_ind["status"] == expected["status"]


def test_hand_calculated_linear_gain_mapping(fixtures_data: dict) -> None:
    """Verify linear gain mapping calculation parity."""
    case = fixtures_data["linear_gain_mapping"]
    judgments = case["query_judgments"]
    ranking = case["candidate_ranking"]
    top_k = case["top_k"]

    evaluator = IndependentStandardEvaluator(top_k=top_k, gain_mapping="linear")

    ndcg_prod = standard_ndcg_at_k(ranking, judgments, top_k=top_k, gain_mapping="linear")
    ndcg_ind = evaluator.ndcg_at_k(ranking, judgments)

    assert ndcg_prod == pytest.approx(case["expected_ndcg"], abs=1e-6)
    assert ndcg_ind == pytest.approx(case["expected_ndcg"], abs=1e-6)


def test_hand_calculated_unjudged_items_zero_gain(fixtures_data: dict) -> None:
    """Verify unjudged retrieved items contribute zero gain."""
    case = fixtures_data["unjudged_items_zero_gain"]
    judgments = case["query_judgments"]
    ranking = case["candidate_ranking"]
    top_k = case["top_k"]

    evaluator = IndependentStandardEvaluator(top_k=top_k)

    ndcg_prod = standard_ndcg_at_k(ranking, judgments, top_k=top_k)
    ndcg_ind = evaluator.ndcg_at_k(ranking, judgments)

    assert ndcg_prod == pytest.approx(case["expected_ndcg"], abs=1e-6)
    assert ndcg_ind == pytest.approx(case["expected_ndcg"], abs=1e-6)


def test_safe_cache_reuse_four_gates() -> None:
    """Verify four gates of SafeCacheReuseContract."""
    contract = SafeCacheReuseContract()

    # All gates satisfied
    ok, rejections = contract.verify_cache_admissibility(
        entity_match=True, temporal_fresh=True, authorized=True, polarity_match=True
    )
    assert ok is True
    assert len(rejections) == 0

    # Entity mismatch
    ok, rejections = contract.verify_cache_admissibility(
        entity_match=False, temporal_fresh=True, authorized=True, polarity_match=True
    )
    assert ok is False
    assert "ENTITY_MISMATCH" in rejections

    # Negation / polarity conflict
    ok, rejections = contract.verify_cache_admissibility(
        entity_match=True, temporal_fresh=True, authorized=True, polarity_match=False
    )
    assert ok is False
    assert "POLARITY_NEGATION_CONFLICT" in rejections


def test_retrieval_operating_limits_validation() -> None:
    """Verify decision owner operating boundary validation."""
    limits = RetrievalOperatingLimits()

    # Compliant run
    ok, failures = limits.validate_metrics(
        ndcg_10=0.85,
        mrr_10=0.75,
        recall_10=0.88,
        p95_latency_ms=120.0,
        unsupported_rate=0.02,
    )
    assert ok is True
    assert len(failures) == 0

    # Violating quality floor and latency budget
    ok, failures = limits.validate_metrics(
        ndcg_10=0.65,  # Below 0.70 floor
        mrr_10=0.55,   # Below 0.60 floor
        recall_10=0.70, # Below 0.75 floor
        p95_latency_ms=300.0, # Above 250ms
        unsupported_rate=0.08, # Above 0.05 ceiling
    )
    assert ok is False
    assert len(failures) == 5
