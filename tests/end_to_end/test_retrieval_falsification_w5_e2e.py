"""End-to-end lifecycle tests for Wave 5 holdout evaluation and falsification attacks."""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    OperatingThresholds,
)
from dual_pipeline.retrieval_release_core.falsification import (
    evaluate_holdout_partition,
    execute_falsification_attack_citation_only_authority,
    execute_falsification_attack_clearance_breach,
    execute_falsification_attack_corrupt_sidecar,
    execute_falsification_attack_corrupt_vector,
    execute_falsification_attack_disable_bge_m3,
    execute_falsification_attack_disable_bge_m3_with_cleared_caches,
    execute_falsification_attack_inject_wrong_facts,
    execute_falsification_attack_remove_positives,
    execute_falsification_attack_reverse_rankings,
    execute_falsification_attack_superseded_contradicted,
)
from tools.evaluate_final_holdout import load_final_holdout_data

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_e2e_holdout_evaluation_lifecycle():
    holdout_queries, ground_truth_map, partitions = load_final_holdout_data(REPO_ROOT)
    assert len(holdout_queries) in (12, 15)
    assert len(partitions["topic_assignments"]["final_holdout"]) in (9, 11, 64)

    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds()

    holdout_res = evaluate_holdout_partition(
        holdout_queries, ground_truth_map, weights, thresholds, cutoffs=(5, 10, 20)
    )
    assert holdout_res["queries_evaluated"] in (12, 15)
    assert holdout_res["mean_candidate_recall"] > 0.50
    assert holdout_res["metrics_at_k"][10]["ndcg"] > 0.0
    assert holdout_res["metrics_at_k"][10]["mrr"] > 0.0


def test_e2e_all_falsification_attacks_detected_safely():
    holdout_queries, ground_truth_map, _ = load_final_holdout_data(REPO_ROOT)
    sample_query = holdout_queries[0]
    sample_gt = ground_truth_map[sample_query["query_id"]]
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds()

    attacks = [
        execute_falsification_attack_remove_positives(sample_query, sample_gt),
        execute_falsification_attack_inject_wrong_facts(sample_query, sample_gt, weights, thresholds),
        execute_falsification_attack_reverse_rankings(sample_query, sample_gt),
        execute_falsification_attack_corrupt_vector([0.1] * 1024),
        execute_falsification_attack_disable_bge_m3_with_cleared_caches(),
        execute_falsification_attack_clearance_breach(),
        execute_falsification_attack_citation_only_authority(),
        execute_falsification_attack_superseded_contradicted(),
        execute_falsification_attack_corrupt_sidecar(),
    ]
    assert all(a.detected_cleanly for a in attacks)
    assert all(a.forged_pass_prevented for a in attacks)


def test_e2e_holdout_falsification_report_artifact_verification():
    report_path = REPO_ROOT / "reports" / "retrieval_calibration_wave5_holdout_falsification_report.json"
    assert report_path.is_file(), f"Report missing: {report_path}"

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    assert report["report_version"] == "1.0.0"
    assert report["wave"] == "Wave 5: Attempt to falsify the frozen system"
    assert report["partition_evaluated"] == "final_holdout"
    assert report["total_holdout_queries"] in (12, 15)
    assert report["total_holdout_topics"] in (9, 11, 64)
    assert report["all_attacks_safely_handled"] is True

    # Attacks recorded
    assert len(report["falsification_attacks"]) >= 5
    for a in report["falsification_attacks"]:
        assert a["detected_cleanly"] is True
        assert a["forged_pass_prevented"] is True


def test_e2e_authentic_holdout_evaluation_and_attacks():
    benchmark_path = REPO_ROOT / "evals" / "qrels_authentic_benchmark.json"
    pool_path = REPO_ROOT / "evals" / "qrels_authentic_candidate_pool.json"
    assert benchmark_path.is_file()
    assert pool_path.is_file()

    holdout_queries, ground_truth_map, partitions = load_final_holdout_data(
        REPO_ROOT,
        benchmark_path=benchmark_path,
        candidate_pool_path=pool_path,
    )
    assert len(holdout_queries) == 12
    assert len(partitions["topic_assignments"]["final_holdout"]) == 11

    weights = CalibrationModelWeights.for_authentic_benchmark()
    thresholds = OperatingThresholds.for_authentic_benchmark()

    holdout_res = evaluate_holdout_partition(
        holdout_queries, ground_truth_map, weights, thresholds, cutoffs=(5, 10, 20)
    )
    assert holdout_res["queries_evaluated"] == 12
    assert holdout_res["mean_candidate_recall"] == 1.0
    assert holdout_res["metrics_at_k"][10]["ndcg"] > 0.80
    assert holdout_res["metrics_at_k"][10]["mrr"] > 0.80

    # Pick query with top positive candidate for clean degradation verification
    sample_query = next(
        (
            q
            for q in holdout_queries
            if any(
                r in ground_truth_map[q["query_id"]].relevant_ids
                for r in [c["record_id"] for c in q.get("candidates", [])[:10]]
            )
        ),
        holdout_queries[0],
    )
    sample_gt = ground_truth_map[sample_query["query_id"]]

    attacks = [
        execute_falsification_attack_remove_positives(sample_query, sample_gt),
        execute_falsification_attack_inject_wrong_facts(sample_query, sample_gt, weights, thresholds),
        execute_falsification_attack_reverse_rankings(sample_query, sample_gt),
        execute_falsification_attack_corrupt_vector([0.1] * 1024),
        execute_falsification_attack_disable_bge_m3_with_cleared_caches(),
        execute_falsification_attack_clearance_breach(),
        execute_falsification_attack_citation_only_authority(),
        execute_falsification_attack_superseded_contradicted(),
        execute_falsification_attack_corrupt_sidecar(),
    ]
    assert all(a.detected_cleanly for a in attacks)
    assert all(a.forged_pass_prevented for a in attacks)


def test_e2e_authentic_falsification_report_artifact_verification():
    report_path = REPO_ROOT / "reports" / "retrieval_calibration_wave5_authentic_falsification_report.json"
    assert report_path.is_file(), f"Authentic falsification report missing: {report_path}"

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    assert report["report_version"] == "1.0.0"
    assert report["wave"] == "Wave 5: Attempt to falsify the frozen system"
    assert report["partition_evaluated"] == "final_holdout"
    assert report["total_holdout_queries"] == 12
    assert report["total_holdout_topics"] == 11
    assert report["all_attacks_safely_handled"] is True

    # Attacks recorded and passed
    assert len(report["falsification_attacks"]) >= 5
    for a in report["falsification_attacks"]:
        assert a["detected_cleanly"] is True
        assert a["forged_pass_prevented"] is True

