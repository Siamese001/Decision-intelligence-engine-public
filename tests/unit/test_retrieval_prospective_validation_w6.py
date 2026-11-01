"""Unit tests for Wave 6: Prospective shadow validation, sampled adjudication, and rollback."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger_verifier import (
    verify_active_pair_rollback,
    verify_ledger_chain,
    verify_ledger_signatures,
)
from dual_pipeline.retrieval_release_core.decision_calibration import DecisionAction
from dual_pipeline.retrieval_release_core.ipw_estimator import (
    IPWAdjudicationSample,
    compute_horvitz_thompson_ipw,
)
from dual_pipeline.retrieval_release_core.prospective_validation import (
    ConsumerMetrics,
    DriftMetrics,
    ObservationConfig,
    PromotionEligibility,
    RollbackEngine,
    RollbackTrigger,
    SamplingPolicy,
    ShadowEvaluationSession,
    ShadowState,
    StoppingRuleType,
)


def test_shadow_evaluation_session_recording():
    policy = SamplingPolicy(p_autonomous=0.50, p_escalated=0.50, seed=123)
    session = ShadowEvaluationSession(sampling_policy=policy)

    rec = session.record_query(
        query_id="q1",
        query_text="operational bottlenecks in land management",
        baseline_decision=DecisionAction.HITL_REVIEW,
        candidate_decision=DecisionAction.AUTONOMOUS,
        baseline_score=0.45,
        candidate_score=0.85,
        baseline_top_id="doc_bm25_1",
        candidate_top_id="doc_hybrid_1",
        fallback_used=False,
        critical_slice="executive",
    )

    assert rec.query_id == "q1"
    assert not rec.is_agreement
    assert rec.critical_slice == "executive"
    assert len(session.records) == 1


def test_disallowed_query_partition_leakage_rejected_fail_closed():
    session = ShadowEvaluationSession(disallowed_query_ids={"dev_01", "cal_01", "holdout_01"})
    with pytest.raises(ValueError, match="rejected to prevent leakage"):
        session.record_query(
            query_id="dev_01",
            query_text="leakage query",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS,
            baseline_score=0.9,
            candidate_score=0.9,
            baseline_top_id="d1",
            candidate_top_id="d1",
        )


def test_stratified_sampling_prevents_selective_label_bias():
    policy = SamplingPolicy(p_autonomous=1.0, p_escalated=1.0, seed=42)
    session = ShadowEvaluationSession(sampling_policy=policy)

    session.record_query(
        query_id="q_auto",
        query_text="autonomous query",
        baseline_decision=DecisionAction.AUTONOMOUS,
        candidate_decision=DecisionAction.AUTONOMOUS,
        baseline_score=0.9,
        candidate_score=0.92,
        baseline_top_id="d1",
        candidate_top_id="d1",
    )
    session.record_query(
        query_id="q_esc",
        query_text="escalated query",
        baseline_decision=DecisionAction.HITL_REVIEW,
        candidate_decision=DecisionAction.HITL_REVIEW,
        baseline_score=0.5,
        candidate_score=0.55,
        baseline_top_id="d2",
        candidate_top_id="d2",
    )

    assert session.records[0].sampled_for_human_review is True
    assert session.records[1].sampled_for_human_review is True


def test_horvitz_thompson_ipw_unbiasing_oversampled_strata():
    # Suppose escalated queries are sampled with p=0.30 (wt=3.33) and autonomous with p=0.10 (wt=10.0)
    # 9 autonomous samples (all 0 errors) -> wt = 9 * 10 = 90
    # 3 escalated samples (1 error, 2 no-error) -> wt = 3 * 3.333 = 10, error wt = 3.333
    samples = [
        IPWAdjudicationSample("q_a1", DecisionAction.AUTONOMOUS, 0.10, 10.0, False),
        IPWAdjudicationSample("q_a2", DecisionAction.AUTONOMOUS, 0.10, 10.0, False),
        IPWAdjudicationSample("q_a3", DecisionAction.AUTONOMOUS, 0.10, 10.0, False),
        IPWAdjudicationSample("q_e1", DecisionAction.HITL_REVIEW, 0.30, 10.0 / 3.0, True),
        IPWAdjudicationSample("q_e2", DecisionAction.HITL_REVIEW, 0.30, 10.0 / 3.0, False),
        IPWAdjudicationSample("q_e3", DecisionAction.HITL_REVIEW, 0.30, 10.0 / 3.0, False),
    ]

    res = compute_horvitz_thompson_ipw(samples)
    # Raw error rate = 1 / 6 = 16.67%
    assert round(res.raw_error_rate, 4) == 0.1667
    # IPW error rate = (10/3) / (30 + 10) = 3.333 / 40 = 8.33%
    # Accurately downweights the oversampled escalated stratum
    assert round(res.ipw_error_rate, 4) == 0.0833
    assert res.effective_sample_size > 0.0
    assert res.ci_upper > res.ipw_error_rate


def test_drift_computation_and_fallback_accounting():
    session = ShadowEvaluationSession()
    for i in range(10):
        session.record_query(
            query_id=f"q_{i}",
            query_text=f"query {i}",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS if i < 8 else DecisionAction.HITL_REVIEW,
            baseline_score=0.70,
            candidate_score=0.85,
            baseline_top_id=f"d_{i}",
            candidate_top_id=f"d_{i}",
            fallback_used=(i == 0),
        )

    drift = session.compute_drift()
    assert round(drift.score_drift, 2) == 0.15
    assert round(drift.route_shift_rate, 2) == 0.20
    assert round(drift.fallback_rate, 2) == 0.10
    assert drift.drift_breached is True


def test_observation_window_stopping_rules():
    config = ObservationConfig(min_sample_size=5, max_sample_size=10, max_allowed_error_rate=0.10)
    session = ShadowEvaluationSession(config=config)

    for i in range(4):
        session.record_query(
            query_id=f"q_{i}",
            query_text=f"q {i}",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS,
            baseline_score=0.8,
            candidate_score=0.8,
            baseline_top_id="d1",
            candidate_top_id="d1",
        )
    is_stopped, reason = session.check_stopping_rule()
    assert is_stopped is False
    assert "WINDOW_ACCUMULATING" in reason

    session.record_query(
        query_id="q_4",
        query_text="q 4",
        baseline_decision=DecisionAction.AUTONOMOUS,
        candidate_decision=DecisionAction.AUTONOMOUS,
        baseline_score=0.8,
        candidate_score=0.8,
        baseline_top_id="d1",
        candidate_top_id="d1",
    )
    is_stopped, reason = session.check_stopping_rule()
    assert is_stopped is True
    assert "MIN_SAMPLE_WINDOW_SATISFIED" in reason


def test_rollback_engine_triggers_and_physical_restoration():
    engine = RollbackEngine()
    assert engine.state == ShadowState.SHADOW_ACTIVE

    with tempfile.TemporaryDirectory(prefix="rt_unit_") as tmp_dir:
        tmp_p = Path(tmp_dir)
        base_pair = {"generation_id": "gen_base_01", "model_id": "BAAI/bge-m3"}
        (tmp_p / "active_pair.json.bak").write_text(json.dumps(base_pair) + "\n", encoding="utf-8")
        (tmp_p / "active_pair.json").write_text('{"generation_id": "gen_corrupt"}\n', encoding="utf-8")

        ev = engine.execute_physical_rollback(
            tmp_p, RollbackTrigger.MODEL_MISMATCH, "Model weights hash mismatch"
        )
        assert engine.is_rolled_back is True
        assert ev.trigger == RollbackTrigger.MODEL_MISMATCH
        assert ev.baseline_restored is True

        # Independent check: read back active_pair.json
        on_disk = json.loads((tmp_p / "active_pair.json").read_text(encoding="utf-8"))
        assert on_disk["generation_id"] == "gen_base_01"
        assert "gen_corrupt" not in json.dumps(on_disk)

        # Verify via ledger_verifier
        ok, msg = verify_active_pair_rollback(tmp_p / "active_pair.json", ev.restored_sha256)
        assert ok is True


def test_consumer_metrics_enforcement():
    config = ObservationConfig(min_sample_size=3, min_consumer_durability=0.85, max_consumer_staleness=0.10)
    session = ShadowEvaluationSession(config=config)
    for i in range(3):
        session.record_query(
            query_id=f"q_{i}",
            query_text=f"q {i}",
            baseline_decision=DecisionAction.AUTONOMOUS,
            candidate_decision=DecisionAction.AUTONOMOUS,
            baseline_score=0.8,
            candidate_score=0.8,
            baseline_top_id="d1",
            candidate_top_id="d1",
            critical_slice="executive" if i == 0 else ("engineering" if i == 1 else "shorthand"),
        )

    # Invalidate durability
    session.consumer_metrics = ConsumerMetrics(query_engine_durability=0.75, query_engine_staleness=0.0)
    eligibility = session.evaluate_promotion_eligibility(adjudicated_errors=0, adjudicated_total=3)
    assert eligibility.is_eligible is False
    assert any("CONSUMER_DURABILITY_BREACH" in r for r in eligibility.reasons)
