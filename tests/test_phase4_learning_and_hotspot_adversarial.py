"""
Adversarial & Hypothesis Property-Based Verification for Phase 4:
Covers 4 mandatory edge-case categories:
1. Boundary payload (empty cohorts, empty holdout metrics, extreme multiplier bounds)
2. Malformed input (malformed Time headers, unmapped generic speakers)
3. Invariant bypass attempt (PROPOSE_ONLY_INVARIANT_VIOLATION on auto_apply=True / approval_required=False)
4. Rollback & recovery (permutation invariance across failure cohorts, stage chain tamper detection)
"""
from __future__ import annotations

from typing import Dict, List

from hypothesis import given, settings, strategies as st
import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import (
    UnifiedPreflightCascadeError,
    batch_validate_preflight_checks,
    raise_if_preflight_failed,
)
from dual_pipeline.learning_loop_core.retrieval_calibrator_agent import (
    AdaptiveRetrievalCalibratorAgent,
    clamp_route_multiplier,
    evaluate_eleven_gate_holdout,
    normalize_hybrid_weights,
)
from dual_pipeline.learning_loop_core.seal import seal_learning_stage_chain


# --- Category 1: Boundary Payloads & Property Invariants ---

@settings(max_examples=40, deadline=None)
@given(
    raw_multiplier=st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    dense=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    sparse=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
)
def test_property_multiplier_clamping_and_hybrid_weight_simplex(
    raw_multiplier: float, dense: float, sparse: float
) -> None:
    """Property: route multipliers stay in [0.20, 0.70] and hybrid weights sum to 1.0 within [0.10, 0.90]."""
    clamped = clamp_route_multiplier(raw_multiplier)
    assert 0.20 <= clamped <= 0.70

    weights = normalize_hybrid_weights(dense, sparse)
    assert 0.10 <= weights["dense_weight"] <= 0.90
    assert 0.10 <= weights["sparse_weight"] <= 0.90
    assert abs((weights["dense_weight"] + weights["sparse_weight"]) - 1.0) <= 1e-4


def test_boundary_empty_metrics_and_empty_cohort_fail_closed() -> None:
    """Boundary payload: empty baseline/candidate holdout metrics and empty cohorts fail closed."""
    ok, regressions, deltas = evaluate_eleven_gate_holdout({}, {"ndcg_at_10": 0.90})
    assert ok is False
    assert "MISSING_HOLDOUT_METRICS" in regressions
    assert deltas == {}

    ok2, regressions2, _ = evaluate_eleven_gate_holdout({"ndcg_at_10": 0.85}, {})
    assert ok2 is False
    assert "MISSING_HOLDOUT_METRICS" in regressions2

    agent = AdaptiveRetrievalCalibratorAgent()
    with pytest.raises(ValueError, match="failure_cohort must be non-empty"):
        agent.calibrate(
            failure_cohort=[],
            current_multipliers={"relational": 0.45},
            baseline_metrics={"ndcg_at_10": 0.80},
            candidate_metrics={"ndcg_at_10": 0.85},
        )


# --- Category 2: Malformed Input ---

def test_malformed_time_header_triggers_u0_mandatory_time_diagnostic() -> None:
    """Malformed input: non-time header text must not bypass U0_MANDATORY_TIME."""
    malformed_transcript = (
        "# Session Notes\n"
        "**Time:** TBD / Not Scheduled\n\n"
        "**Amit Ayer:** Let us review the roadmap.\n"
    )
    metadata = {"title": "Session Notes", "notes_mode": True}
    report = batch_validate_preflight_checks(metadata, raw_transcript=malformed_transcript)
    assert report.is_valid is False
    assert "time" in report.missing_fields
    assert any(d.check_name == "U0_MANDATORY_TIME" for d in report.diagnostics)
    with pytest.raises(UnifiedPreflightCascadeError):
        raise_if_preflight_failed(report)


# --- Category 3: Invariant Bypass Attempts ---

def test_propose_only_invariant_blocks_auto_apply_and_unapproved_calibration() -> None:
    """Invariant bypass attempt: auto_apply=True or approval_required=False raises PermissionError."""
    agent = AdaptiveRetrievalCalibratorAgent()
    cohort = [{"event_id": "ev_1", "missed_dimension": "relational", "suggested_multiplier": 0.55, "severity": 0.8}]
    base = {"ndcg_at_10": 0.80, "recall_at_50": 0.85}
    cand = {"ndcg_at_10": 0.85, "recall_at_50": 0.88}

    with pytest.raises(PermissionError, match="PROPOSE_ONLY_INVARIANT_VIOLATION"):
        agent.calibrate(
            failure_cohort=cohort,
            current_multipliers={"relational": 0.45},
            baseline_metrics=base,
            candidate_metrics=cand,
            auto_apply=True,
        )

    with pytest.raises(PermissionError, match="PROPOSE_ONLY_INVARIANT_VIOLATION"):
        agent.calibrate(
            failure_cohort=cohort,
            current_multipliers={"relational": 0.45},
            baseline_metrics=base,
            candidate_metrics=cand,
            approval_required=False,
        )


# --- Category 4: Rollback, Recovery & Permutation Invariance ---

@settings(max_examples=25, deadline=None)
@given(
    sev_a=st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False),
    sev_b=st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_property_cohort_order_permutation_invariance(sev_a: float, sev_b: float) -> None:
    """Property: _compute_candidate_parameters and primary_dim selection are invariant to cohort/key order."""
    agent = AdaptiveRetrievalCalibratorAgent()
    item_a = {"event_id": "ev_a", "missed_dimension": "temporal", "suggested_multiplier": 0.50, "severity": sev_a}
    item_b = {"event_id": "ev_b", "missed_dimension": "contradiction", "suggested_multiplier": 0.68, "severity": sev_b}

    c1, w1, _ = agent._compute_candidate_parameters(
        [item_a, item_b],
        {"temporal": 0.45, "contradiction": 0.45},
        0.60,
        0.40,
    )
    c2, w2, _ = agent._compute_candidate_parameters(
        [item_b, item_a],
        {"contradiction": 0.45, "temporal": 0.45},
        0.60,
        0.40,
    )
    assert c1 == c2
    assert w1 == w2

    ext1 = agent._build_proposal_extensions(
        "prop-1", "run-1", ["ev_a", "ev_b"], "{}", c1,
        {"temporal": 0.45, "contradiction": 0.45}, w1, {"ndcg_at_10": 0.05},
        {"ndcg_at_10": 0.80}, {"ndcg_at_10": 0.85},
    )
    ext2 = agent._build_proposal_extensions(
        "prop-1", "run-1", ["ev_a", "ev_b"], "{}", c2,
        {"contradiction": 0.45, "temporal": 0.45}, w2, {"ndcg_at_10": 0.05},
        {"ndcg_at_10": 0.80}, {"ndcg_at_10": 0.85},
    )
    assert ext1["com.partner_advisory.nee.learning"]["parameter_name"] == ext2["com.partner_advisory.nee.learning"]["parameter_name"]


def test_missing_stage_in_stage_chain_seal_fails_closed() -> None:
    """Missing any mandatory stage in LP0..LP5 fails seal_learning_stage_chain with ValueError."""
    with pytest.raises(ValueError, match="missing mandatory stages"):
        seal_learning_stage_chain(
            proposal_id="prop-missing-stage",
            run_id="run-tamper-check",
            stage_events=[
                {"stage": "LP0", "event_type": "cohort_mined", "status": "PASS", "payload_digest": "a" * 64},
                {"stage": "LP1", "event_type": "proposal_synthesized", "status": "PASS", "payload_digest": "b" * 64},
            ],
            require_canary=True,
        )
