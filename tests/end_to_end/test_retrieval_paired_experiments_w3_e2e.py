"""End-to-end lifecycle tests for Wave 3 paired experiments, ablations, and profiling."""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.retrieval_release_core.paired_experiments import (
    EvaluationMode,
    RetrievalConfig,
    compute_family_clustered_differences,
    run_paired_experiment,
)
from tools.run_paired_retrieval_experiments import (
    load_dev_evaluation_data,
    profile_real_bge_m3,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_e2e_dev_partition_paired_experiments_lifecycle():
    queries_data, ground_truth_map, partitions = load_dev_evaluation_data(REPO_ROOT)
    assert len(queries_data) == 23
    assert len(partitions["topic_assignments"]["dev"]) >= 60

    # Run HYBRID_FULL vs BM25_ONLY in REGENERATED_CANDIDATES mode
    hybrid_res, hybrid_per_q = run_paired_experiment(
        RetrievalConfig.HYBRID_FULL,
        EvaluationMode.REGENERATED_CANDIDATES,
        queries_data,
        ground_truth_map,
    )
    bm25_res, bm25_per_q = run_paired_experiment(
        RetrievalConfig.BM25_ONLY,
        EvaluationMode.REGENERATED_CANDIDATES,
        queries_data,
        ground_truth_map,
    )

    # Verify candidate recall and metric bounds on dev partition
    assert hybrid_res.candidate_recall == 1.0
    assert bm25_res.candidate_recall < hybrid_res.candidate_recall
    assert bm25_res.candidate_recall >= 0.80
    assert hybrid_res.metrics_at_k[10]["ndcg"] > 0.30
    assert bm25_res.metrics_at_k[10]["ndcg"] > 0.30

    # Compute family clustered differences
    diff_res = compute_family_clustered_differences(
        hybrid_per_q, bm25_per_q, ground_truth_map
    )
    mean_d, se_d, (ci_lo, ci_hi) = diff_res
    assert se_d > 0.0
    assert ci_lo <= mean_d <= ci_hi
    assert hasattr(diff_res, "p_value")


def test_e2e_experiments_report_artifact_verification():
    report_path = REPO_ROOT / "reports" / "retrieval_calibration_wave3_experiments_report.json"
    assert report_path.is_file(), f"Report not found at {report_path}"

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    assert report["report_version"] == "1.0.0"
    assert report["wave"] == "Wave 3: Measure actual retrieval contributions"
    assert report["partition_evaluated"] == "dev"
    assert report["total_dev_queries"] == 23
    assert report["total_dev_topics"] >= 60
    assert report["holdout_protection_verified"] is True

    # Check that both modes are evaluated
    exps = report["experiments"]
    assert "fixed_pool_rerank" in exps
    assert "regenerated_candidates" in exps

    # Verify Holm-Bonferroni adjusted p-values are present in paired comparisons
    bm25_paired = exps["regenerated_candidates"]["BM25_ONLY"]["paired_vs_hybrid_full"]
    assert "p_value_holm_bonferroni" in bm25_paired
    assert 0.0 <= bm25_paired["p_value_holm_bonferroni"] <= 1.0

    # Check HYBRID_FULL and all ablations are present
    for mode in ("fixed_pool_rerank", "regenerated_candidates"):
        mode_data = exps[mode]
        for cfg in (
            "HYBRID_FULL",
            "BM25_ONLY",
            "DENSE_ONLY",
            "SPARSE_ONLY",
            "COLBERT_ONLY",
            "NO_BM25",
            "NO_DENSE",
            "NO_SPARSE",
            "NO_COLBERT",
        ):
            assert cfg in mode_data
            cfg_res = mode_data[cfg]
            assert "candidate_recall" in cfg_res
            assert "metrics_at_k" in cfg_res
            assert 5 in cfg_res["metrics_at_k"] or "5" in cfg_res["metrics_at_k"]
            assert 10 in cfg_res["metrics_at_k"] or "10" in cfg_res["metrics_at_k"]
            assert 20 in cfg_res["metrics_at_k"] or "20" in cfg_res["metrics_at_k"]


def test_e2e_real_bge_m3_profiling_bounds():
    # Verify profiling numbers recorded in report are within realistic physical bounds
    report_path = REPO_ROOT / "reports" / "retrieval_calibration_wave3_experiments_report.json"
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    prof = report.get("profiling", {})
    assert prof, "Profiling section is missing from report"
    assert prof["model_load_latency_seconds"] > 0.0
    assert prof["uncached_encoding_latency_ms"] > 0.0
    assert prof["avg_warm_encoding_latency_ms"] > 0.0
    assert prof["memory_rss_delta_mb"] > 500.0  # Real multi-GB BGE-M3 model footprint
    assert prof["throughput_queries_per_second"] > 0.0


def test_e2e_authentic_benchmark_paired_experiments_lifecycle():
    bench_path = REPO_ROOT / "evals" / "qrels_authentic_benchmark.json"
    pool_path = REPO_ROOT / "evals" / "qrels_authentic_candidate_pool.json"
    report_path = REPO_ROOT / "reports" / "retrieval_calibration_wave3_authentic_benchmark_report.json"

    assert bench_path.is_file(), f"Benchmark not found at {bench_path}"
    assert pool_path.is_file(), f"Candidate pool not found at {pool_path}"
    assert report_path.is_file(), f"Report not found at {report_path}"

    queries_data, ground_truth_map, partitions = load_dev_evaluation_data(
        REPO_ROOT, benchmark_path=bench_path, candidate_pool_path=pool_path
    )
    assert len(queries_data) == 12
    assert len(partitions["topic_assignments"]["dev"]) == 11

    hybrid_res, hybrid_per_q = run_paired_experiment(
        RetrievalConfig.HYBRID_FULL,
        EvaluationMode.FIXED_POOL_RERANK,
        queries_data,
        ground_truth_map,
    )
    bm25_res, bm25_per_q = run_paired_experiment(
        RetrievalConfig.BM25_ONLY,
        EvaluationMode.FIXED_POOL_RERANK,
        queries_data,
        ground_truth_map,
    )

    assert hybrid_res.candidate_recall == 1.0
    assert hybrid_res.metrics_at_k[10]["ndcg"] > 0.80
    assert bm25_res.metrics_at_k[10]["ndcg"] > 0.80

    mean_d, se_d, (ci_lo, ci_hi) = compute_family_clustered_differences(
        hybrid_per_q, bm25_per_q, ground_truth_map
    )
    assert se_d > 0.0

    with open(report_path, "r", encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["total_dev_queries"] == 12
    assert rep["total_dev_topics"] == 11
    assert "fixed_pool_rerank" in rep["experiments"]
    assert "regenerated_candidates" in rep["experiments"]
