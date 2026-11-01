"""Adversarial and edge-case tests for AdaptiveRetrievalCalibratorAgent.

Covers mandatory edge-case categories:
1. Boundary & extreme multiplier clamping (empty cohort, invalid max_steps, extreme weights -5.0 and +99.0 clamped to [0.20, 0.70]).
2. Holdout 11-gate regression rejection (candidate regressing on ndcg_at_10, p95_latency_ms, or stale_leakage_rate rejected with proposal_artifact=None).
3. Invariant bypass attempts (auto_apply=True, approval_required=False, spoofed bge_gates with regressed metrics, tampered provenance).
4. Permutation invariance & Bayesian single-cycle step cap (|delta| <= 0.15).
"""
from __future__ import annotations

import copy
from pathlib import Path
import random

import pytest

from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core.retrieval_calibrator_agent import (
    AdaptiveRetrievalCalibratorAgent,
    clamp_route_multiplier,
    normalize_hybrid_weights,
)


def test_boundary_and_extreme_multiplier_clamping_fail_closed() -> None:
    """Category 1: Boundary inputs rejected and extreme multipliers clamped to [0.20, 0.70]."""
    with pytest.raises(ValueError, match="max_steps"):
        AdaptiveRetrievalCalibratorAgent(max_steps=0)

    with pytest.raises(ValueError, match="max_tool_calls"):
        AdaptiveRetrievalCalibratorAgent(max_tool_calls=0)

    with pytest.raises(ValueError, match="max_step_delta"):
        AdaptiveRetrievalCalibratorAgent(max_step_delta=0.0)

    calibrator = AdaptiveRetrievalCalibratorAgent()
    with pytest.raises(ValueError, match="non-empty"):
        calibrator.calibrate(
            failure_cohort=[],
            current_multipliers={"relational": 0.45},
            baseline_metrics={"ndcg_at_10": 0.80},
            candidate_metrics={"ndcg_at_10": 0.85},
        )

    assert clamp_route_multiplier(-5.0) == 0.20
    assert clamp_route_multiplier(0.01) == 0.20
    assert clamp_route_multiplier(99.9) == 0.70
    weights = normalize_hybrid_weights(50.0, 0.001)
    assert 0.10 <= weights["dense_weight"] <= 0.90
    assert round(weights["dense_weight"] + weights["sparse_weight"], 4) == 1.0


def test_eleven_gate_holdout_regression_rejects_proposal() -> None:
    """Category 2: Any regression across the 11 BGE-M3 gates blocks proposal emission and caps single-step jump at +0.15."""
    calibrator = AdaptiveRetrievalCalibratorAgent()
    cohort = [{"event_id": "ev_reg_0001", "missed_dimension": "relational", "suggested_multiplier": 0.95}]
    baseline = {
        "ndcg_at_10": 0.85,
        "recall_at_50": 0.90,
        "p95_latency_ms": 120.0,
        "stale_leakage_rate": 0.00,
    }
    regressed_candidate = {
        "ndcg_at_10": 0.82,  # regressed (-0.03)
        "recall_at_50": 0.92,
        "p95_latency_ms": 145.0,  # regressed (+25ms)
        "stale_leakage_rate": 0.02,  # regressed (+0.02)
    }

    outcome = calibrator.calibrate(
        failure_cohort=cohort,
        current_multipliers={"relational": 0.45},
        baseline_metrics=baseline,
        candidate_metrics=regressed_candidate,
    )
    assert outcome.accepted is False
    assert outcome.proposal_artifact is None
    # Single-cycle step cap (+0.15) prevents 0.45 -> 0.70 jump in one step (0.45 + 0.15 = 0.60)
    assert outcome.clamped_multipliers["relational"] == 0.60
    assert any("REGRESSION_ON_NDCG_AT_10" in r for r in outcome.rejection_reasons)
    assert any("REGRESSION_ON_P95_LATENCY_MS" in r for r in outcome.rejection_reasons)
    assert any("REGRESSION_ON_STALE_LEAKAGE_RATE" in r for r in outcome.rejection_reasons)

    # Starting from 0.60 with +0.15 cap hits upper bound [0.20, 0.70]
    outcome_clamped = calibrator.calibrate(
        failure_cohort=cohort,
        current_multipliers={"relational": 0.60},
        baseline_metrics=baseline,
        candidate_metrics=regressed_candidate,
    )
    assert outcome_clamped.clamped_multipliers["relational"] == 0.70


def test_permutation_invariance_and_step_cap_under_disordered_cohorts() -> None:
    """Category 4: Multi-event cohorts for the same dimension produce identical multipliers regardless of ordering."""
    calibrator = AdaptiveRetrievalCalibratorAgent()
    events = [
        {"event_id": "ev_1", "missed_dimension": "relational", "suggested_multiplier": 0.52, "severity": 0.9},
        {"event_id": "ev_2", "missed_dimension": "relational", "suggested_multiplier": 0.68, "severity": 0.2},
        {"event_id": "ev_3", "missed_dimension": "relational", "suggested_multiplier": 0.49, "severity": 0.6},
        {"event_id": "ev_4", "missed_dimension": "temporal", "suggested_multiplier": 0.62, "severity": 0.8},
        {"event_id": "ev_5", "missed_dimension": "temporal", "suggested_multiplier": 0.30, "severity": 0.4},
    ]
    baseline = {"ndcg_at_10": 0.80, "recall_at_50": 0.85}
    candidate = {"ndcg_at_10": 0.86, "recall_at_50": 0.90}
    curr = {"relational": 0.45, "temporal": 0.45}

    ref_outcome = calibrator.calibrate(
        failure_cohort=events,
        current_multipliers=curr,
        baseline_metrics=baseline,
        candidate_metrics=candidate,
    )
    rng = random.Random(42)
    for _ in range(10):
        shuffled = list(events)
        rng.shuffle(shuffled)
        out = calibrator.calibrate(
            failure_cohort=shuffled,
            current_multipliers=curr,
            baseline_metrics=baseline,
            candidate_metrics=candidate,
        )
        assert out.clamped_multipliers == ref_outcome.clamped_multipliers
        for dim, val in out.clamped_multipliers.items():
            assert abs(val - curr[dim]) <= 0.15 + 1e-9


def test_spoofed_bge_gates_and_tampered_provenance_rejected_by_clle(tmp_path: Path) -> None:
    """Category 3: Spoofed bge_gates with regressed holdout metrics or tampered proposed_change fails closed."""
    calibrator = AdaptiveRetrievalCalibratorAgent()
    outcome = calibrator.calibrate(
        failure_cohort=[{"event_id": "ev_sp_01", "missed_dimension": "relational", "suggested_multiplier": 0.55}],
        current_multipliers={"relational": 0.45},
        baseline_metrics={"ndcg_at_10": 0.80, "p95_latency_ms": 120.0},
        candidate_metrics={"ndcg_at_10": 0.85, "p95_latency_ms": 115.0},
    )
    assert outcome.proposal_artifact is not None
    engine = ClosedLoopLearningEngine(runtime_root=tmp_path, proposals_dir=tmp_path / "proposals")

    # Tamper with candidate_metrics while keeping bge_gates all True
    spoofed = copy.deepcopy(outcome.proposal_artifact)
    spoofed["extensions"]["com.partner_advisory.nee.calibrator"]["candidate_metrics"]["p95_latency_ms"] = 195.0
    sim_spoofed = engine.simulate_proposal(spoofed)
    assert sim_spoofed.success is False
    assert sim_spoofed.status == "SIMULATED_FAIL"
    assert "REGRESSION_ON_P95_LATENCY_MS" in sim_spoofed.details.get("error", "")

    # Tamper with proposed_change without re-signing provenance
    tampered = copy.deepcopy(outcome.proposal_artifact)
    tampered["proposed_change"] = '{"route_multipliers": {"relational": 0.70}}'
    prov_ok, prov_err = engine.verify_provenance(tampered)
    assert prov_ok is False
    assert "does not match" in prov_err.lower() or "provenance hash" in prov_err.lower()


def test_invariant_bypass_auto_apply_rejected() -> None:
    """Category 3: Attempting auto_apply=True or approval_required=False raises PermissionError."""
    calibrator = AdaptiveRetrievalCalibratorAgent()
    cohort = [{"event_id": "ev_bypass_0001", "missed_dimension": "relational"}]

    with pytest.raises(PermissionError, match="PROPOSE_ONLY_INVARIANT_VIOLATION"):
        calibrator.calibrate(
            failure_cohort=cohort,
            current_multipliers={"relational": 0.45},
            baseline_metrics={"ndcg_at_10": 0.80},
            candidate_metrics={"ndcg_at_10": 0.85},
            auto_apply=True,
        )

    with pytest.raises(PermissionError, match="PROPOSE_ONLY_INVARIANT_VIOLATION"):
        calibrator.calibrate(
            failure_cohort=cohort,
            current_multipliers={"relational": 0.45},
            baseline_metrics={"ndcg_at_10": 0.80},
            candidate_metrics={"ndcg_at_10": 0.85},
            approval_required=False,
        )
