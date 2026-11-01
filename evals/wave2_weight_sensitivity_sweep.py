"""Wave 2: Grid-Search Weight Sensitivity Sweep on the 4-Simplex.

Tests ~1,771 weight configurations across (dense, sparse, bm25, colbert) summing to 1.0
with grid step 0.05, plus the canonical production weights (0.34, 0.17, 0.15, 0.34).
Evaluates all 133 adversarial queries from Wave 1 against real sidecar records.
Computes MRR, Hit@1, Hit@3, and worst rank to determine the Pareto frontier.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from dual_pipeline.graph_retrieval import governed_bm25_assertion_ranks
from dual_pipeline.index import (
    HybridRetriever,
    get_backend,
)
from dual_pipeline.release import ReleaseManager


def load_wave1_queries(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("all_results", [])


def collect_query_candidates(
    retriever: HybridRetriever,
    graph_path: Path,
    queries: list[dict[str, Any]],
    cache_path: Path | None = None,
) -> list[dict[str, Any]]:
    if cache_path and cache_path.exists():
        print(f"Loading cached query candidates from {cache_path}...")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    print(f"Retrieving candidate sets for {len(queries)} adversarial queries...")
    cached_candidates: list[dict[str, Any]] = []

    for q in tqdm(queries, desc="Retrieving candidates", unit="query"):
        query_text = q["query_text"]
        target_id = q["record_id"]
        q_type = q.get("query_type", "unknown")

        try:
            ranks_by_lane, _ = governed_bm25_assertion_ranks(
                question=query_text,
                graph_path=graph_path,
                sidecar_path=retriever.sidecar_path,
                allowed_lanes=["approved"],
                retrieval_mode=None,
                classification="internal",
                audiences=["internal_consulting"],
            )
            bm25_ranks = ranks_by_lane.get("approved", {})

            result = retriever.retrieve_lane(
                query=query_text,
                lane="approved",
                classification="internal",
                audiences=["internal_consulting"],
                bm25_assertion_ranks=bm25_ranks,
                top_k=50,
            )

            # Extract candidates with their component ranks
            candidates: list[dict[str, Any]] = []
            for item in result.get("results", []):
                candidates.append({
                    "record_id": item["record_id"],
                    "dense_rank": item.get("dense_rank"),
                    "sparse_rank": item.get("sparse_rank"),
                    "bm25_rank": item.get("bm25_rank"),
                    "colbert_rank": item.get("colbert_rank"),
                    "fused_rank": item.get("fused_rank"),
                })

            cached_candidates.append({
                "query_text": query_text,
                "target_record_id": target_id,
                "query_type": q_type,
                "title": q.get("title", ""),
                "candidates": candidates,
            })
        except Exception as exc:
            cached_candidates.append({
                "query_text": query_text,
                "target_record_id": target_id,
                "query_type": q_type,
                "title": q.get("title", ""),
                "error": str(exc),
                "candidates": [],
            })

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cached_candidates, f, indent=2)
        print(f"Cached candidate data saved to {cache_path}")

    return cached_candidates


def generate_simplex_grid(step: float = 0.05) -> list[tuple[float, float, float, float]]:
    """Generate all points on the 4-simplex with grid step summing to 1.0."""
    grid: list[tuple[float, float, float, float]] = []
    n_steps = int(round(1.0 / step))
    for i in range(n_steps + 1):
        for j in range(n_steps + 1 - i):
            for k in range(n_steps + 1 - i - j):
                l = n_steps - i - j - k
                w = (round(i * step, 4), round(j * step, 4), round(k * step, 4), round(l * step, 4))
                grid.append(w)
    return grid


def evaluate_weights(
    weights: tuple[float, float, float, float],
    query_data: list[dict[str, Any]],
    rrf_k: int = 60,
) -> dict[str, float]:
    """Score all queries under given weights and return performance metrics."""
    w_dense, w_sparse, w_bm25, w_colbert = weights
    reciprocal_ranks: list[float] = []
    hit_1: int = 0
    hit_3: int = 0
    worst_rank: int = 1

    for q in query_data:
        target_id = q["target_record_id"]
        candidates = q.get("candidates", [])
        if not candidates:
            reciprocal_ranks.append(0.0)
            worst_rank = max(worst_rank, 51)
            continue

        scored: list[tuple[float, str]] = []
        for c in candidates:
            score = 0.0
            if c["dense_rank"] is not None:
                score += w_dense / (rrf_k + c["dense_rank"])
            if c["sparse_rank"] is not None:
                score += w_sparse / (rrf_k + c["sparse_rank"])
            if c["bm25_rank"] is not None:
                score += w_bm25 / (rrf_k + c["bm25_rank"])
            if c["colbert_rank"] is not None:
                score += w_colbert / (rrf_k + c["colbert_rank"])
            scored.append((score, c["record_id"]))

        scored.sort(key=lambda item: (-item[0], item[1]))

        # Find rank of target
        target_rank = 51  # Not in top-50
        for rank, (_score, rec_id) in enumerate(scored, 1):
            if rec_id == target_id:
                target_rank = rank
                break

        worst_rank = max(worst_rank, target_rank)
        if target_rank == 1:
            hit_1 += 1
        if target_rank <= 3:
            hit_3 += 1
        reciprocal_ranks.append(1.0 / target_rank if target_rank <= 50 else 0.0)

    n = len(query_data)
    mrr = sum(reciprocal_ranks) / n if n > 0 else 0.0
    return {
        "mrr": round(mrr, 5),
        "hit_1": hit_1,
        "hit_1_pct": round(hit_1 / n * 100, 2),
        "hit_3": hit_3,
        "hit_3_pct": round(hit_3 / n * 100, 2),
        "worst_rank": worst_rank,
    }


def main():
    wave1_path = Path("evals/wave1_weight_sensitivity_results.json")
    if not wave1_path.exists():
        print(f"Error: {wave1_path} does not exist.")
        sys.exit(1)

    queries = load_wave1_queries(wave1_path)
    print(f"Loaded {len(queries)} queries from Wave 1.")

    runtime_root = Path(".local/main/runtime-bge-m3-sherpa-strategic-20260729")

    # Load backend
    backend = get_backend(
        "bge-m3-dev",
        allow_dev_backend=True,
        model_path="C:\\NEE\\models\\bge-m3\\5617a9f61b028005a4858fdac845db406aefb181",
        model_revision="5617a9f61b028005a4858fdac845db406aefb181",
        model_manifest="C:\\NEE\\models\\bge-m3\\bge-m3-manifest-5617a9f61b028005a4858fdac845db406aefb181.json",
    )

    manager = ReleaseManager(runtime_root, Path("dummy.db"))
    pair = manager.active_pair()
    sidecar_path = manager._resolve(pair["sidecar_path"])
    graph_path = manager._resolve(pair.get("graph_path") or pair["graph_snapshot"])

    retriever = HybridRetriever(sidecar_path, backend)

    # Collect or load candidates
    cache_path = Path("evals/wave2_cached_candidates.json")
    query_candidates = collect_query_candidates(
        retriever=retriever,
        graph_path=graph_path,
        queries=queries,
        cache_path=cache_path,
    )

    # Filter out any query with errors
    valid_queries = [q for q in query_candidates if "error" not in q and q.get("candidates")]
    print(f"Total valid queries with candidates: {len(valid_queries)} / {len(queries)}")

    # 1. Evaluate Canonical Production Weights
    canonical_w = (0.34, 0.17, 0.15, 0.34)
    canonical_metrics = evaluate_weights(canonical_w, valid_queries)
    print(f"\n{'='*70}")
    print(f"CANONICAL WEIGHTS {canonical_w}:")
    print(f"  MRR:        {canonical_metrics['mrr']:.5f}")
    print(f"  Hit@1:      {canonical_metrics['hit_1']}/{len(valid_queries)} ({canonical_metrics['hit_1_pct']}%)")
    print(f"  Hit@3:      {canonical_metrics['hit_3']}/{len(valid_queries)} ({canonical_metrics['hit_3_pct']}%)")
    print(f"  Worst rank: {canonical_metrics['worst_rank']}")
    print(f"{'='*70}\n")

    # 2. Generate and evaluate grid
    grid = generate_simplex_grid(step=0.05)
    print(f"Running grid search over {len(grid)} weight configurations on 4-simplex...")

    results: list[dict[str, Any]] = []
    for w in tqdm(grid, desc="Simplex sweep", unit="config"):
        m = evaluate_weights(w, valid_queries)
        results.append({
            "weights": {
                "dense": w[0],
                "sparse": w[1],
                "bm25": w[2],
                "colbert": w[3],
            },
            **m,
        })

    # Sort configurations by MRR descending, then Hit@1 descending
    results.sort(key=lambda r: (r["mrr"], r["hit_1_pct"], -r["worst_rank"]), reverse=True)

    max_mrr = results[0]["mrr"]
    max_hit1_pct = max(r["hit_1_pct"] for r in results)
    min_mrr = results[-1]["mrr"]

    # Compute canonical rank in grid
    canonical_rank_in_grid = sum(1 for r in results if r["mrr"] > canonical_metrics["mrr"]) + 1
    percentile = (1 - (canonical_rank_in_grid - 1) / len(results)) * 100

    print("\n--- GRID SEARCH SUMMARY ---")
    print(f"Total configurations: {len(results)}")
    print(f"MRR Range:            {min_mrr:.5f} -> {max_mrr:.5f}")
    print(f"Max Hit@1%:           {max_hit1_pct:.2f}%")
    print(f"Canonical MRR:        {canonical_metrics['mrr']:.5f}")
    print(f"Canonical Rank:       #{canonical_rank_in_grid} out of {len(results)} ({percentile:.1f}th percentile)")

    # Identify Pareto frontier
    # Pareto optimal if no other config has BOTH strictly higher MRR and strictly higher Hit@1%
    pareto_frontier: list[dict[str, Any]] = []
    for r in results:
        dominated = False
        for other in results:
            if (other["mrr"] > r["mrr"] and other["hit_1_pct"] >= r["hit_1_pct"]) or \
               (other["mrr"] >= r["mrr"] and other["hit_1_pct"] > r["hit_1_pct"]):
                dominated = True
                break
        if not dominated:
            pareto_frontier.append(r)

    print(f"\nPareto frontier size: {len(pareto_frontier)} configurations")
    print("Top Pareto optimal configurations:")
    for idx, p in enumerate(pareto_frontier[:10], 1):
        w = p["weights"]
        print(f"  {idx}. d={w['dense']:.2f}, s={w['sparse']:.2f}, b={w['bm25']:.2f}, c={w['colbert']:.2f} | "
              f"MRR={p['mrr']:.5f} | Hit@1={p['hit_1_pct']:.1f}% | Hit@3={p['hit_3_pct']:.1f}% | Worst={p['worst_rank']}")

    # Ablation studies
    print("\n--- ABLATION: COMPONENT REMOVAL (Zeroing each lane) ---")
    ablations = [
        ("Zero BM25 (dense=0.40, sparse=0.20, bm25=0.00, colbert=0.40)", (0.40, 0.20, 0.00, 0.40)),
        ("Zero Sparse (dense=0.40, sparse=0.00, bm25=0.20, colbert=0.40)", (0.40, 0.00, 0.20, 0.40)),
        ("Zero Dense (dense=0.00, sparse=0.25, bm25=0.25, colbert=0.50)", (0.00, 0.25, 0.25, 0.50)),
        ("Zero ColBERT (dense=0.50, sparse=0.25, bm25=0.25, colbert=0.00)", (0.50, 0.25, 0.25, 0.00)),
        ("BM25 Only (dense=0.00, sparse=0.00, bm25=1.00, colbert=0.00)", (0.00, 0.00, 1.00, 0.00)),
        ("Dense Only (dense=1.00, sparse=0.00, bm25=0.00, colbert=0.00)", (1.00, 0.00, 0.00, 0.00)),
        ("ColBERT Only (dense=0.00, sparse=0.00, bm25=0.00, colbert=1.00)", (0.00, 0.00, 0.00, 1.00)),
    ]
    for name, w in ablations:
        m = evaluate_weights(w, valid_queries)
        print(f"  {name:65s} -> MRR={m['mrr']:.5f}, Hit@1={m['hit_1_pct']}%, Worst={m['worst_rank']}")

    # Save results
    output_path = Path("evals/wave2_weight_sweep_results.json")
    output_data = {
        "metadata": {
            "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_queries": len(valid_queries),
            "grid_step": 0.05,
            "grid_size": len(grid),
            "canonical_weights": {
                "dense": canonical_w[0],
                "sparse": canonical_w[1],
                "bm25": canonical_w[2],
                "colbert": canonical_w[3],
            },
            "canonical_metrics": canonical_metrics,
            "canonical_rank": canonical_rank_in_grid,
            "canonical_percentile": round(percentile, 2),
            "max_mrr": max_mrr,
            "max_hit1_pct": max_hit1_pct,
            "pareto_size": len(pareto_frontier),
        },
        "canonical_metrics": canonical_metrics,
        "pareto_frontier": pareto_frontier,
        "top_50_configs": results[:50],
        "ablations": [
            {"name": name, "weights": w, **evaluate_weights(w, valid_queries)}
            for name, w in ablations
        ],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    hasher = hashlib.sha256()
    with open(output_path, "rb") as f:
        hasher.update(f.read())
    digest = hasher.hexdigest()
    print(f"\nWave 2 results saved to {output_path}")
    print(f"Artifact SHA256: {digest}")


if __name__ == "__main__":
    main()
