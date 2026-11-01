"""Adversarial tests for Structural Monotonicity and Metric Trend Drift detection."""

from __future__ import annotations

import pytest

from dual_pipeline.graph_delta import (
    MetricTrendAnalyzer,
    StructuralMonotonicityError,
    verify_structural_monotonicity,
)


def test_adversarial_simultaneous_multi_table_collapse():
    """Verify fail-closed behavior when an adversary attempts a broad silent wipe across all tables."""
    before = {
        "source": 100,
        "source_version": 100,
        "evidence": 500,
        "internal_entity": 200,
        "external_concept": 50,
        "canonical_assertion": 1000,
        "candidate_assertion": 100,
        "candidate_search_document": 300,
        "search_document": 400,
        "audit_event": 50,
    }
    # Adversary wipes almost everything to 0
    after = {k: 0 for k in before}

    with pytest.raises(StructuralMonotonicityError) as exc_info:
        verify_structural_monotonicity(before, after)

    err_msg = str(exc_info.value)
    assert "Structural monotonicity violation" in err_msg
    assert "source (100 -> 0" in err_msg
    assert "canonical_assertion (1000 -> 0" in err_msg


def test_adversarial_partial_unauthorized_spoofing():
    """Verify that granting authorization to one component does not permit drops in others."""
    before = {
        "canonical_assertion": 500,
        "internal_entity": 100,
    }
    after = {
        "canonical_assertion": 499,  # 1 dropped assertion
        "internal_entity": 99,  # 1 dropped entity
    }

    # Only authorize internal_entity
    with pytest.raises(StructuralMonotonicityError) as exc_info:
        verify_structural_monotonicity(before, after, allow_deletions={"internal_entity"})

    assert "canonical_assertion" in str(exc_info.value)
    assert "internal_entity" not in str(exc_info.value)


def test_adversarial_zero_variance_flatline():
    """Verify that a completely flat metric series (std=0) does not cause ZeroDivisionError."""
    analyzer = MetricTrendAnalyzer(min_history_for_stats=3)
    for i in range(10):
        analyzer.record_generation(f"gen-{i}", {"constant_metric": 42.0})

    report = analyzer.analyze_metric_drift("constant_metric")
    assert report["status"] == "HEALTHY"
    assert report["rolling_std"] == 0.0
    assert report["sigma_drift_detected"] is False
    assert report["z_score"] == 0.0


def test_adversarial_insufficient_history_handling():
    """Verify graceful handling when fewer data points than min_history exist."""
    analyzer = MetricTrendAnalyzer(min_history_for_stats=5)
    analyzer.record_generation("gen-1", {"val": 10.0})
    analyzer.record_generation("gen-2", {"val": 5.0})

    # Only 2 points, below min_history_for_stats=5
    report = analyzer.analyze_metric_drift("val")
    # Even if dropping, insufficient data for 2-sigma
    assert report["sigma_drift_detected"] is False


def test_adversarial_boundary_recovery_after_decline():
    """Verify consecutive declines reset when an improvement occurs."""
    analyzer = MetricTrendAnalyzer(min_history_for_stats=3)
    # 2 declines, then recovery, then 1 decline -> total consecutive declines should be 1
    values = [10.0, 9.0, 8.0, 8.5, 7.5]
    for i, v in enumerate(values):
        analyzer.record_generation(f"gen-{i}", {"m": v})

    report = analyzer.analyze_metric_drift("m")
    assert report["consecutive_declines"] == 1
    assert report["consecutive_declines_detected"] is False


def test_adversarial_empty_query_metric():
    """Verify non-existent metric returns INSUFFICIENT_DATA status."""
    analyzer = MetricTrendAnalyzer()
    analyzer.record_generation("gen-1", {"existing": 1.0})
    report = analyzer.analyze_metric_drift("non_existent")
    assert report["status"] == "INSUFFICIENT_DATA"
