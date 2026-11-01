"""Adversarial and edge-case tests for Wave 4 decision calibration and cache substitution."""

from __future__ import annotations

import math
import pytest

from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    DecisionAction,
    OperatingThresholds,
    compute_autonomous_tradeoffs,
    compute_calibration_metrics,
    make_calibrated_decision,
    predict_calibrated_confidence,
    validate_calibration_holdout_protection,
    verify_safe_cache_substitution,
)


def test_adversarial_boundary_empty_metrics():
    # Category: Boundary payload
    metrics = compute_calibration_metrics([], [])
    assert metrics["brier_score"] == 0.0
    assert metrics["log_loss"] == 0.0
    assert metrics["ece"] == 0.0

    tradeoffs = compute_autonomous_tradeoffs([], [], [])
    assert tradeoffs == {}


def test_adversarial_boundary_extreme_probabilities():
    # Category: Boundary payload
    # Perfect predictions
    perfect_metrics = compute_calibration_metrics([1.0, 0.0], [1, 0])
    assert perfect_metrics["brier_score"] == 0.0
    assert perfect_metrics["log_loss"] == pytest.approx(0.0, abs=1e-6)

    # Worst possible predictions
    worst_metrics = compute_calibration_metrics([0.0, 1.0], [1, 0])
    assert worst_metrics["brier_score"] == 1.0
    assert worst_metrics["log_loss"] > 10.0


def test_adversarial_malformed_input_nan_scores():
    # Category: Malformed input
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds()

    # Pass nan as scores: sigmoid should handle without crashing
    prob = predict_calibrated_confidence(float("nan"), float("nan"), 1, weights)
    assert not math.isnan(prob)
    assert 0.0 <= prob <= 1.0


def test_adversarial_bypass_high_similarity_cannot_bypass_entity_mismatch():
    # Category: Invariant bypass attempt
    # Vector similarity is 0.999, but entity changed from "Sharon Diaram" to "Scott Bores"
    is_safe, rejections = verify_safe_cache_substitution(
        source_query="APEX land management LCDA",
        target_query="APEX land management LCDA",
        similarity_score=0.999,
        source_entity="Sharon Diaram",
        target_entity="Scott Bores",
        source_is_negated=False,
        target_is_negated=False,
        source_timestamp="2026-08-28T10:00:00Z",
        target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal",
        target_clearance="internal",
    )
    assert is_safe is False
    assert "ENTITY_MISMATCH" in rejections


def test_adversarial_bypass_high_similarity_cannot_bypass_polarity_negation():
    # Category: Invariant bypass attempt
    # Vector similarity is 0.995, but query polarity is negated ("is" vs "is NOT")
    is_safe, rejections = verify_safe_cache_substitution(
        source_query="Scott Bores is APEX CIO",
        target_query="Scott Bores is NOT APEX CIO",
        similarity_score=0.995,
        source_entity="Scott Bores",
        target_entity="Scott Bores",
        source_is_negated=False,
        target_is_negated=True,
        source_timestamp="2026-08-28T10:00:00Z",
        target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal",
        target_clearance="internal",
    )
    assert is_safe is False
    assert "POLARITY_NEGATION_CONFLICT" in rejections


def test_adversarial_bypass_high_similarity_cannot_bypass_temporal_staleness():
    # Category: Invariant bypass attempt
    # Vector similarity is 0.990, but record timestamp is 5 years stale
    is_safe, rejections = verify_safe_cache_substitution(
        source_query="APEX land prospecting status",
        target_query="APEX land prospecting status",
        similarity_score=0.990,
        source_entity="APEX",
        target_entity="APEX",
        source_is_negated=False,
        target_is_negated=False,
        source_timestamp="2021-01-01T00:00:00Z",
        target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal",
        target_clearance="internal",
    )
    assert is_safe is False
    assert "TEMPORAL_STALE_RECORD" in rejections


def test_adversarial_bypass_high_similarity_cannot_bypass_clearance_elevation():
    # Category: Invariant bypass attempt
    # User with public clearance attempting to read executive advisory cached entry
    is_safe, rejections = verify_safe_cache_substitution(
        source_query="Executive career thesis",
        target_query="Executive career thesis",
        similarity_score=0.998,
        source_entity="Exec",
        target_entity="Exec",
        source_is_negated=False,
        target_is_negated=False,
        source_timestamp="2026-08-28T10:00:00Z",
        target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal_advisory",
        target_clearance="public",
    )
    assert is_safe is False
    assert "AUTHORIZATION_CLEARANCE_MISMATCH" in rejections


def test_adversarial_bypass_calibration_holdout_leakage_blocked():
    # Category: Invariant bypass attempt
    partitions = {
        "query_partitions": {
            "calibration": ["calib_1"],
            "final_holdout": ["holdout_p0"],
        }
    }
    with pytest.raises(ValueError, match="HOLDOUT LEAKAGE VIOLATION"):
        validate_calibration_holdout_protection(["calib_1", "holdout_p0"], partitions)


def test_adversarial_exact_production_regression_wrong_singleton_refuses_autonomous():
    # Category: Exact production regression
    # Distractor trap query with only 1 candidate retrieved (singleton)
    # The system must NOT declare autonomous certainty on a singleton
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds(tau_auto=0.75, min_margin=0.15)

    dec = make_calibrated_decision(
        top_score=0.75,
        second_score=None,
        candidate_count=1,
        weights=weights,
        thresholds=thresholds,
    )
    # Singleton candidate must NOT be AUTONOMOUS
    assert dec.action != DecisionAction.AUTONOMOUS
    assert dec.is_singleton is True
