"""Wave 3: Paired retrieval experiments, ablation studies, and evaluation parity."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .metrics import (
    standard_ndcg_at_k,
    standard_recall_at_k,
    standard_reciprocal_rank,
    validate_retrieved_ranking,
)

CANONICAL_WEIGHTS = {
    "dense": 0.34,
    "sparse": 0.17,
    "bm25": 0.15,
    "colbert": 0.34,
}


class EvaluationMode(str, Enum):
    FIXED_POOL_RERANK = "fixed_pool_rerank"
    REGENERATED_CANDIDATES = "regenerated_candidates"


class RetrievalConfig(str, Enum):
    HYBRID_FULL = "HYBRID_FULL"
    BM25_ONLY = "BM25_ONLY"
    DENSE_ONLY = "DENSE_ONLY"
    SPARSE_ONLY = "SPARSE_ONLY"
    COLBERT_ONLY = "COLBERT_ONLY"
    NO_BM25 = "NO_BM25"
    NO_DENSE = "NO_DENSE"
    NO_SPARSE = "NO_SPARSE"
    NO_COLBERT = "NO_COLBERT"


@dataclass
class QueryJudgments:
    query_id: str
    query_text: str
    topic_id: str
    phrasing_type: str
    judgments: Dict[str, int]
    relevant_ids: Set[str]
    is_answerable: bool


@dataclass
class PairedExperimentResult:
    config: RetrievalConfig
    mode: EvaluationMode
    queries_evaluated: int
    candidate_recall: float
    metrics_at_k: Dict[int, Dict[str, float]]
    critical_slices: Dict[str, Dict[str, float]]
    paired_deltas: Dict[str, float] = field(default_factory=dict)
    topic_cluster_deltas: Dict[str, float] = field(default_factory=dict)


def validate_holdout_protection(query_ids: Iterable[str], partitions: Dict[str, Any]) -> None:
    """Enforce fail-closed gate preventing any final_holdout query from being evaluated."""
    holdout_set = set(partitions.get("query_partitions", {}).get("final_holdout", []))
    for qid in query_ids:
        if qid in holdout_set:
            raise ValueError(
                f"HOLDOUT LEAKAGE VIOLATION: Query '{qid}' belongs to locked final_holdout partition. "
                "Wave 3 evaluation is strictly quarantined to the development partition."
            )


def _get_active_generators(config: RetrievalConfig) -> Set[str]:
    mapping = {
        RetrievalConfig.BM25_ONLY: {"bm25"},
        RetrievalConfig.DENSE_ONLY: {"dense"},
        RetrievalConfig.SPARSE_ONLY: {"sparse"},
        RetrievalConfig.COLBERT_ONLY: {"colbert"},
        RetrievalConfig.HYBRID_FULL: {"dense", "sparse", "bm25", "colbert"},
        RetrievalConfig.NO_BM25: {"dense", "sparse", "colbert"},
        RetrievalConfig.NO_DENSE: {"sparse", "bm25", "colbert"},
        RetrievalConfig.NO_SPARSE: {"dense", "bm25", "colbert"},
        RetrievalConfig.NO_COLBERT: {"dense", "sparse", "bm25"},
    }
    return mapping.get(config, set())


def filter_active_candidates(
    candidates: List[Dict[str, Any]],
    config: RetrievalConfig,
    mode: EvaluationMode,
) -> List[Dict[str, Any]]:
    """Enforce invariant: disabled engines contribute no invented candidates in regenerated mode."""
    if mode == EvaluationMode.FIXED_POOL_RERANK:
        return list(candidates)

    active_generators = _get_active_generators(config)

    admitted: List[Dict[str, Any]] = []
    for c in candidates:
        if any(c.get(f"{eng}_rank") is not None for eng in active_generators):
            admitted.append(c)
    return admitted


def _safe_float(val: Any, default: float = -1e9) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return default


def _safe_rank(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        r = int(val)
        return r if r > 0 else None
    except (ValueError, TypeError):
        return None


def compute_config_score(
    candidate: Dict[str, Any],
    config: RetrievalConfig,
    rrf_k: int = 60,
) -> float:
    """Compute scoring for a candidate under a specific retrieval configuration."""
    if config == RetrievalConfig.BM25_ONLY:
        rk = _safe_rank(candidate.get("bm25_rank"))
        return -float(rk) if rk is not None else -1e9
    if config == RetrievalConfig.DENSE_ONLY:
        return _safe_float(candidate.get("dense_score"))
    if config == RetrievalConfig.SPARSE_ONLY:
        return _safe_float(candidate.get("sparse_score"))
    if config == RetrievalConfig.COLBERT_ONLY:
        return _safe_float(candidate.get("colbert_score"))

    active_weights: Dict[str, float] = {}
    if config == RetrievalConfig.HYBRID_FULL:
        active_weights = CANONICAL_WEIGHTS
    elif config == RetrievalConfig.NO_BM25:
        active_weights = {k: v for k, v in CANONICAL_WEIGHTS.items() if k != "bm25"}
    elif config == RetrievalConfig.NO_DENSE:
        active_weights = {k: v for k, v in CANONICAL_WEIGHTS.items() if k != "dense"}
    elif config == RetrievalConfig.NO_SPARSE:
        active_weights = {k: v for k, v in CANONICAL_WEIGHTS.items() if k != "sparse"}
    elif config == RetrievalConfig.NO_COLBERT:
        active_weights = {k: v for k, v in CANONICAL_WEIGHTS.items() if k != "colbert"}

    total_weight = sum(active_weights.values())
    rrf_score = 0.0
    for eng, weight in active_weights.items():
        rk = _safe_rank(candidate.get(f"{eng}_rank"))
        if rk is not None:
            norm_w = weight / total_weight
            rrf_score += norm_w / (rrf_k + rk)
    return rrf_score


def rank_candidates_for_query(
    candidates: List[Dict[str, Any]],
    config: RetrievalConfig,
    mode: EvaluationMode,
    exact_anchor_ids: Optional[List[str]] = None,
    rrf_k: int = 60,
) -> List[str]:
    """Rank candidates with evaluation parity for exact anchors and active components."""
    admitted = filter_active_candidates(candidates, config, mode)
    if not admitted:
        return []

    anchors = set(exact_anchor_ids or [])
    promoted_records: List[str] = []
    normal_candidates: List[Tuple[float, str]] = []

    for c in admitted:
        rec_id = c["record_id"]
        if rec_id in anchors:
            promoted_records.append(rec_id)
        else:
            score = compute_config_score(c, config, rrf_k=rrf_k)
            normal_candidates.append((score, rec_id))

    normal_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    sorted_normal = [rec_id for _, rec_id in normal_candidates]
    full_ranking = promoted_records + sorted_normal

    validate_retrieved_ranking(full_ranking)
    return full_ranking


def evaluate_query_ranking(
    retrieved_ids: List[str],
    query_judgments: QueryJudgments,
    cutoffs: Tuple[int, ...] = (5, 10, 20),
) -> Tuple[Optional[float], Dict[int, Dict[str, float]]]:
    """Evaluate candidate recall and metrics at each cutoff for a single query."""
    rel_ids = query_judgments.relevant_ids
    if not query_judgments.is_answerable or not rel_ids:
        metrics_by_k: Dict[int, Dict[str, float]] = {}
        for k in cutoffs:
            top_k = retrieved_ids[:k]
            unjudged = sum(1 for rid in top_k if rid not in query_judgments.judgments) / k if k > 0 else 0.0
            metrics_by_k[k] = {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0, "unjudged": unjudged}
        return None, metrics_by_k

    retrieved_set = set(retrieved_ids)
    cand_recall = len(retrieved_set & rel_ids) / len(rel_ids)

    metrics_by_k = {}
    for k in cutoffs:
        top_k = retrieved_ids[:k]
        top_set = set(top_k)
        rec_k = len(top_set & rel_ids) / len(rel_ids)
        ndcg_val = standard_ndcg_at_k(top_k, query_judgments.judgments, top_k=k)
        ndcg_k = ndcg_val if ndcg_val is not None else 0.0
        mrr_val = standard_reciprocal_rank(top_k, rel_ids, top_k=k, relevance_threshold=1)
        mrr_k = mrr_val if mrr_val is not None else 0.0
        unjudged_k = sum(1 for rid in top_k if rid not in query_judgments.judgments) / k if k > 0 else 0.0
        metrics_by_k[k] = {
            "ndcg": ndcg_k,
            "mrr": mrr_k,
            "recall": rec_k,
            "unjudged": unjudged_k,
        }
    return cand_recall, metrics_by_k


def _aggregate_metrics(metrics_at_k: Dict[int, Dict[str, float]], cutoffs: Tuple[int, ...], k_sums: Dict[int, Dict[str, float]]) -> None:
    for k in cutoffs:
        for m in ("ndcg", "mrr", "recall", "unjudged"):
            k_sums[k][m] += metrics_at_k[k][m]

def _update_slices(metrics_at_k: Dict[int, Dict[str, float]], gt: QueryJudgments, slices_data: Dict[str, List[float]]) -> None:
    ndcg = metrics_at_k[10]["ndcg"]
    slices_data.setdefault(f"phrasing_{gt.phrasing_type}", []).append(ndcg)
    slices_data.setdefault(f"topic_{gt.topic_id}", []).append(ndcg)

def run_paired_experiment(
    config: RetrievalConfig,
    mode: EvaluationMode,
    queries_data: List[Dict[str, Any]],
    ground_truth_map: Dict[str, QueryJudgments],
    cutoffs: Tuple[int, ...] = (5, 10, 20),
) -> Tuple[PairedExperimentResult, Dict[str, Dict[str, float]]]:
    """Run an evaluation experiment for one configuration across partition queries."""
    total_cand_recall = 0.0
    valid_cand_queries = 0
    k_sums: Dict[int, Dict[str, float]] = {k: {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0, "unjudged": 0.0} for k in cutoffs}
    valid_queries_count = 0
    per_query_metrics: Dict[str, Dict[str, float]] = {}
    slices_data: Dict[str, List[float]] = {}

    for q in queries_data:
        qid = q["query_id"]
        gt = ground_truth_map[qid]
        candidates = q.get("candidates", [])
        exact_anchors = q.get("exact_anchor_ids")

        ranked_ids = rank_candidates_for_query(candidates, config, mode, exact_anchor_ids=exact_anchors)
        cand_recall, metrics_at_k = evaluate_query_ranking(ranked_ids, gt, cutoffs=cutoffs)

        if cand_recall is not None:
            total_cand_recall += cand_recall
            valid_cand_queries += 1

        if gt.is_answerable:
            valid_queries_count += 1
            _aggregate_metrics(metrics_at_k, cutoffs, k_sums)
            
            per_query_metrics[qid] = {
                "ndcg_10": metrics_at_k[10]["ndcg"],
                "mrr_10": metrics_at_k[10]["mrr"],
                "recall_10": metrics_at_k[10]["recall"],
            }
            _update_slices(metrics_at_k, gt, slices_data)

    mean_cand_recall = total_cand_recall / valid_cand_queries if valid_cand_queries > 0 else 0.0
    agg_metrics: Dict[int, Dict[str, float]] = {}
    for k in cutoffs:
        agg_metrics[k] = {}
        for m in ("ndcg", "mrr", "recall", "unjudged"):
            agg_metrics[k][m] = round(k_sums[k][m] / valid_queries_count, 4) if valid_queries_count > 0 else 0.0

    critical_slices: Dict[str, Dict[str, float]] = {}
    for s_name, scores in slices_data.items():
        critical_slices[s_name] = {
            "mean_ndcg_10": round(sum(scores) / len(scores), 4),
            "worst_ndcg_10": round(min(scores), 4),
            "sample_count": len(scores),
        }

    res = PairedExperimentResult(
        config=config,
        mode=mode,
        queries_evaluated=len(queries_data),
        candidate_recall=round(mean_cand_recall, 4),
        metrics_at_k=agg_metrics,
        critical_slices=critical_slices,
    )
    return res, per_query_metrics


class ClusteredDifferenceResult(tuple):
    mean_delta: float
    standard_error: float
    ci_95: Tuple[float, float]
    p_value: float

    def __new__(
        cls,
        mean_delta: float,
        standard_error: float,
        ci_95: Tuple[float, float],
        p_value: float = 1.0,
    ):
        instance = super().__new__(cls, (mean_delta, standard_error, ci_95))
        instance.mean_delta = mean_delta
        instance.standard_error = standard_error
        instance.ci_95 = ci_95
        instance.p_value = p_value
        return instance


def compute_family_clustered_differences(
    base_query_metrics: Dict[str, Dict[str, float]],
    comp_query_metrics: Dict[str, Dict[str, float]],
    ground_truth_map: Dict[str, QueryJudgments],
    *,
    n_bootstraps: int = 10000,
    seed: int = 42,
) -> ClusteredDifferenceResult:
    """Compute topic-family clustered ratio difference, 10,000-draw bootstrap SE, 95% CI, and p-value."""
    topic_deltas: Dict[str, List[float]] = {}
    for qid, base_m in base_query_metrics.items():
        if qid not in comp_query_metrics:
            continue
        gt = ground_truth_map[qid]
        delta = comp_query_metrics[qid]["ndcg_10"] - base_m["ndcg_10"]
        topic_deltas.setdefault(gt.topic_id, []).append(delta)

    if not topic_deltas:
        return ClusteredDifferenceResult(0.0, 0.0, (0.0, 0.0), 1.0)

    cluster_sums = [sum(deltas) for deltas in topic_deltas.values()]
    cluster_sizes = [len(deltas) for deltas in topic_deltas.values()]
    k_clusters = len(cluster_sums)
    total_queries = sum(cluster_sizes)

    if total_queries == 0:
        return ClusteredDifferenceResult(0.0, 0.0, (0.0, 0.0), 1.0)

    ratio_mean = sum(cluster_sums) / total_queries

    if k_clusters < 2:
        return ClusteredDifferenceResult(
            round(ratio_mean, 4), 0.0, (round(ratio_mean, 4), round(ratio_mean, 4)), 1.0
        )

    import numpy as np

    rng = np.random.default_rng(seed=seed)
    y_arr = np.array(cluster_sums, dtype=np.float64)
    n_arr = np.array(cluster_sizes, dtype=np.float64)

    idx = rng.integers(0, k_clusters, size=(n_bootstraps, k_clusters))
    boot_y = y_arr[idx].sum(axis=1)
    boot_n = n_arr[idx].sum(axis=1)
    boot_means = boot_y / np.maximum(boot_n, 1.0)

    se_cluster = float(np.std(boot_means, ddof=1))
    ci_lower = float(np.percentile(boot_means, 2.5))
    ci_upper = float(np.percentile(boot_means, 97.5))
    p_val = float(min(1.0, 2.0 * min(np.mean(boot_means <= 0), np.mean(boot_means >= 0))))

    return ClusteredDifferenceResult(
        round(ratio_mean, 4),
        round(se_cluster, 4),
        (round(ci_lower, 4), round(ci_upper, 4)),
        round(p_val, 4),
    )


def apply_holm_bonferroni_correction(
    p_values: Dict[str, float],
) -> Dict[str, float]:
    """Applies step-down Holm-Bonferroni FWER adjustment across multiple ablation hypotheses."""
    if not p_values:
        return {}
    sorted_items = sorted(p_values.items(), key=lambda x: x[1])
    m = len(sorted_items)
    adjusted: Dict[str, float] = {}

    cum_max = 0.0
    for rank, (name, p_val) in enumerate(sorted_items, 1):
        multiplier = m - rank + 1
        adj_p = min(1.0, p_val * multiplier)
        cum_max = max(cum_max, adj_p)
        adjusted[name] = round(cum_max, 4)

    return adjusted
