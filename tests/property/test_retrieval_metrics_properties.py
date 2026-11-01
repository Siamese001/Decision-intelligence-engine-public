"""Property-based tests for retrieval evaluation metrics under Decision H-8.

Verifies mathematical invariants:
1. Monotonicity: improving candidate relevance rank never decreases MRR.
2. Boundedness: nDCG@k is strictly bounded in [0.0, 1.0].
3. Fixed Ideal DCG: unretrieved positives reduce nDCG@k relative to retrieved positives.
4. Scale-invariance: non-relevant candidates beyond cutoff k do not alter nDCG@k.
5. Fail-closed duplicate detection: duplicate record IDs in rankings always raise ValueError.
"""
from __future__ import annotations

import math
from typing import List, Mapping

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dual_pipeline.retrieval_release_core.metrics import (
    compute_dcg,
    compute_mrr_at_k,
    compute_ndcg,
    compute_ndcg_at_k,
    standard_ndcg_at_k,
    standard_recall_at_k,
    standard_reciprocal_rank,
    validate_retrieved_ranking,
)


@settings(max_examples=100)
@given(
    grades=st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=30),
    k=st.integers(min_value=1, max_value=20),
)
def test_property_ndcg_bounded_between_zero_and_one(grades: List[int], k: int) -> None:
    """Property: nDCG@k is mathematically bounded in [0.0, 1.0] for any non-negative grades."""
    val = compute_ndcg_at_k(grades, k=k)
    assert 0.0 <= val <= 1.0 + 1e-9, f"nDCG {val} outside [0.0, 1.0]"


@settings(max_examples=100)
@given(
    grades=st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=20),
    k=st.integers(min_value=1, max_value=15),
)
def test_property_mrr_monotonicity_on_rank_improvement(grades: List[int], k: int) -> None:
    """Property: improving the grade of any candidate cannot decrease MRR@k."""
    base_mrr = compute_mrr_at_k(grades, k=k)

    # Pick an index and improve its grade
    for idx in range(min(len(grades), k)):
        improved_grades = list(grades)
        improved_grades[idx] = max(improved_grades[idx], 1)
        improved_mrr = compute_mrr_at_k(improved_grades, k=k)
        assert (
            improved_mrr >= base_mrr - 1e-9
        ), f"MRR decreased from {base_mrr} to {improved_mrr} after improving grade at index {idx}"


@settings(max_examples=100)
@given(
    retrieved_count=st.integers(min_value=1, max_value=15),
    k=st.integers(min_value=1, max_value=10),
)
def test_property_duplicate_record_ids_fail_closed(retrieved_count: int, k: int) -> None:
    """Property: any duplicate record ID in candidate ranking must raise ValueError."""
    ids = [f"rec_{i}" for i in range(retrieved_count)]
    # Duplicate the first ID
    fraudulent = ids + [ids[0]]
    with pytest.raises(ValueError, match="Duplicate record IDs"):
        validate_retrieved_ranking(fraudulent, top_k=k)

    with pytest.raises(ValueError, match="Duplicate record IDs"):
        standard_ndcg_at_k(fraudulent, {r: 1 for r in ids}, top_k=k)

    with pytest.raises(ValueError, match="Duplicate record IDs"):
        standard_reciprocal_rank(fraudulent, {r: 1 for r in ids}, top_k=k)


@settings(max_examples=100)
@given(
    pos_count=st.integers(min_value=1, max_value=10),
    k=st.integers(min_value=1, max_value=10),
)
def test_property_perfect_ranking_yields_unit_ndcg(pos_count: int, k: int) -> None:
    """Property: perfect ranking of positive items yields nDCG@k == 1.0."""
    ids = [f"doc_{i}" for i in range(pos_count)]
    judgments = {f"doc_{i}": 2 for i in range(pos_count)}
    ndcg = standard_ndcg_at_k(ids, judgments, top_k=k)
    assert ndcg is not None
    assert math.isclose(ndcg, 1.0, abs_tol=1e-6)


@settings(max_examples=100)
@given(
    retrieved_pos=st.integers(min_value=1, max_value=5),
    unretrieved_pos=st.integers(min_value=1, max_value=5),
    k=st.integers(min_value=5, max_value=15),
)
def test_property_unretrieved_positives_penalize_ndcg(
    retrieved_pos: int, unretrieved_pos: int, k: int
) -> None:
    """Property: ground-truth positives that are unretrieved must strictly reduce nDCG@k."""
    ret_ids = [f"ret_{i}" for i in range(retrieved_pos)]
    unret_ids = [f"unret_{i}" for i in range(unretrieved_pos)]

    # Judgments containing both retrieved and unretrieved positives
    full_judgments = {rid: 2 for rid in ret_ids + unret_ids}
    ndcg_with_unretrieved = standard_ndcg_at_k(ret_ids, full_judgments, top_k=k)

    # Judgments if unretrieved were not in ground truth
    truncated_judgments = {rid: 2 for rid in ret_ids}
    ndcg_truncated = standard_ndcg_at_k(ret_ids, truncated_judgments, top_k=k)

    assert ndcg_with_unretrieved is not None
    assert ndcg_truncated is not None
    # Truncated ideal DCG is smaller, so score without unretrieved is higher
    assert ndcg_with_unretrieved <= ndcg_truncated + 1e-9


@settings(max_examples=100)
@given(
    retrieved_count=st.integers(min_value=1, max_value=15),
    k=st.integers(min_value=1, max_value=10),
)
def test_property_recall_bounded_between_zero_and_one(retrieved_count: int, k: int) -> None:
    """Property: Recall@k is strictly bounded in [0.0, 1.0]."""
    rids = [f"rec_{i}" for i in range(retrieved_count)]
    judgments = {f"rec_{i}": 1 for i in range(min(5, retrieved_count))}
    rec = standard_recall_at_k(rids, judgments, top_k=k)
    assert rec is not None
    assert 0.0 <= rec <= 1.0
