"""Wave 3D: Simplex Grid Weight Calibration & Decision Gate Evaluation

Re-scores all 4-engine pooled candidates under 1,771 simplex weight configurations
plus component ablations against the grounded human/audited QRELS judgments.

Computes mean graded NDCG@10 and MRR@10 for each configuration, identifies the
empirical global optimum on the 4-simplex, evaluates component ablation defenses,
and enforces the 0.02 delta decision gate.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from dual_pipeline.index import BM25_RRF_WEIGHTS
from dual_pipeline.retrieval_release_core.metrics import (
    compute_mrr_at_k,
    compute_ndcg_at_k,
    standard_ndcg_at_k,
    standard_reciprocal_rank,
)


def classify_query_intent(query_text: str) -> str:
    """Classifies query intent into EXACT_KEYWORD, SEMANTIC_NARRATIVE, or HYBRID_STANDARD.
    
    Categories:
      - EXACT_KEYWORD: Exact IDs, code identifiers, quoted literals, uppercase acronyms, or short keywords.
      - SEMANTIC_NARRATIVE: Natural language questions, exploratory explanations, long multi-clause descriptions.
      - HYBRID_STANDARD: Balanced entity-relation searches or mixed terminology queries.
    """
    text = query_text.strip()
    if not text:
        return "HYBRID_STANDARD"

    # Exact keywords: IDs (e.g. c_wal_dec, PO2000622222, urn:nee:...), quotes ("..."), acronyms (WAL, FTS5, BGE, AWS), short code tokens
    has_quotes = (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'"))
    has_code_tokens = bool(re.search(r"^[A-Za-z0-9_-]+:[A-Za-z0-9_.:-]+$", text)) or bool(re.search(r"^[A-Z0-9_-]{3,25}$", text))
    words = text.split()

    # Check for question words or narrative markers
    question_markers = {
        "what", "why", "how", "when", "who", "where", "explain",
        "describe", "summarize", "rationale", "impact", "relationship"
    }
    has_question_word = any(w.lower().rstrip("?:;,.") in question_markers for w in words)
    is_long_narrative = len(words) >= 6

    if has_quotes or has_code_tokens or (len(words) <= 2 and not has_question_word):
        return "EXACT_KEYWORD"
    elif has_question_word or is_long_narrative:
        return "SEMANTIC_NARRATIVE"
    else:
        return "HYBRID_STANDARD"


def calculate_dynamic_query_weights(
    query_text: str,
    base_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Dynamically rebalances BM25, Dense, Sparse, and ColBERT fusion weights based on query intent.

    Enforces strict simplex constraint: sum(weights.values()) == 1.0.
    """
    intent = classify_query_intent(query_text)

    if intent == "EXACT_KEYWORD":
        # High BM25, moderate sparse, low dense/colbert
        weights = {"dense": 0.05, "sparse": 0.15, "bm25": 0.75, "colbert": 0.05}
    elif intent == "SEMANTIC_NARRATIVE":
        # High dense and colbert, low BM25, moderate sparse
        weights = {"dense": 0.40, "sparse": 0.15, "bm25": 0.15, "colbert": 0.30}
    else:
        # Default production calibrated weights
        weights = dict(base_weights or BM25_RRF_WEIGHTS)

    total = sum(weights.values())
    return {k: round(v / total, 4) for k, v in weights.items()}


def generate_simplex_grid(step: float = 0.05) -> list[dict[str, float]]:
    """Generate all points on the 4-simplex summing to 1.0."""
    steps = int(round(1.0 / step)) + 1
    configs: list[dict[str, float]] = []

    for d in range(steps):
        for s in range(steps - d):
            for b in range(steps - d - s):
                c = steps - 1 - d - s - b
                w = {
                    "dense": round(d * step, 4),
                    "sparse": round(s * step, 4),
                    "bm25": round(b * step, 4),
                    "colbert": round(c * step, 4),
                }
                if abs(sum(w.values()) - 1.0) < 1e-4:
                    configs.append(w)
    return configs


def evaluate_weight_configuration(
    queries: list[dict[str, Any]],
    weights: dict[str, float],
    rrf_k: int = 60,
    k: int = 10,
) -> tuple[float, float]:
    """Re-rank candidate pools for all queries under given weights and return (mean_ndcg, mean_mrr)."""
    ndcgs: list[float] = []
    mrrs: list[float] = []

    for q in queries:
        candidates = q.get("judged_candidates", [])
        if not candidates:
            continue

        scored: list[tuple[float, int]] = []
        for cand in candidates:
            grade = cand.get("grade", 0)
            score = 0.0
            if cand.get("dense_rank") is not None and "dense" in weights:
                score += weights["dense"] / (rrf_k + cand["dense_rank"])
            if cand.get("sparse_rank") is not None and "sparse" in weights:
                score += weights["sparse"] / (rrf_k + cand["sparse_rank"])
            if cand.get("bm25_rank") is not None and "bm25" in weights:
                score += weights["bm25"] / (rrf_k + cand["bm25_rank"])
            if cand.get("colbert_rank") is not None and "colbert" in weights:
                score += weights["colbert"] / (rrf_k + cand["colbert_rank"])

            scored.append((score, grade))

        # Re-sort candidates by fused score (descending)
        scored.sort(key=lambda x: -x[0])
        reranked_grades = [g for _, g in scored]

        ndcgs.append(compute_ndcg_at_k(reranked_grades, k=k))
        mrrs.append(compute_mrr_at_k(reranked_grades, k=k))

    return (float(np.mean(ndcgs)), float(np.mean(mrrs))) if ndcgs else (0.0, 0.0)


def evaluate_dynamic_weight_configuration(
    queries: list[dict[str, Any]],
    base_weights: dict[str, float] | None = None,
    rrf_k: int = 60,
    k: int = 10,
) -> tuple[float, float]:
    """Re-ranks candidate pools with query-intent dynamic weight adaptation."""
    ndcgs: list[float] = []
    mrrs: list[float] = []

    for q in queries:
        candidates = q.get("judged_candidates", [])
        if not candidates:
            continue

        q_text = q.get("query_text", "")
        weights = calculate_dynamic_query_weights(q_text, base_weights)

        scored: list[tuple[float, int]] = []
        for cand in candidates:
            grade = cand.get("grade", 0)
            score = 0.0
            if cand.get("dense_rank") is not None and "dense" in weights:
                score += weights["dense"] / (rrf_k + cand["dense_rank"])
            if cand.get("sparse_rank") is not None and "sparse" in weights:
                score += weights["sparse"] / (rrf_k + cand["sparse_rank"])
            if cand.get("bm25_rank") is not None and "bm25" in weights:
                score += weights["bm25"] / (rrf_k + cand["bm25_rank"])
            if cand.get("colbert_rank") is not None and "colbert" in weights:
                score += weights["colbert"] / (rrf_k + cand["colbert_rank"])

            scored.append((score, grade))

        scored.sort(key=lambda x: -x[0])
        reranked_grades = [g for _, g in scored]

        ndcgs.append(compute_ndcg_at_k(reranked_grades, k=k))
        mrrs.append(compute_mrr_at_k(reranked_grades, k=k))

    return (float(np.mean(ndcgs)), float(np.mean(mrrs))) if ndcgs else (0.0, 0.0)


def run_weight_calibration(
    judgments_path: Path,
    output_path: Path,
    grid_step: float = 0.05,
    partitions_path: Path | None = None,
) -> dict[str, Any]:
    print("=" * 80)
    print(f"Wave 3D: Simplex Grid Weight Calibration & Gate Evaluation (Step: {grid_step})")
    print("=" * 80)
    start_time = time.time()

    with open(judgments_path, "r", encoding="utf-8") as f:
        judgments_data = json.load(f)

    all_queries = judgments_data.get("evaluated_queries", [])
    print(f"Loaded {len(all_queries)} evaluated queries with graded candidates.")

    # Partition Isolation: Strictly exclude final_holdout queries from calibration
    if partitions_path is None:
        partitions_path = judgments_path.parent / "qrels_partitions.json"
    
    holdout_query_ids: set[str] = set()
    if partitions_path.exists():
        with open(partitions_path, "r", encoding="utf-8") as f:
            partitions_data = json.load(f)
        holdout_query_ids = set(
            partitions_data.get("query_partitions", {}).get("final_holdout", [])
        )

    queries = [q for q in all_queries if q.get("query_id") not in holdout_query_ids]
    print(
        f"Partitions applied: {len(queries)} dev+calibration queries selected for sweep, "
        f"{len(all_queries) - len(queries)} final_holdout queries isolated."
    )

    # 1. Evaluate Canonical Production Weights
    canonical_ndcg, canonical_mrr = evaluate_weight_configuration(queries, BM25_RRF_WEIGHTS)
    print("\n[Canonical Production Weights]")
    print(f"Weights: {BM25_RRF_WEIGHTS}")
    print(f"Mean NDCG@10: {canonical_ndcg:.4f}")
    print(f"Mean MRR@10:  {canonical_mrr:.4f}")

    # 2. Key Ablation Baselines
    ablations = {
        "dense_only": {"dense": 1.0, "sparse": 0.0, "bm25": 0.0, "colbert": 0.0},
        "sparse_only": {"dense": 0.0, "sparse": 1.0, "bm25": 0.0, "colbert": 0.0},
        "bm25_only": {"dense": 0.0, "sparse": 0.0, "bm25": 1.0, "colbert": 0.0},
        "colbert_only": {"dense": 0.0, "sparse": 0.0, "bm25": 0.0, "colbert": 1.0},
        "no_bm25 (dense+sparse+colbert)": {"dense": 0.40, "sparse": 0.20, "bm25": 0.0, "colbert": 0.40},
        "no_colbert (dense+sparse+bm25)": {"dense": 0.50, "sparse": 0.25, "bm25": 0.25, "colbert": 0.0},
        "equal_weights": {"dense": 0.25, "sparse": 0.25, "bm25": 0.25, "colbert": 0.25},
    }

    ablation_results: dict[str, Any] = {}
    print("\n[Component Ablation Benchmarks]")
    for name, w in ablations.items():
        ndcg, mrr = evaluate_weight_configuration(queries, w)
        diff = ndcg - canonical_ndcg
        print(f"  - {name:32s}: NDCG@10 = {ndcg:.4f} ({diff:+.4f}) | MRR = {mrr:.4f}")
        ablation_results[name] = {
            "weights": w,
            "mean_ndcg_10": round(ndcg, 4),
            "mean_mrr_10": round(mrr, 4),
            "delta_vs_canonical": round(diff, 4),
        }

    # 3. Simplex Grid Search Sweep
    grid_configs = generate_simplex_grid(step=grid_step)
    print(f"\nEvaluating {len(grid_configs)} simplex grid configurations with progress tracking...")

    best_ndcg = -1.0
    best_config: dict[str, float] = {}
    best_mrr = 0.0
    all_sweep_results: list[dict[str, Any]] = []

    with tqdm(total=len(grid_configs), desc="Sweeping Simplex Grid", unit="config") as pbar:
        for w in grid_configs:
            ndcg, mrr = evaluate_weight_configuration(queries, w)
            if ndcg > best_ndcg:
                best_ndcg = ndcg
                best_mrr = mrr
                best_config = w

            all_sweep_results.append({
                "weights": w,
                "mean_ndcg_10": round(ndcg, 4),
                "mean_mrr_10": round(mrr, 4),
            })
            pbar.update(1)

    delta_ndcg = best_ndcg - canonical_ndcg
    is_gate_passed = delta_ndcg < 0.02
    gate_verdict = "PASS_VALIDATED" if is_gate_passed else "RECALIBRATION_REQUIRED"

    elapsed = time.time() - start_time

    # Sort sweep results
    all_sweep_results.sort(key=lambda x: -x["mean_ndcg_10"])
    top_10_configs = all_sweep_results[:10]

    payload = {
        "calibration_contract": "urn:nee:qrels:weight-calibration:1.0.0",
        "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "partition_strategy": "dev_plus_calibration_strict",
        "total_queries_loaded": len(all_queries),
        "calibration_queries_count": len(queries),
        "calibration_query_ids": [q["query_id"] for q in queries if "query_id" in q],
        "excluded_holdout_queries_count": len(all_queries) - len(queries),
        "excluded_holdout_query_ids": sorted(list(holdout_query_ids)),
        "total_configurations_evaluated": len(grid_configs),
        "grid_step": grid_step,
        "canonical_weights": BM25_RRF_WEIGHTS,
        "canonical_metrics": {
            "mean_ndcg_10": round(canonical_ndcg, 4),
            "mean_mrr_10": round(canonical_mrr, 4),
        },
        "optimal_configuration": {
            "weights": best_config,
            "mean_ndcg_10": round(best_ndcg, 4),
            "mean_mrr_10": round(best_mrr, 4),
            "delta_ndcg_vs_canonical": round(delta_ndcg, 4),
        },
        "decision_gate": {
            "criterion": "Delta NDCG@10 (optimal - canonical) < 0.02",
            "threshold": 0.02,
            "actual_delta": round(delta_ndcg, 4),
            "verdict": gate_verdict,
            "is_validated": is_gate_passed,
        },
        "ablations": ablation_results,
        "top_10_configurations": top_10_configs,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    receipt_path = output_path.with_name("wave3d_calibration_receipt.json")
    receipt = {
        "wave": "Wave 3D",
        "status": "COMPLETE",
        "partition_strategy": "dev_plus_calibration_strict",
        "calibration_queries_count": len(queries),
        "excluded_holdout_queries_count": len(all_queries) - len(queries),
        "canonical_weights": BM25_RRF_WEIGHTS,
        "canonical_ndcg_10": round(canonical_ndcg, 4),
        "optimal_weights": best_config,
        "optimal_ndcg_10": round(best_ndcg, 4),
        "delta_ndcg": round(delta_ndcg, 4),
        "gate_verdict": gate_verdict,
        "output_file": str(output_path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 2),
    }
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)

    print("\n" + "=" * 80)
    print("WEIGHT CALIBRATION & GATE VERDICT")
    print("=" * 80)
    print(f"Canonical Weights:  {BM25_RRF_WEIGHTS} -> NDCG@10 = {canonical_ndcg:.4f} | MRR@10 = {canonical_mrr:.4f}")
    print(f"Optimal Weights:    {best_config} -> NDCG@10 = {best_ndcg:.4f} | MRR@10 = {best_mrr:.4f}")
    print(f"Delta NDCG@10:      {delta_ndcg:+.4f}")
    print(f"Decision Gate:      {gate_verdict} (Threshold: Delta < 0.02)")
    print("\nTop 5 Weight Configurations:")
    for rank, c in enumerate(top_10_configs[:5], 1):
        print(f"  #{rank}: NDCG@10 = {c['mean_ndcg_10']:.4f} | MRR = {c['mean_mrr_10']:.4f} | Weights: {c['weights']}")
    print(f"\nSaved calibration results to: {output_path}")
    print(f"Recorded receipt to:          {receipt_path}")
    print(f"Total elapsed:                {elapsed:.2f}s")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wave 3D Weight Calibration")
    parser.add_argument(
        "--judgments",
        type=Path,
        default=Path("evals/qrels_graded_judgments.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/qrels_weight_calibration_results.json"),
    )
    parser.add_argument(
        "--partitions",
        type=Path,
        default=None,
        help="Optional path to qrels_partitions.json to isolate final_holdout",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.05,
        help="Simplex grid search step size (default 0.05 = 1,771 configurations)",
    )
    args = parser.parse_args()
    run_weight_calibration(args.judgments, args.output, args.step, partitions_path=args.partitions)
