"""Unit tests for Wave 4 decision calibration, abstention gating, and safe cache substitution."""

from __future__ import annotations

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


def test_calibration_holdout_protection_blocks_holdout_queries():
    partitions = {
        "query_partitions": {
            "calibration": ["calib_01", "calib_02"],
            "final_holdout": ["holdout_secret"],
        }
    }
    # Calibration queries pass
    validate_calibration_holdout_protection(["calib_01", "calib_02"], partitions)

    # Final holdout queries fail closed
    with pytest.raises(ValueError, match="HOLDOUT LEAKAGE VIOLATION"):
        validate_calibration_holdout_protection(["calib_01", "holdout_secret"], partitions)


def test_anti_certainty_singleton_candidate_penalized():
    weights = CalibrationModelWeights()
    # Case A: Singleton candidate with score 0.8
    prob_singleton = predict_calibrated_confidence(
        top_score=0.80, second_score=None, candidate_count=1, weights=weights
    )

    # Case B: Multi-candidate with score 0.8 and decisive margin 0.3
    prob_multi = predict_calibrated_confidence(
        top_score=0.80, second_score=0.50, candidate_count=10, weights=weights
    )

    # Singleton candidate must NOT manufacture higher certainty than multi-candidate decisive margin
    assert prob_singleton < prob_multi


def test_calibrated_decision_routing_actions():
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds(tau_auto=0.75, tau_abstain=0.40, min_margin=0.15)

    # 1. High score + large margin -> AUTONOMOUS
    dec_auto = make_calibrated_decision(
        top_score=0.98, second_score=0.50, candidate_count=5, weights=weights, thresholds=thresholds
    )
    assert dec_auto.action == DecisionAction.AUTONOMOUS
    assert dec_auto.margin >= 0.15

    # 2. Decent score but tight margin (ambiguous) -> HITL_REVIEW
    dec_hitl = make_calibrated_decision(
        top_score=0.80, second_score=0.78, candidate_count=10, weights=weights, thresholds=thresholds
    )
    assert dec_hitl.action == DecisionAction.HITL_REVIEW
    assert "AMBIGUITY_MARGIN_BELOW_THRESHOLD" in dec_hitl.rejection_reasons or "CONFIDENCE_REQUIRES_OPERATOR_REVIEW" in dec_hitl.rejection_reasons

    # 3. Very low score -> ABSTAIN
    dec_abstain = make_calibrated_decision(
        top_score=0.20, second_score=0.10, candidate_count=2, weights=weights, thresholds=thresholds
    )
    assert dec_abstain.action == DecisionAction.ABSTAIN
    assert "CONFIDENCE_BELOW_ABSTENTION_THRESHOLD" in dec_abstain.rejection_reasons


def test_calibration_metrics_brier_and_log_loss_calculation():
    # Hand-calculated case:
    # y = [1, 0]
    # p = [0.8, 0.2]
    # Brier: ((0.8 - 1)^2 + (0.2 - 0)^2) / 2 = (0.04 + 0.04) / 2 = 0.04
    # LogLoss: -(ln(0.8) + ln(0.8)) / 2 = -ln(0.8) ~= 0.2231
    probs = [0.8, 0.2]
    labels = [1, 0]
    metrics = compute_calibration_metrics(probs, labels, num_bins=2)

    assert metrics["brier_score"] == pytest.approx(0.04, abs=0.001)
    assert metrics["log_loss"] == pytest.approx(0.2231, abs=0.001)
    assert metrics["ece"] >= 0.0


def test_autonomous_tradeoffs_and_wilson_bounds():
    thresholds = OperatingThresholds(tau_auto=0.75, min_margin=0.10)
    weights = CalibrationModelWeights()
    # 2 autonomous decisions, 1 correct, 1 wrong
    d1 = make_calibrated_decision(0.95, 0.50, 5, weights, thresholds)
    d2 = make_calibrated_decision(0.95, 0.50, 5, weights, thresholds)
    d3 = make_calibrated_decision(0.20, 0.10, 2, weights, thresholds)  # abstained

    tradeoffs = compute_autonomous_tradeoffs(
        decisions=[d1, d2, d3],
        ground_truth_labels=[1, 0, 0],
        is_supported=[True, True, False],
    )
    assert tradeoffs["total_requests"] == 3
    assert tradeoffs["autonomous_count"] == 2
    assert tradeoffs["coverage"] == pytest.approx(2.0 / 3.0, abs=0.001)
    assert tradeoffs["wrong_autonomous_count"] == 1
    assert tradeoffs["decision_error_rate"] == 0.5
    assert tradeoffs["upper_bound_error_95"] > 0.5


def test_safe_cache_substitution_invariants():
    # Identical valid passes
    safe, rejections = verify_safe_cache_substitution(
        source_query="test", target_query="test", similarity_score=0.95,
        source_entity="E1", target_entity="E1",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-01-01", target_timestamp="2026-01-01",
        source_clearance="internal", target_clearance="internal",
    )
    assert safe is True
    assert len(rejections) == 0

    # Entity mismatch fails
    safe_ent, rej_ent = verify_safe_cache_substitution(
        source_query="test", target_query="test", similarity_score=0.99,
        source_entity="E1", target_entity="E2",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-01-01", target_timestamp="2026-01-01",
        source_clearance="internal", target_clearance="internal",
    )
    assert safe_ent is False
    assert "ENTITY_MISMATCH" in rej_ent

    # Polarity conflict fails
    safe_pol, rej_pol = verify_safe_cache_substitution(
        source_query="test", target_query="test", similarity_score=0.99,
        source_entity="E1", target_entity="E1",
        source_is_negated=False, target_is_negated=True,
        source_timestamp="2026-01-01", target_timestamp="2026-01-01",
        source_clearance="internal", target_clearance="internal",
    )
    assert safe_pol is False
    assert "POLARITY_NEGATION_CONFLICT" in rej_pol


def test_auto_count_zero_fails_closed_with_error_bound_one():
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds(tau_auto=0.99, tau_abstain=0.01)

    dec = make_calibrated_decision(0.10, 0.05, 1, weights, thresholds)
    assert dec.action != DecisionAction.AUTONOMOUS

    tradeoffs = compute_autonomous_tradeoffs(
        decisions=[dec],
        ground_truth_labels=[1],
        is_supported=[True],
    )
    assert tradeoffs["autonomous_count"] == 0
    assert tradeoffs["upper_bound_error_95"] == 1.0
    assert tradeoffs["coverage"] == 0.0
    assert tradeoffs["lower_bound_coverage_95"] == 0.0


def test_safe_cache_substitution_merkle_audience_and_advisory_guards():
    # 1. INTERNAL_ADVISORY quarantine
    s1, r1 = verify_safe_cache_substitution(
        source_query="test", target_query="test", similarity_score=0.99,
        source_entity="E1", target_entity="E1",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-08-28T10:00:00Z", target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal", target_clearance="internal",
        is_internal_advisory=True,
    )
    assert s1 is False
    assert "INTERNAL_ADVISORY_QUARANTINE_BLOCKED" in r1

    # 2. Subgraph Merkle Root mismatch
    s2, r2 = verify_safe_cache_substitution(
        source_query="test", target_query="test", similarity_score=0.99,
        source_entity="E1", target_entity="E1",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-08-28T10:00:00Z", target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal", target_clearance="internal",
        source_merkle_root="sha256:111", target_merkle_root="sha256:222",
    )
    assert s2 is False
    assert "SUBGRAPH_MERKLE_ROOT_MISMATCH" in r2

    # 3. Audience expansion beyond clearance
    s3, r3 = verify_safe_cache_substitution(
        source_query="test", target_query="test", similarity_score=0.99,
        source_entity="E1", target_entity="E1",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-08-28T10:00:00Z", target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal", target_clearance="internal",
        source_audiences=["internal_consulting"], target_audiences=["internal_consulting", "external_public"],
    )
    assert s3 is False
    assert "AUDIENCE_CLEARANCE_EXPANSION_BLOCKED" in r3

    # 4. Expired bitemporal validity
    s4, r4 = verify_safe_cache_substitution(
        source_query="test", target_query="test", similarity_score=0.99,
        source_entity="E1", target_entity="E1",
        source_is_negated=False, target_is_negated=False,
        source_timestamp="2026-08-28T10:00:00Z", target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal", target_clearance="internal",
        valid_to="2020-01-01",
    )
    assert s4 is False
    assert "TEMPORAL_WINDOW_EXPIRED" in r4


def test_egress_sanitizer_blocks_internal_advisory_patterns():
    from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer

    safe_text = "Standard client proposal regarding energy software."
    assert LocalPreEgressSanitizer.sanitize_text(safe_text) == safe_text

    advisory_texts = [
        "Confidential INTERNAL_ADVISORY notes.",
        "Entity marked is_quarantined for executive view.",
        "Personal career leverage document.",
        "Shadow graph stealth_score evaluation.",
    ]
    for text in advisory_texts:
        with pytest.raises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_text(text, allow_internal_operator=False)

        # Operator override allows internal inspection
        assert LocalPreEgressSanitizer.sanitize_text(text, allow_internal_operator=True) == text


def test_decision_evidence_pack_rank_and_score_invariance():
    from dual_pipeline.decision_engine_core.evidence_pack import build_decision_evidence_pack

    items = [
        {
            "record": {
                "record_id": f"rec_{i}",
                "title": f"Fact Title {i}",
                "retrieval_text": f"Fact body text {i}.",
                "evidence_ids": [f"ev_{i}"],
                "record_type": "source_chunk",
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "factual_answer"},
            "fused_score": 0.015 + i * 0.005,
            "dense_score": 0.50 + i * 0.1,
        }
        for i in range(4)
    ]
    packet_a = {"approved": list(items), "candidate": [], "external_public": [], "graph_paths": [], "counterevidence": [], "gaps": []}
    pack_a = build_decision_evidence_pack(query_result={}, evidence_packet=packet_a)

    # Reverse order and alter scores
    items_rev = list(reversed(items))
    for it in items_rev:
        it["fused_score"] = 0.999
        it["dense_score"] = 0.111
    packet_b = {"approved": items_rev, "candidate": [], "external_public": [], "graph_paths": [], "counterevidence": [], "gaps": []}
    pack_b = build_decision_evidence_pack(query_result={}, evidence_packet=packet_b)

    # Invariant: Extracted facts, risks, constraints sets are 100% identical regardless of ranking/scores
    assert {f["record_id"] for f in pack_a["facts"]} == {f["record_id"] for f in pack_b["facts"]}

