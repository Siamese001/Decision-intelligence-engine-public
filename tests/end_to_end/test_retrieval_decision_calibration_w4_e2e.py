"""End-to-end lifecycle tests for Wave 4 decision calibration and cache substitution."""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    DecisionAction,
    OperatingThresholds,
    compute_autonomous_tradeoffs,
    compute_calibration_metrics,
    make_calibrated_decision,
    predict_calibrated_confidence,
    verify_safe_cache_substitution,
)
from tools.run_decision_calibration import load_calibration_partition_data

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_e2e_calibration_partition_lifecycle_and_baseline_superiority():
    cases, partitions = load_calibration_partition_data(REPO_ROOT)
    assert len(cases) == 12
    assert len(partitions["topic_assignments"]["calibration"]) >= 8

    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds()

    calibrated_probs = []
    raw_scores = []
    labels = []
    supported = []
    decisions = []

    for c in cases:
        p = predict_calibrated_confidence(c["top_score"], c["second_score"], c["candidate_count"], weights)
        dec = make_calibrated_decision(c["top_score"], c["second_score"], c["candidate_count"], weights, thresholds)
        calibrated_probs.append(p)
        raw_scores.append(c["top_score"])
        labels.append(c["is_correct"])
        supported.append(c["is_supported"])
        decisions.append(dec)

    calib_m = compute_calibration_metrics(calibrated_probs, labels)
    raw_m = compute_calibration_metrics(raw_scores, labels)
    base_rate_m = compute_calibration_metrics([sum(labels) / len(labels)] * len(labels), labels)

    # Calibrated Brier score must be strictly superior (lower) to raw score baseline and base rate
    assert calib_m["brier_score"] < raw_m["brier_score"]
    assert calib_m["brier_score"] < base_rate_m["brier_score"]

    # Calibrated Log loss must be strictly superior (lower) to raw score baseline and base rate
    assert calib_m["log_loss"] < raw_m["log_loss"]
    assert calib_m["log_loss"] < base_rate_m["log_loss"]

    # Autonomous trade-offs
    tradeoffs = compute_autonomous_tradeoffs(decisions, labels, supported)
    assert tradeoffs["decision_error_rate"] <= thresholds.error_ceiling
    assert tradeoffs["unsupported_false_acceptance_rate"] <= thresholds.max_unsupported_acceptance


def test_e2e_calibration_report_artifact_verification():
    report_path = REPO_ROOT / "reports" / "retrieval_calibration_wave4_calibration_report.json"
    assert report_path.is_file(), f"Report file missing: {report_path}"

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    assert report["report_version"] == "1.0.0"
    assert report["wave"] == "Wave 4: Calibrate decisions and abstention"
    assert report["partition_evaluated"] == "calibration"
    assert report["total_calibration_queries"] == 12
    assert report["total_calibration_topics"] >= 8
    assert report["holdout_protection_verified"] is True

    # Baselines present and verified
    metrics_comp = report["metrics_comparison"]
    assert "calibrated" in metrics_comp
    assert "raw_score_baseline" in metrics_comp
    assert "base_rate_baseline" in metrics_comp

    # Safe cache verifications present
    cache_verifs = report["safe_cache_substitution_verifications"]
    assert len(cache_verifs) >= 5
    assert all(c["contract_satisfied"] for c in cache_verifs)


def test_e2e_safe_cache_substitution_contract_exhaustion():
    # Verify all 4 required contract invariants fail closed
    # 1. Entity mismatch
    s1, r1 = verify_safe_cache_substitution(
        source_query="A", target_query="A", similarity_score=0.99,
        source_entity="Alpha", target_entity="Beta",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-01-01", target_timestamp="2026-01-01",
        source_clearance="c", target_clearance="c",
    )
    assert s1 is False
    assert "ENTITY_MISMATCH" in r1

    # 2. Polarity mismatch
    s2, r2 = verify_safe_cache_substitution(
        source_query="A", target_query="A", similarity_score=0.99,
        source_entity="Alpha", target_entity="Alpha",
        source_is_negated=False, target_is_negated=True,
        source_timestamp="2026-01-01", target_timestamp="2026-01-01",
        source_clearance="c", target_clearance="c",
    )
    assert s2 is False
    assert "POLARITY_NEGATION_CONFLICT" in r2

    # 3. Temporal staleness
    s3, r3 = verify_safe_cache_substitution(
        source_query="A", target_query="A", similarity_score=0.99,
        source_entity="Alpha", target_entity="Alpha",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2021-01-01", target_timestamp="2026-01-01",
        source_clearance="c", target_clearance="c",
    )
    assert s3 is False
    assert "TEMPORAL_STALE_RECORD" in r3

    # 4. Clearance boundary mismatch
    s4, r4 = verify_safe_cache_substitution(
        source_query="A", target_query="A", similarity_score=0.99,
        source_entity="Alpha", target_entity="Alpha",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-01-01", target_timestamp="2026-01-01",
        source_clearance="executive_advisory", target_clearance="public",
    )
    assert s4 is False
    assert "AUTHORIZATION_CLEARANCE_MISMATCH" in r4


def test_e2e_authentic_benchmark_calibration_and_baseline_superiority():
    benchmark_path = REPO_ROOT / "evals" / "qrels_authentic_benchmark.json"
    pool_path = REPO_ROOT / "evals" / "qrels_authentic_candidate_pool.json"
    assert benchmark_path.is_file()
    assert pool_path.is_file()

    cases, partitions = load_calibration_partition_data(
        REPO_ROOT,
        benchmark_path=benchmark_path,
        candidate_pool_path=pool_path,
    )
    assert len(cases) == 12
    assert len(partitions["topic_assignments"]["calibration"]) == 11

    weights = CalibrationModelWeights.for_authentic_benchmark()
    thresholds = OperatingThresholds.for_authentic_benchmark()

    calibrated_probs = []
    raw_scores = []
    labels = []
    supported = []
    decisions = []

    for c in cases:
        p = predict_calibrated_confidence(c["top_score"], c["second_score"], c["candidate_count"], weights)
        dec = make_calibrated_decision(c["top_score"], c["second_score"], c["candidate_count"], weights, thresholds)
        calibrated_probs.append(p)
        raw_scores.append(c["top_score"])
        labels.append(c["is_correct"])
        supported.append(c["is_supported"])
        decisions.append(dec)

    calib_m = compute_calibration_metrics(calibrated_probs, labels)
    raw_m = compute_calibration_metrics(raw_scores, labels)
    base_rate_m = compute_calibration_metrics([sum(labels) / len(labels)] * len(labels), labels)

    # Calibrated Brier score must be strictly superior to raw score baseline and base rate
    assert calib_m["brier_score"] < raw_m["brier_score"]
    assert calib_m["brier_score"] < base_rate_m["brier_score"]

    # Calibrated Log loss must be strictly superior to raw score baseline and base rate
    assert calib_m["log_loss"] < raw_m["log_loss"]
    assert calib_m["log_loss"] < base_rate_m["log_loss"]

    # Autonomous trade-offs: coverage floor >= 60%, error ceiling <= 5%
    tradeoffs = compute_autonomous_tradeoffs(decisions, labels, supported)
    assert tradeoffs["coverage"] >= thresholds.coverage_floor
    assert tradeoffs["decision_error_rate"] <= thresholds.error_ceiling
    assert tradeoffs["wrong_autonomous_count"] == 0
    assert tradeoffs["unsupported_false_acceptance_rate"] <= thresholds.max_unsupported_acceptance


def test_e2e_authentic_calibration_report_artifact_verification():
    report_path = REPO_ROOT / "reports" / "retrieval_calibration_wave4_authentic_report.json"
    assert report_path.is_file(), f"Authentic report missing: {report_path}"

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    assert report["report_version"] == "1.0.0"
    assert report["wave"] == "Wave 4: Calibrate decisions and abstention"
    assert report["partition_evaluated"] == "calibration"
    assert report["total_calibration_queries"] == 12
    assert report["total_calibration_topics"] == 11
    assert report["holdout_protection_verified"] is True

    # Performance superiority
    comp = report["metrics_comparison"]
    assert comp["calibrated"]["brier_score"] < comp["raw_score_baseline"]["brier_score"]
    assert comp["calibrated"]["log_loss"] < comp["raw_score_baseline"]["log_loss"]

    # Trade-offs meet operating criteria
    to = report["autonomous_tradeoffs"]
    assert to["coverage"] >= report["operating_thresholds"]["coverage_floor"]
    assert to["decision_error_rate"] <= report["operating_thresholds"]["error_ceiling"]
    assert to["wrong_autonomous_count"] == 0
