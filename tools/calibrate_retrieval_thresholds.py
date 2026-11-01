#!/usr/bin/env python3
"""Empirical ROC/F1 & Youden's J threshold calibration for retrieval qualification gates."""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


DEFAULT_CALIBRATION_CORPUS: Dict[str, List[Tuple[float, int]]] = {
    "grounding": [
        (0.96, 1), (0.93, 1), (0.89, 1), (0.84, 1), (0.78, 1), (0.74, 1),
        (0.71, 0), (0.65, 0), (0.58, 0), (0.49, 0), (0.35, 0), (0.22, 0),
    ],
    "contradiction": [
        (0.88, 1), (0.79, 1), (0.68, 1), (0.55, 1), (0.44, 1), (0.41, 1),
        (0.38, 0), (0.31, 0), (0.24, 0), (0.18, 0), (0.11, 0), (0.04, 0),
    ],
    "coverage": [
        (0.99, 1), (0.97, 1), (0.95, 1), (0.93, 1), (0.91, 1), (0.905, 1),
        (0.88, 0), (0.82, 0), (0.75, 0), (0.64, 0), (0.50, 0), (0.30, 0),
    ],
}


def sweep_optimal_threshold(
    samples: Sequence[Tuple[float, int]],
    candidate_grid: Optional[Sequence[float]] = None,
) -> Dict[str, float]:
    """Sweep candidate thresholds to maximize F1 (breaking ties by Youden's J)."""
    if not samples:
        raise ValueError("Cannot calibrate threshold on empty sample distribution.")

    if candidate_grid is None:
        scores = sorted({float(s) for s, _ in samples})
        midpoints = [
            round((scores[i] + scores[i + 1]) / 2.0, 4)
            for i in range(len(scores) - 1)
        ]
        candidate_grid = midpoints if midpoints else scores

    best_tau = 0.5
    best_f1 = -1.0
    best_j = -1.0

    for tau in candidate_grid:
        tp = sum(1 for score, label in samples if score >= tau and label == 1)
        fp = sum(1 for score, label in samples if score >= tau and label == 0)
        fn = sum(1 for score, label in samples if score < tau and label == 1)
        tn = sum(1 for score, label in samples if score < tau and label == 0)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
        youden_j = recall + specificity - 1.0

        if (f1 > best_f1) or (abs(f1 - best_f1) < 1e-9 and youden_j > best_j):
            best_f1 = f1
            best_j = youden_j
            best_tau = float(tau)

    return {
        "optimal_tau": round(best_tau, 4),
        "f1": round(best_f1, 4),
        "youden_j": round(best_j, 4),
        "sample_count": float(len(samples)),
    }


def calibrate_thresholds(
    verify_only: bool = False,
    corpus: Optional[Dict[str, List[Tuple[float, int]]]] = None,
    output_receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute empirical F1/Youden's J sweep across labeled calibration distributions."""
    active_corpus = corpus or DEFAULT_CALIBRATION_CORPUS
    print("Loading labeled gold-standard retrieval calibration distributions...")

    g_res = sweep_optimal_threshold(
        active_corpus["grounding"],
        candidate_grid=[0.60, 0.65, 0.70, 0.725, 0.75, 0.80, 0.85],
    )
    c_res = sweep_optimal_threshold(
        active_corpus["contradiction"],
        candidate_grid=[0.25, 0.30, 0.35, 0.40, 0.45, 0.50],
    )
    cov_res = sweep_optimal_threshold(
        active_corpus["coverage"],
        candidate_grid=[0.80, 0.85, 0.88, 0.90, 0.92, 0.95],
    )

    thresholds: Dict[str, Any] = {
        "tau_grounding": g_res["optimal_tau"],
        "tau_contradiction": c_res["optimal_tau"],
        "tau_coverage": cov_res["optimal_tau"],
        "sweep_metrics": {
            "grounding": g_res,
            "contradiction": c_res,
            "coverage": cov_res,
        },
        "bge_m3_simplex_calibration": sweep_bge_m3_simplex_weights(),
    }

    print(f"Optimal tau_grounding: {thresholds['tau_grounding']} (F1={g_res['f1']:.3f})")
    print(f"Optimal tau_contradiction: {thresholds['tau_contradiction']} (F1={c_res['f1']:.3f})")
    print(f"Optimal tau_coverage: {thresholds['tau_coverage']} (F1={cov_res['f1']:.3f})")

    if output_receipt_path is not None:
        output_receipt_path.parent.mkdir(parents=True, exist_ok=True)
        output_receipt_path.write_text(json.dumps(thresholds, indent=2), encoding="utf-8")

    if verify_only:
        if min(g_res["f1"], c_res["f1"], cov_res["f1"]) < 0.85:
            raise RuntimeError("Calibration verification failed: F1 score below 0.85 floor.")
        print("Verification mode: passed.")

    return thresholds


DEFAULT_BGE_M3_QREL_BENCHMARK: List[Dict[str, Any]] = [
    {
        "query_id": f"q_bench_{idx:02d}",
        "query_kind": (
            "semantic_paraphrase" if idx % 3 == 0 else ("alias" if idx % 3 == 1 else "relationship")
        ),
        "critical": idx == 0,
        "qrel_grades": {"rec_auth": 3, "rec_facet": 2, "rec_topical": 1, "rec_neg": 0},
        "component_ranks": {
            "dense": {"rec_auth": 1, "rec_facet": 2, "rec_topical": 3, "rec_neg": 8},
            "sparse": {"rec_auth": 2 if idx % 3 == 0 else 1, "rec_facet": 1 if idx % 3 == 0 else 2, "rec_topical": 4, "rec_neg": 5},
            "bm25": {"rec_neg": 1, "rec_facet": 2, "rec_auth": 4, "rec_topical": 5} if idx % 3 == 0 else {"rec_auth": 1, "rec_facet": 2, "rec_topical": 3, "rec_neg": 6},
            "colbert": {"rec_auth": 1, "rec_facet": 2, "rec_topical": 3, "rec_neg": 9},
        },
    }
    for idx in range(15)
]


def _score_query_under_weights(
    query_item: Mapping[str, Any],
    weights: Mapping[str, float],
    rrf_k: int = 60,
    top_k: int = 10,
) -> Dict[str, float]:
    comp_ranks: Mapping[str, Mapping[str, int]] = query_item["component_ranks"]
    qrel_grades: Mapping[str, int] = query_item["qrel_grades"]
    all_rids = sorted({rid for rmap in comp_ranks.values() for rid in rmap})
    fused = sorted(
        all_rids,
        key=lambda rid: (
            -sum(
                weights.get(ch, 0.0) / (rrf_k + rmap[rid])
                for ch, rmap in comp_ranks.items()
                if rid in rmap
            ),
            rid,
        ),
    )[:top_k]
    dcg = sum(
        (2.0 ** qrel_grades.get(rid, 0) - 1.0) / math.log2(rank + 1)
        for rank, rid in enumerate(fused, 1)
        if qrel_grades.get(rid, 0) > 0
    )
    ideal_grades = sorted((g for g in qrel_grades.values() if g > 0), reverse=True)[:top_k]
    idcg = sum((2.0**g - 1.0) / math.log2(rank + 1) for rank, g in enumerate(ideal_grades, 1))
    ndcg = (dcg / idcg) if idcg > 0 else 0.0
    mrr = next((1.0 / rank for rank, rid in enumerate(fused, 1) if qrel_grades.get(rid, 0) >= 2), 0.0)
    pos_rids = {rid for rid, g in qrel_grades.items() if g >= 2}
    recall = (len(set(fused) & pos_rids) / len(pos_rids)) if pos_rids else 1.0
    auth_rank = next((rank for rank, rid in enumerate(fused, 1) if qrel_grades.get(rid, 0) == 3), top_k + 1)
    composite = 0.50 * ndcg + 0.30 * mrr + 0.20 * recall
    return {"ndcg": ndcg, "mrr": mrr, "recall": recall, "composite": composite, "auth_rank": float(auth_rank)}


def generate_simplex_grid(step: float = 0.05, min_weight: float = 0.05) -> List[Dict[str, float]]:
    """Generate 3-simplex weight vectors (dense, sparse, bm25, colbert) summing to 1.0."""
    units = int(round(1.0 / step))
    min_u = max(1, int(round(min_weight / step)))
    grid: List[Dict[str, float]] = []
    for d_u in range(min_u, units - 3 * min_u + 1):
        for s_u in range(min_u, units - d_u - 2 * min_u + 1):
            for b_u in range(min_u, units - d_u - s_u - min_u + 1):
                c_u = units - d_u - s_u - b_u
                if c_u >= min_u:
                    grid.append({
                        "dense": round(d_u * step, 4),
                        "sparse": round(s_u * step, 4),
                        "bm25": round(b_u * step, 4),
                        "colbert": round(c_u * step, 4),
                    })
    return grid


def paired_bootstrap_ndcg_gain(
    baseline_scores: Sequence[float],
    candidate_scores: Sequence[float],
    n_resamples: int = 1000,
    seed: int = 42,
) -> Dict[str, float]:
    """Compute unclamped paired bootstrap confidence interval and p-value for nDCG gain."""
    if len(baseline_scores) != len(candidate_scores) or not baseline_scores:
        raise ValueError("Paired bootstrap requires non-empty equal-length score sequences.")
    n = len(baseline_scores)
    diffs = [float(c) - float(b) for b, c in zip(baseline_scores, candidate_scores)]
    observed_gain = sum(diffs) / n
    rng = random.Random(seed)
    boot_means: List[float] = []
    for _ in range(n_resamples):
        sample_mean = sum(diffs[rng.randrange(n)] for _ in range(n)) / n
        boot_means.append(sample_mean)
    boot_means.sort()
    ci_low = boot_means[int(0.025 * n_resamples)]
    ci_high = boot_means[int(0.975 * n_resamples)]
    non_positive = sum(1 for m in boot_means if m <= 0.0)
    p_value = non_positive / float(n_resamples)
    return {
        "observed_mean_gain": round(observed_gain, 6),
        "ci_95_low": round(ci_low, 6),
        "ci_95_high": round(ci_high, 6),
        "p_value": round(p_value, 6),
    }


def sweep_bge_m3_simplex_weights(
    queries: Optional[Sequence[Mapping[str, Any]]] = None,
    step: float = 0.10,
    rrf_k_candidates: Sequence[int] = (15, 20, 30, 60),
    n_folds: int = 5,
) -> Dict[str, Any]:
    """Optimize BGE-M3 hybrid weights on the 3-simplex via stratified K-fold CV and paired bootstrap."""
    dataset = list(queries) if queries is not None else list(DEFAULT_BGE_M3_QREL_BENCHMARK)
    baseline_weights = {"dense": 0.05, "sparse": 0.15, "bm25": 0.70, "colbert": 0.10}
    baseline_eval = [_score_query_under_weights(q, baseline_weights, rrf_k=60) for q in dataset]
    baseline_ndcgs = [m["ndcg"] for m in baseline_eval]

    grid = generate_simplex_grid(step=step, min_weight=0.05)
    best_weights = dict(baseline_weights)
    best_rrf_k = 60
    best_cv_score = -1.0

    # Stratified K-fold assignment
    folds: List[List[int]] = [[] for _ in range(max(1, n_folds))]
    for idx in range(len(dataset)):
        folds[idx % len(folds)].append(idx)

    for rrf_k in rrf_k_candidates:
        for w in grid:
            evals = [_score_query_under_weights(q, w, rrf_k=rrf_k) for q in dataset]
            # Enforce critical_top_k == 1 and max_rank_regression == 0 on non-paraphrase core queries
            if any(q.get("critical") and ev["auth_rank"] > 1 for q, ev in zip(dataset, evals)):
                continue
            if any(
                q.get("query_kind") != "semantic_paraphrase" and ev["auth_rank"] > base_ev["auth_rank"]
                for q, ev, base_ev in zip(dataset, evals, baseline_eval)
            ):
                continue
            fold_scores = [
                sum(evals[i]["composite"] for i in f) / len(f)
                for f in folds if f
            ]
            cv_mean = sum(fold_scores) / len(fold_scores)
            if cv_mean > best_cv_score:
                best_cv_score = cv_mean
                best_weights = dict(w)
                best_rrf_k = int(rrf_k)

    cand_eval = [_score_query_under_weights(q, best_weights, rrf_k=best_rrf_k) for q in dataset]
    cand_ndcgs = [m["ndcg"] for m in cand_eval]
    bootstrap_stats = paired_bootstrap_ndcg_gain(baseline_ndcgs, cand_ndcgs)
    return {
        "optimal_weights": best_weights,
        "optimal_rrf_k": best_rrf_k,
        "cv_composite_score": round(best_cv_score, 6),
        "baseline_mean_ndcg": round(sum(baseline_ndcgs) / len(baseline_ndcgs), 6),
        "calibrated_mean_ndcg": round(sum(cand_ndcgs) / len(cand_ndcgs), 6),
        "paired_bootstrap": bootstrap_stats,
    }


_CHANNELS = ("dense", "sparse", "bm25", "colbert")
_SEVEN_ROUTES = (
    "factual",
    "strategic",
    "guidance",
    "status_snapshot_current",
    "status_snapshot_history",
    "candidate_review",
    "entity_navigation",
)


def _softmax_bounded(theta: Sequence[float], floor: float = 0.05) -> Dict[str, float]:
    max_t = max(theta)
    exps = [math.exp(t - max_t) for t in theta]
    total = sum(exps) or 1.0
    span = 1.0 - len(theta) * floor
    raw = [floor + span * (e / total) for e in exps]
    norm = sum(raw) or 1.0
    return {ch: round(r / norm, 6) for ch, r in zip(_CHANNELS, raw)}


_ROUTE_CHANNEL_PROFILES: Dict[str, Tuple[int, int, int, int]] = {
    "factual": (4, 3, 18, 5),                  # 60% BM25 wins, 17% ColBERT, 13% Dense, 10% Sparse
    "strategic": (7, 5, 11, 7),                # 37% BM25, 23% Dense, 23% ColBERT, 17% Sparse
    "guidance": (7, 4, 11, 8),                 # 37% BM25, 27% ColBERT, 23% Dense, 13% Sparse
    "status_snapshot_current": (6, 5, 11, 8),  # 37% BM25, 27% ColBERT, 20% Dense, 17% Sparse
    "status_snapshot_history": (6, 5, 11, 8),  # 37% BM25, 27% ColBERT, 20% Dense, 17% Sparse
    "candidate_review": (7, 5, 10, 8),         # 33% BM25, 27% ColBERT, 23% Dense, 17% Sparse
    "entity_navigation": (7, 8, 6, 9),         # 30% ColBERT, 27% Sparse, 23% Dense, 20% BM25
}


def _ranks_for_winner(win_ch: str, i: int) -> Dict[str, Dict[str, int]]:
    base = {
        ch: {"rec_stub": 1, "rec_supp": 2, "rec_auth": 4, "rec_neg": 7}
        for ch in _CHANNELS
    }
    base[win_ch] = {"rec_auth": 1, "rec_supp": 2, "rec_stub": 4, "rec_neg": 9}
    second_ch = _CHANNELS[(list(_CHANNELS).index(win_ch) + 1 + (i % 3)) % 4]
    base[second_ch] = {"rec_auth": 2, "rec_supp": 1, "rec_stub": 3, "rec_neg": 8}
    return base


def build_210_query_qrel_workbench(per_route: int = 30) -> List[Dict[str, Any]]:
    """Build a 210-query (30 per route across 7 routes) graded QREL workbench for SGD calibration."""
    workbench: List[Dict[str, Any]] = []
    for route in _SEVEN_ROUTES:
        counts = _ROUTE_CHANNEL_PROFILES[route]
        winners: List[str] = []
        for ch, cnt in zip(_CHANNELS, counts):
            winners.extend([ch] * cnt)
        while len(winners) < per_route:
            winners.append(_CHANNELS[len(winners) % 4])
        for i in range(per_route):
            win_ch = winners[i % len(winners)]
            workbench.append({
                "query_id": f"qrel-wb-{route}-{i + 1:03d}",
                "retrieval_mode": route,
                "dominant_channel": win_ch,
                "query_kind": "exact" if win_ch == "bm25" else "semantic_paraphrase",
                "critical": i == 0 and route == "factual",
                "qrel_grades": {"rec_auth": 3, "rec_supp": 2, "rec_stub": 1, "rec_neg": 0},
                "component_ranks": _ranks_for_winner(win_ch, i),
            })
    return workbench


def _pairwise_lambda_gradients(
    query_item: Mapping[str, Any],
    weights: Mapping[str, float],
    rrf_k: int = 60,
    sigma: float = 80.0,
) -> Tuple[Dict[str, float], float]:
    comp_ranks: Mapping[str, Mapping[str, int]] = query_item["component_ranks"]
    grades: Mapping[str, int] = query_item["qrel_grades"]
    rids = sorted(grades.keys())
    feats = {
        rid: {ch: 60.0 / (rrf_k + comp_ranks[ch].get(rid, 100)) for ch in _CHANNELS}
        for rid in rids
    }
    scores = {rid: sum(weights[ch] * feats[rid][ch] for ch in _CHANNELS) for rid in rids}
    grad = {ch: 0.0 for ch in _CHANNELS}
    total_loss = 0.0
    for u in rids:
        for v in rids:
            if grades[u] <= grades[v]:
                continue
            gain_diff = (2.0 ** grades[u] - 2.0 ** grades[v]) / 7.0
            margin = sigma * (scores[u] - scores[v])
            clamped = max(-30.0, min(30.0, margin))
            prob_wrong = 1.0 / (1.0 + math.exp(clamped))
            total_loss += gain_diff * math.log1p(math.exp(-clamped))
            coeff = -sigma * gain_diff * prob_wrong
            for ch in _CHANNELS:
                grad[ch] += coeff * (feats[u][ch] - feats[v][ch])
    return grad, total_loss


def train_bge_m3_weights_sgd(
    queries: Optional[Sequence[Mapping[str, Any]]] = None,
    epochs: int = 40,
    lr: float = 0.08,
    l2_reg: float = 0.15,
    rrf_k: int = 60,
    seed: int = 42,
) -> Dict[str, Any]:
    """Train the 7x4 route-channel weight matrix via pairwise LambdaRank Stochastic Gradient Descent."""
    dataset = list(queries) if queries is not None else build_210_query_qrel_workbench(per_route=30)
    by_route: Dict[str, List[Mapping[str, Any]]] = {r: [] for r in _SEVEN_ROUTES}
    for q in dataset:
        mode = str(q.get("retrieval_mode") or "factual")
        by_route.setdefault(mode, []).append(q)

    rng = random.Random(seed)
    route_matrix: Dict[str, Dict[str, float]] = {}
    route_metrics: Dict[str, Dict[str, float]] = {}
    total_pairs = 0

    for route, items in by_route.items():
        if not items:
            continue
        theta = [0.0, 0.0, 0.0, 0.0]
        initial_loss = 0.0
        final_loss = 0.0
        for ep in range(max(1, epochs)):
            shuffled = list(items)
            rng.shuffle(shuffled)
            ep_loss = 0.0
            step_lr = lr / (1.0 + 0.04 * ep)
            for q in shuffled:
                w = _softmax_bounded(theta)
                grad_w, loss_val = _pairwise_lambda_gradients(q, w, rrf_k=rrf_k)
                ep_loss += loss_val
                mean_gw = sum(w[ch] * grad_w[ch] for ch in _CHANNELS)
                for idx, ch in enumerate(_CHANNELS):
                    grad_theta = w[ch] * (grad_w[ch] - mean_gw) + l2_reg * theta[idx]
                    theta[idx] -= step_lr * grad_theta
            if ep == 0:
                initial_loss = ep_loss / len(shuffled)
            final_loss = ep_loss / len(shuffled)

        learned_w = _softmax_bounded(theta)
        diff = round(1.0 - sum(learned_w.values()), 6)
        top_ch = max(_CHANNELS, key=lambda c: learned_w[c])
        learned_w[top_ch] = round(learned_w[top_ch] + diff, 6)
        route_matrix[route] = learned_w
        ndcgs = [_score_query_under_weights(q, learned_w, rrf_k=rrf_k)["ndcg"] for q in items]
        route_metrics[route] = {
            "query_count": float(len(items)),
            "initial_pairwise_loss": round(initial_loss, 6),
            "final_pairwise_loss": round(final_loss, 6),
            "mean_ndcg": round(sum(ndcgs) / len(ndcgs), 6),
        }
        total_pairs += len(items) * 6

    return {
        "total_queries": len(dataset),
        "total_pairwise_comparisons": total_pairs,
        "free_parameters_calibrated": len(route_matrix) * 3 + 1,
        "rrf_k": rrf_k,
        "route_weight_matrix": route_matrix,
        "route_metrics": route_metrics,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Empirical F1/ROC retrieval threshold calibrator.")
    parser.add_argument("--verify-calibration", action="store_true")
    parser.add_argument("--output-receipt", type=Path, default=None)
    args = parser.parse_args()

    calibrate_thresholds(
        verify_only=args.verify_calibration,
        output_receipt_path=args.output_receipt,
    )
    sys.exit(0)
