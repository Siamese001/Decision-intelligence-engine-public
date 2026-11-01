"""Unit tests for Structural Monotonicity Guard and MetricTrendAnalyzer (Wave 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.graph_delta import (
    MetricTrendAnalyzer,
    StructuralMonotonicityError,
    verify_structural_monotonicity,
)


def test_monotonicity_clean_pass():
    before = {
        "source": 10,
        "source_version": 10,
        "evidence": 50,
        "internal_entity": 20,
        "external_concept": 5,
        "canonical_assertion": 100,
        "candidate_assertion": 15,
        "candidate_search_document": 30,
    }
    after = {
        "source": 11,
        "source_version": 11,
        "evidence": 55,
        "internal_entity": 22,
        "external_concept": 5,
        "canonical_assertion": 105,
        "candidate_assertion": 15,
        "candidate_search_document": 32,
    }
    res = verify_structural_monotonicity(before, after)
    assert res["status"] == "PASS"
    assert res["is_monotonic"] is True
    assert res["deltas"]["source"] == 1
    assert res["deltas"]["canonical_assertion"] == 5


def test_monotonicity_unauthorized_deletion_raises():
    before = {"canonical_assertion": 100, "internal_entity": 20}
    after = {"canonical_assertion": 95, "internal_entity": 20}

    with pytest.raises(StructuralMonotonicityError) as exc_info:
        verify_structural_monotonicity(before, after)
    assert "canonical_assertion (100 -> 95, delta=-5)" in str(exc_info.value)


def test_monotonicity_explicit_allow_deletions_bool():
    before = {"canonical_assertion": 100, "node": 50}
    after = {"canonical_assertion": 80, "node": 45}

    res = verify_structural_monotonicity(before, after, allow_deletions=True)
    assert res["status"] == "PASS"
    assert res["is_monotonic"] is True
    assert res["deltas"]["canonical_assertion"] == -20


def test_monotonicity_explicit_allow_deletions_component_set():
    before = {
        "candidate_assertion": 20,
        "canonical_assertion": 100,
    }
    after = {
        "candidate_assertion": 10,  # drop authorized
        "canonical_assertion": 90,  # drop UNAUTHORIZED
    }

    # Authorizing candidate_assertion should still fail on canonical_assertion
    with pytest.raises(StructuralMonotonicityError) as exc_info:
        verify_structural_monotonicity(before, after, allow_deletions={"candidate_assertion"})
    assert "canonical_assertion" in str(exc_info.value)
    assert "candidate_assertion" not in str(exc_info.value)

    # Authorizing both should pass
    res = verify_structural_monotonicity(
        before, after, allow_deletions={"candidate_assertion", "canonical_assertion"}
    )
    assert res["status"] == "PASS"


def test_trend_analyzer_recording_and_series():
    analyzer = MetricTrendAnalyzer(min_history_for_stats=3)
    analyzer.record_generation("gen-001", {"entity_count": 100, "density": 0.85})
    analyzer.record_generation("gen-002", {"entity_count": 105, "density": 0.86})

    series = analyzer.get_metric_series("entity_count")
    assert series == [100.0, 105.0]


def test_trend_analyzer_stable_metrics():
    analyzer = MetricTrendAnalyzer(min_history_for_stats=3)
    for i in range(5):
        analyzer.record_generation(f"gen-{i}", {"score": 0.90 + (i * 0.005)})

    report = analyzer.analyze_metric_drift("score")
    assert report["status"] == "HEALTHY"
    assert report["consecutive_declines_detected"] is False
    assert report["sigma_drift_detected"] is False


def test_trend_analyzer_consecutive_declines():
    analyzer = MetricTrendAnalyzer(min_history_for_stats=3)
    # 4 consecutive decreases
    scores = [1.0, 0.95, 0.90, 0.85, 0.80]
    for i, s in enumerate(scores):
        analyzer.record_generation(f"gen-{i}", {"score": s})

    report = analyzer.analyze_metric_drift("score")
    assert report["status"] == "ALERT"
    assert report["consecutive_declines_detected"] is True
    assert report["consecutive_declines"] >= 3
    assert "consecutive declines observed" in report["details"]


def test_trend_analyzer_sigma_drift():
    analyzer = MetricTrendAnalyzer(min_history_for_stats=3)
    # Stable history around 100
    for i in range(10):
        analyzer.record_generation(f"gen-{i}", {"metric": 100.0 + (i % 2)})
    # Sudden plunge to 50
    analyzer.record_generation("gen-10", {"metric": 50.0})

    report = analyzer.analyze_metric_drift("metric")
    assert report["status"] == "ALERT"
    assert report["sigma_drift_detected"] is True
    assert report["z_score"] < -2.0


def test_trend_analyzer_save_load(tmp_path: Path):
    path = tmp_path / "trend_history.json"
    analyzer = MetricTrendAnalyzer(min_history_for_stats=3)
    analyzer.record_generation("gen-1", {"m1": 10.0})
    analyzer.save(path)

    loaded = MetricTrendAnalyzer.load(path)
    assert len(loaded.history) == 1
    assert loaded.get_metric_series("m1") == [10.0]
