"""Adversarial and edge-case tests for Wave 6 prospective validation and rollback.

Covers mandatory categories per e2e-and-edge-case-coverage governance:
1. Boundary payload: empty session, single query, zero-traffic window.
2. Malformed input: invalid sampling probabilities, missing required attributes.
3. Invariant bypass attempt: promoting while rolled back, exceeding error ceiling, bypassing critical slices, data leakage bypass.
4. Rollback & recovery: mid-flight mismatch injection, fail-closed routing integrity, physical rollback verification.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger_verifier import verify_active_pair_rollback
from dual_pipeline.retrieval_release_core.decision_calibration import DecisionAction
from dual_pipeline.retrieval_release_core.prospective_validation import (
    ObservationConfig,
    RollbackEngine,
    RollbackTrigger,
    SamplingPolicy,
    ShadowEvaluationSession,
    ShadowState,
)


# --- Category 1: Boundary Payload ---


def test_adversarial_boundary_empty_session_handles_gracefully():
    session = ShadowEvaluationSession()
    drift = session.compute_drift()
    assert drift.score_drift == 0.0
    assert drift.fallback_rate == 0.0
    assert not drift.drift_breached

    is_stopped, reason = session.check_stopping_rule()
    assert not is_stopped

    eligibility = session.evaluate_promotion_eligibility(adjudicated_errors=0, adjudicated_total=0)
    assert not eligibility.is_eligible
    assert eligibility.terminal_outcome == "RETAIN_BASELINE"
    assert any("SAMPLE_SIZE_DEFICIT" in r for r in eligibility.reasons)


def test_adversarial_boundary_single_query_fails_closed():
    config = ObservationConfig(min_sample_size=50)
    session = ShadowEvaluationSession(config=config)

    session.record_query(
        query_id="single_q",
        query_text="boundary query",
        baseline_decision=DecisionAction.AUTONOMOUS,
        candidate_decision=DecisionAction.AUTONOMOUS,
        baseline_score=0.9,
        candidate_score=0.9,
        baseline_top_id="d1",
        candidate_top_id="d1",
    )

    eligibility = session.evaluate_promotion_eligibility(adjudicated_errors=0, adjudicated_total=1)
    assert not eligibility.is_eligible
    assert any("SAMPLE_SIZE_DEFICIT" in r for r in eligibility.reasons)


# --- Category 2: Malformed Input ---


def test_adversarial_malformed_sampling_probabilities_rejected():
    with pytest.raises(ValueError, match="Sampling probabilities must be in"):
        SamplingPolicy(p_autonomous=0.0)

    with pytest.raises(ValueError, match="Sampling probabilities must be in"):
        SamplingPolicy(p_autonomous=1.5)

    with pytest.raises(ValueError, match="Sampling probabilities must be in"):
        SamplingPolicy(p_escalated=-0.1)


# --- Category 3: Invariant Bypass Attempts ---


def test_adversarial_bypass_attempt_while_rolled_back_fails_closed():
    session = ShadowEvaluationSession()
    for i in range(50):
        session.record_query(
            query_id=f"q_{i}",
            query_text=f"query {i}",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS,
            baseline_score=0.8,
            candidate_score=0.8,
            baseline_top_id="d1",
            candidate_top_id="d1",
            critical_slice="executive" if i % 2 == 0 else "engineering",
        )

    # Force rollback state
    session.rollback_engine.trigger_rollback(
        RollbackTrigger.MODEL_MISMATCH, "Tampered model weights detected"
    )

    eligibility = session.evaluate_promotion_eligibility(adjudicated_errors=0, adjudicated_total=50)
    assert not eligibility.is_eligible
    assert eligibility.terminal_outcome == "RETAIN_BASELINE"
    assert any("SYSTEM_ROLLED_BACK" in r for r in eligibility.reasons)


def test_adversarial_bypass_attempt_exceeding_error_ceiling_fails_closed():
    config = ObservationConfig(min_sample_size=30, max_allowed_error_rate=0.05)
    session = ShadowEvaluationSession(config=config)

    for i in range(35):
        session.record_query(
            query_id=f"q_{i}",
            query_text=f"query {i}",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS,
            baseline_score=0.8,
            candidate_score=0.8,
            baseline_top_id="d1",
            candidate_top_id="d1",
            critical_slice="executive" if i % 2 == 0 else "engineering",
        )

    # 3 errors in 30 = 10% error rate > 5% ceiling
    eligibility = session.evaluate_promotion_eligibility(adjudicated_errors=3, adjudicated_total=30)
    assert not eligibility.is_eligible
    assert eligibility.terminal_outcome == "RETAIN_BASELINE"
    assert any("ERROR_CEILING_BREACH" in r for r in eligibility.reasons)


def test_adversarial_bypass_missing_critical_slice_fails_closed():
    config = ObservationConfig(
        min_sample_size=20,
        critical_slices=("executive", "engineering", "shorthand"),
    )
    session = ShadowEvaluationSession(config=config)

    # Only provide executive slice, missing engineering and shorthand
    for i in range(25):
        session.record_query(
            query_id=f"q_{i}",
            query_text=f"query {i}",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS,
            baseline_score=0.8,
            candidate_score=0.8,
            baseline_top_id="d1",
            candidate_top_id="d1",
            critical_slice="executive",
        )

    eligibility = session.evaluate_promotion_eligibility(adjudicated_errors=0, adjudicated_total=25)
    assert not eligibility.is_eligible
    assert any("Slice 'engineering' has 0 observations" in r for r in eligibility.reasons)
    assert any("Slice 'shorthand' has 0 observations" in r for r in eligibility.reasons)


def test_adversarial_partition_leakage_bypass_attempt_fails_closed():
    session = ShadowEvaluationSession(disallowed_query_ids={"t1_01_exec", "t1_02_exec"})
    with pytest.raises(ValueError, match="rejected to prevent leakage"):
        session.record_query(
            query_id="t1_01_exec",
            query_text="Attacking prospective with dev query",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS,
            baseline_score=0.8,
            candidate_score=0.8,
            baseline_top_id="d1",
            candidate_top_id="d1",
        )


def test_adversarial_unverified_adjudication_ledger_bypass_fails_closed():
    session = ShadowEvaluationSession()
    session.record_query(
        query_id="q_leak",
        query_text="Query with fake adjudication",
        baseline_decision=DecisionAction.AUTONOMOUS,
        candidate_decision=DecisionAction.AUTONOMOUS,
        baseline_score=0.8,
        candidate_score=0.8,
        baseline_top_id="d1",
        candidate_top_id="d1",
    )
    with pytest.raises(ValueError, match="lacks verified receipt in ledger"):
        session.record_adjudication(
            query_id="q_leak",
            is_error=False,
            decision_id="fake_unratified_decision_12345",
            require_cleared=True,
        )


# --- Category 4: Rollback & Recovery ---


def test_adversarial_rollback_and_recovery_during_active_traffic():
    engine = RollbackEngine()

    # Initial routing goes to candidate
    res1, tag1 = engine.route_query(
        candidate_fn=lambda: "CANDIDATE_V2",
        baseline_fn=lambda: "BASELINE_V1",
    )
    assert res1 == "CANDIDATE_V2"
    assert tag1 == "CANDIDATE_SHADOW_ACTIVE"

    # Sudden mid-flight index mismatch detected
    event = engine.verify_environment(
        expected_model_hash="sha_v1",
        current_model_hash="sha_v1",
        expected_index_gen="gen_20261006_v1",
        current_index_gen="gen_20261007_corrupted",
        calibration_verified=True,
    )
    assert event is not None
    assert event.trigger == RollbackTrigger.INDEX_MISMATCH
    assert engine.is_rolled_back is True

    # Immediate subsequent routing routes cleanly to baseline
    res2, tag2 = engine.route_query(
        candidate_fn=lambda: "CANDIDATE_V2",
        baseline_fn=lambda: "BASELINE_V1",
    )
    assert res2 == "BASELINE_V1"
    assert tag2 == "BASELINE_ENFORCED_POST_ROLLBACK"


def test_adversarial_physical_rollback_digest_tampering_fails():
    with tempfile.TemporaryDirectory(prefix="rt_adv_rollback_") as tmp_dir:
        tmp_p = Path(tmp_dir)
        base_pair = {"generation_id": "gen_base_valid"}
        cand_pair = {"generation_id": "gen_cand_unauthorized"}
        (tmp_p / "active_pair.json.bak").write_text(json.dumps(base_pair) + "\n", encoding="utf-8")
        (tmp_p / "active_pair.json").write_text(json.dumps(cand_pair) + "\n", encoding="utf-8")

        engine = RollbackEngine()
        ev = engine.execute_physical_rollback(tmp_p, RollbackTrigger.MODEL_MISMATCH, "Weights drifted")

        # Fake expected hash
        ok, msg = verify_active_pair_rollback(tmp_p / "active_pair.json", "fake_corrupted_sha256")
        assert ok is False
        assert "Rollback mismatch" in msg
