"""End-to-end tests for Wave 6: Prospective shadow validation and rollback."""

from __future__ import annotations

import json
from pathlib import Path

from tools.run_prospective_shadow_validation import run_prospective_shadow_run


def test_prospective_shadow_validation_e2e_lifecycle():
    repo_root = Path(__file__).resolve().parents[2]
    report = run_prospective_shadow_run(repo_root)

    # 1. Observed sample size
    assert report["total_queries_observed"] >= 30

    # 2. Anti-selective label bias: verified sampling across both autonomous and escalated
    sampled = report["sampled_adjudication"]
    assert sampled["total_sampled"] > 0
    assert sampled["selective_label_bias_prevented"] is True
    assert "ipw_error_rate" in sampled
    assert "effective_sample_size" in sampled

    # 3. Drift metrics and fallback accounting
    drift = report["drift_metrics"]
    assert "score_drift" in drift
    assert "fallback_rate" in drift
    assert "route_shift_rate" in drift

    # 4. Stopping rules
    stopping = report["stopping_rule"]
    assert stopping["is_stopped"] is True

    # 5. Rollback verification
    rollback = report["rollback_verification"]
    assert rollback["clean_environment_status"] is True
    assert rollback["rollback_trigger_detected"] is True
    assert rollback["post_rollback_routing_enforced"] is True
    assert rollback["physical_rollback_verified"] is True
    assert len(rollback["restored_baseline_sha256"]) == 64

    # 6. Consumer metrics
    assert "consumer_metrics" in report
    consumer = report["consumer_metrics"]
    assert consumer["query_engine_durability"] >= 0.80
    assert consumer["query_engine_staleness"] <= 0.15

    # 7. Promotion eligibility enforcement
    promo = report["promotion_eligibility"]
    assert promo["terminal_outcome"] in ("PROCEED_TO_PROMOTION", "RETAIN_BASELINE")
    for s in ("executive", "engineering", "shorthand", "direct_probe"):
        assert promo["slice_coverage"].get(s, 0) > 0


def test_prospective_shadow_report_artifact_parity():
    repo_root = Path(__file__).resolve().parents[2]
    report_file = repo_root / "reports" / "retrieval_calibration_wave6_shadow_report.json"
    assert report_file.exists()

    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["wave"] == "Wave 6: Validate prospective behavior and rollback"
    assert data["report_version"] == "1.0.0"
    assert "drift_metrics" in data
    assert "rollback_verification" in data
    assert "promotion_eligibility" in data
    assert "consumer_metrics" in data
    assert data["rollback_verification"]["physical_rollback_verified"] is True


def test_authentic_prospective_shadow_validation_e2e_lifecycle():
    repo_root = Path(__file__).resolve().parents[2]
    benchmark_path = repo_root / "evals" / "qrels_authentic_benchmark.json"
    pool_path = repo_root / "evals" / "qrels_authentic_candidate_pool.json"
    output_path = repo_root / "reports" / "retrieval_calibration_wave6_authentic_shadow_report.json"

    report = run_prospective_shadow_run(
        repo_root,
        benchmark_path=benchmark_path,
        candidate_pool_path=pool_path,
        output_path=output_path,
    )

    # 1. Observed sample size
    assert report["total_queries_observed"] == 12

    # 2. Anti-selective label bias: sampling across both autonomous and escalated
    sampled = report["sampled_adjudication"]
    assert sampled["total_sampled"] > 0
    assert sampled["sampled_autonomous"] > 0
    assert sampled["sampled_escalated"] > 0
    assert sampled["selective_label_bias_prevented"] is True
    assert "ipw_error_rate" in sampled

    # 3. Drift metrics
    drift = report["drift_metrics"]
    assert "score_drift" in drift
    assert "fallback_rate" in drift
    assert "route_shift_rate" in drift

    # 4. Stopping rules
    stopping = report["stopping_rule"]
    assert stopping["is_stopped"] is True

    # 5. Rollback verification
    rollback = report["rollback_verification"]
    assert rollback["clean_environment_status"] is True
    assert rollback["rollback_trigger_detected"] is True
    assert rollback["post_rollback_routing_enforced"] is True
    assert rollback["physical_rollback_verified"] is True

    # 6. Consumer metrics
    consumer = report["consumer_metrics"]
    assert consumer["query_engine_durability"] >= 0.80

    # 7. Promotion eligibility enforcement
    promo = report["promotion_eligibility"]
    assert promo["terminal_outcome"] in ("PROCEED_TO_PROMOTION", "RETAIN_BASELINE")
    assert promo["slice_coverage"].get("executive", 0) > 0


def test_authentic_prospective_shadow_report_artifact_parity():
    repo_root = Path(__file__).resolve().parents[2]
    report_file = repo_root / "reports" / "retrieval_calibration_wave6_authentic_shadow_report.json"
    assert report_file.exists()

    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["wave"] == "Wave 6: Validate prospective behavior and rollback"
    assert data["report_version"] == "1.0.0"
    assert data["total_queries_observed"] == 12
    assert "drift_metrics" in data
    assert "rollback_verification" in data
    assert "promotion_eligibility" in data
    assert data["rollback_verification"]["physical_rollback_verified"] is True
