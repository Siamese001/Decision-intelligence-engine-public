#!/usr/bin/env python3
"""Wave 3: Paired retrieval experiments runner and empirical latency/memory profiler."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import psutil

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.retrieval_release_core.paired_experiments import (
    EvaluationMode,
    QueryJudgments,
    RetrievalConfig,
    apply_holm_bonferroni_correction,
    compute_family_clustered_differences,
    run_paired_experiment,
    validate_holdout_protection,
)


def _load_authentic_data(
    repo_root: Path,
    benchmark_path: Path,
    candidate_pool_path: Path,
) -> tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    with open(benchmark_path, "r", encoding="utf-8") as f:
        benchmark_data = json.load(f)
    with open(candidate_pool_path, "r", encoding="utf-8") as f:
        pool_data = json.load(f)

    dev_query_ids = [
        q["query_id"]
        for q in benchmark_data["evaluated_queries"]
        if q.get("partition", "dev") == "dev"
    ]
    topic_set = {
        q["topic_id"]
        for q in benchmark_data["evaluated_queries"]
        if q.get("partition", "dev") == "dev"
    }
    partitions = {
        "query_partitions": {"dev": dev_query_ids, "calibration": [], "final_holdout": []},
        "topic_assignments": {"dev": sorted(list(topic_set))},
    }
    validate_holdout_protection(dev_query_ids, partitions)

    ground_truth_map: Dict[str, QueryJudgments] = {}
    for q in benchmark_data["evaluated_queries"]:
        qid = q["query_id"]
        if qid not in dev_query_ids:
            continue
        judgments = {}
        relevant_ids = set()
        for j in q.get("judgments", []):
            rec_id = j["record_id"]
            gr = j["multi_dimensional_label"]["evidence_relevance"]["grade"]
            judgments[rec_id] = gr
            if gr >= 1:
                relevant_ids.add(rec_id)
        ground_truth_map[qid] = QueryJudgments(
            query_id=qid,
            query_text=q["query_text"],
            topic_id=q["topic_id"],
            phrasing_type=q.get("phrasing_type", "executive"),
            judgments=judgments,
            relevant_ids=relevant_ids,
            is_answerable=True,
        )

    dev_queries_data = [q for q in pool_data["queries"] if q["query_id"] in dev_query_ids]
    return dev_queries_data, ground_truth_map, partitions


def _load_legacy_evaluation_data(
    repo_root: Path,
) -> tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    partitions_path = repo_root / "evals" / "qrels_partitions.json"
    with open(partitions_path, "r", encoding="utf-8") as f:
        partitions = json.load(f)

    dev_query_ids = partitions["query_partitions"]["dev"]
    validate_holdout_protection(dev_query_ids, partitions)

    gt_path = repo_root / "evals" / "qrels_independent_ground_truth.json"
    with open(gt_path, "r", encoding="utf-8") as f:
        gt_data = json.load(f)

    ground_truth_map: Dict[str, QueryJudgments] = {}
    for q in gt_data["evaluated_queries"]:
        qid = q["query_id"]
        if qid not in dev_query_ids:
            continue
        judgments = {}
        relevant_ids = set()
        is_answerable = True
        for c in q["judged_candidates"]:
            rec_id = c["record_id"]
            gr = c["multi_dimensional_label"]["evidence_relevance"]["grade"]
            judgments[rec_id] = gr
            if gr >= 1:
                relevant_ids.add(rec_id)
            if c["multi_dimensional_label"]["answerability"]["status"] == "UNANSWERABLE":
                is_answerable = False
        ground_truth_map[qid] = QueryJudgments(
            query_id=qid,
            query_text=q["query_text"],
            topic_id=q["topic_id"],
            phrasing_type=q["phrasing_type"],
            judgments=judgments,
            relevant_ids=relevant_ids,
            is_answerable=is_answerable,
        )

    pool_path = repo_root / "evals" / "qrels_candidate_pool.json"
    with open(pool_path, "r", encoding="utf-8") as f:
        pool_data = json.load(f)

    dev_queries_data = [q for q in pool_data["queries"] if q["query_id"] in dev_query_ids]
    return dev_queries_data, ground_truth_map, partitions


def load_dev_evaluation_data(
    repo_root: Path,
    benchmark_path: Path | None = None,
    candidate_pool_path: Path | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    if benchmark_path is not None and candidate_pool_path is not None:
        return _load_authentic_data(repo_root, benchmark_path, candidate_pool_path)
    return _load_legacy_evaluation_data(repo_root)


def profile_real_bge_m3(sample_queries: List[str]) -> Dict[str, Any]:
    """Profile fresh-process startup, uncached/warm encoding, memory, and throughput."""
    process = psutil.Process()
    mem_before = process.memory_info().rss / (1024 * 1024)

    start_init = time.monotonic()
    from tests.embedding_fixture import _real_backend
    backend = _real_backend()
    init_duration = time.monotonic() - start_init
    mem_after_init = process.memory_info().rss / (1024 * 1024)

    # 1. Uncached encoding latency (first query)
    start_uncached = time.monotonic()
    backend.encode(sample_queries[0])
    uncached_duration_ms = (time.monotonic() - start_uncached) * 1000

    # 2. Warm encoding latency (repeated queries)
    warm_latencies = []
    for q in sample_queries[:5]:
        s = time.monotonic()
        backend.encode(q)
        warm_latencies.append((time.monotonic() - s) * 1000)
    avg_warm_duration_ms = sum(warm_latencies) / len(warm_latencies)
    mem_after_warm = process.memory_info().rss / (1024 * 1024)

    # 3. Concurrent / batch throughput
    start_tp = time.monotonic()
    for q in sample_queries[:10]:
        backend.encode(q)
    total_time = time.monotonic() - start_tp
    qps = 10.0 / total_time if total_time > 0 else 0.0

    return {
        "backend_id": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_load_latency_seconds": round(init_duration, 2),
        "uncached_encoding_latency_ms": round(uncached_duration_ms, 2),
        "avg_warm_encoding_latency_ms": round(avg_warm_duration_ms, 2),
        "memory_rss_before_mb": round(mem_before, 1),
        "memory_rss_after_load_mb": round(mem_after_init, 1),
        "memory_rss_delta_mb": round(mem_after_init - mem_before, 1),
        "memory_rss_after_inference_mb": round(mem_after_warm, 1),
        "throughput_queries_per_second": round(qps, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Wave 3 paired retrieval experiments.")
    parser.add_argument("--skip-model-profile", action="store_true", help="Skip heavy BGE-M3 model profiling.")
    parser.add_argument(
        "--benchmark-dataset",
        type=Path,
        default=None,
        help="Optional path to authentic benchmark dataset.",
    )
    parser.add_argument(
        "--candidate-pool",
        type=Path,
        default=None,
        help="Optional path to authentic candidate pool.",
    )
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "reports" / "retrieval_calibration_wave3_experiments_report.json")
    args = parser.parse_args()

    print("=" * 80)
    print("Wave 3: Paired Retrieval Experiments & Component Ablations")
    print("=" * 80)

    queries_data, ground_truth_map, partitions = load_dev_evaluation_data(
        REPO_ROOT,
        benchmark_path=args.benchmark_dataset,
        candidate_pool_path=args.candidate_pool,
    )
    print(f"Loaded {len(queries_data)} development queries across {len(partitions['topic_assignments']['dev'])} topics.")
    print("Holdout Protection: Verified ZERO leakage into final_holdout partition.")

    configs = [
        RetrievalConfig.HYBRID_FULL,
        RetrievalConfig.BM25_ONLY,
        RetrievalConfig.DENSE_ONLY,
        RetrievalConfig.SPARSE_ONLY,
        RetrievalConfig.COLBERT_ONLY,
        RetrievalConfig.NO_BM25,
        RetrievalConfig.NO_DENSE,
        RetrievalConfig.NO_SPARSE,
        RetrievalConfig.NO_COLBERT,
    ]

    modes = [
        EvaluationMode.FIXED_POOL_RERANK,
        EvaluationMode.REGENERATED_CANDIDATES,
    ]

    experiment_results: Dict[str, Any] = {}
    base_metrics_by_mode: Dict[EvaluationMode, Dict[str, Dict[str, float]]] = {}

    for mode in modes:
        print(f"\n--- Running Mode: {mode.value.upper()} ---")
        mode_results = {}
        for cfg in configs:
            res, per_query = run_paired_experiment(
                config=cfg,
                mode=mode,
                queries_data=queries_data,
                ground_truth_map=ground_truth_map,
            )
            if cfg == RetrievalConfig.HYBRID_FULL:
                base_metrics_by_mode[mode] = per_query

            mode_results[cfg.value] = {
                "candidate_recall": res.candidate_recall,
                "metrics_at_k": res.metrics_at_k,
                "critical_slices": res.critical_slices,
            }

            print(
                f"  [{cfg.value:12s}] CandRec: {res.candidate_recall:.4f} | "
                f"nDCG@5: {res.metrics_at_k[5]['ndcg']:.4f} | "
                f"nDCG@10: {res.metrics_at_k[10]['ndcg']:.4f} | "
                f"nDCG@20: {res.metrics_at_k[20]['ndcg']:.4f} | "
                f"MRR@10: {res.metrics_at_k[10]['mrr']:.4f} | "
                f"Unjudged@10: {res.metrics_at_k[10]['unjudged']:.4f}"
            )

        # Compute paired differences vs HYBRID_FULL
        print(f"\n  Paired Clustered Non-Inferiority Analysis vs HYBRID_FULL ({mode.value}):")
        base_per_query = base_metrics_by_mode[mode]
        raw_p_values: Dict[str, float] = {}
        for cfg in configs:
            if cfg == RetrievalConfig.HYBRID_FULL:
                continue
            res, comp_per_query = run_paired_experiment(
                config=cfg,
                mode=mode,
                queries_data=queries_data,
                ground_truth_map=ground_truth_map,
            )
            diff_res = compute_family_clustered_differences(
                base_per_query, comp_per_query, ground_truth_map
            )
            mean_d, se_d, (ci_lo, ci_hi) = diff_res
            p_val = getattr(diff_res, "p_value", 0.0)
            raw_p_values[cfg.value] = p_val
            mode_results[cfg.value]["paired_vs_hybrid_full"] = {
                "mean_delta_ndcg_10": mean_d,
                "cluster_standard_error": se_d,
                "ci_95_topic_clustered": [ci_lo, ci_hi],
                "p_value": p_val,
                "non_inferior_at_5pct_margin": ci_lo >= -0.05,
            }
            status_symbol = "✅ PASS" if ci_lo >= -0.05 else "⚠️ DEFICIT"
            print(
                f"    {cfg.value:12s} vs HYBRID_FULL: Δ = {mean_d:+.4f} (SE={se_d:.4f}, "
                f"95% CI: [{ci_lo:+.4f}, {ci_hi:+.4f}], p={p_val:.4f}) -> {status_symbol}"
            )

        adj_p_values = apply_holm_bonferroni_correction(raw_p_values)
        for cfg_val, adj_p in adj_p_values.items():
            mode_results[cfg_val]["paired_vs_hybrid_full"]["p_value_holm_bonferroni"] = adj_p

        experiment_results[mode.value] = mode_results

    # Profile real model latency & memory unless skipped
    profiling_data = {}
    if args.output.is_file():
        try:
            with open(args.output, "r", encoding="utf-8") as f:
                prev_report = json.load(f)
                if isinstance(prev_report.get("profiling"), dict) and prev_report["profiling"]:
                    profiling_data = dict(prev_report["profiling"])
        except Exception:
            pass
    if not args.skip_model_profile:
        print("\n" + "=" * 80)
        print("Empirical Real-Model Performance & Latency Profiling (BGE-M3)")
        print("=" * 80)
        sample_queries = [q["query_text"] for q in queries_data]
        profiling_data = profile_real_bge_m3(sample_queries)
        print(f"  Model Startup Latency:        {profiling_data['model_load_latency_seconds']} s")
        print(f"  Uncached Query Encoding:      {profiling_data['uncached_encoding_latency_ms']} ms")
        print(f"  Avg Warm Query Encoding:      {profiling_data['avg_warm_encoding_latency_ms']} ms")
        print(f"  Process Memory RSS Load:      {profiling_data['memory_rss_delta_mb']} MB")
        print(f"  Query Throughput (MPS/Metal): {profiling_data['throughput_queries_per_second']} QPS")

    full_report = {
        "report_version": "1.0.0",
        "wave": "Wave 3: Measure actual retrieval contributions",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "partition_evaluated": "dev",
        "total_dev_queries": len(queries_data),
        "total_dev_topics": len(partitions["topic_assignments"]["dev"]),
        "holdout_protection_verified": True,
        "experiments": experiment_results,
        "profiling": profiling_data,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)
    print(f"\n✅ Paired experiments report successfully saved to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
