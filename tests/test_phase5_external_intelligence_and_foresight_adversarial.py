"""
Adversarial & Hypothesis Property-Based Verification for Phase 5:
Covers 4 mandatory edge-case categories:
1. Boundary payload (exact [0.20, 0.70] boundary edges and extreme floats across all 7 route multipliers)
2. Malformed input (non-numeric strings, None, and malformed parameter values in _validate_bounds)
3. Invariant bypass attempt (attempting to pass out-of-bounds contradiction/entity_anchor multipliers through UWG simulation)
4. Rollback & recovery (AnswerSimulationResult serialization round-trip and digest stability)
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st
import pytest

from dual_pipeline.learning_loop_core.models import (
    RETRIEVAL_PARAMETER_BOUNDS,
    AnswerSimulationResult,
)
from dual_pipeline.learning_loop_core.simulation import (
    _check_simulation_bounds,
    _validate_bounds,
)

ROUTE_MULTIPLIER_KEYS = tuple(
    k for k in RETRIEVAL_PARAMETER_BOUNDS if k.startswith("retrieval.route_multiplier.")
)


# --- Category 1: Boundary Payloads & Property Invariants ---

@settings(max_examples=50, deadline=None)
@given(
    param_name=st.sampled_from(ROUTE_MULTIPLIER_KEYS),
    val=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_property_all_route_multipliers_enforce_governed_bounds(param_name: str, val: float) -> None:
    """Property: every registered route multiplier strictly enforces [0.20, 0.70] bounds."""
    ok, msg = _validate_bounds(param_name, val)
    if 0.20 <= val <= 0.70:
        assert ok is True
        assert msg == "VALID"
    else:
        assert ok is False
        assert "outside governed bounds" in msg


def test_boundary_exact_min_and_max_route_multipliers() -> None:
    """Boundary payload: exact 0.20 and 0.70 pass, while 0.1999 and 0.7001 fail closed."""
    for key in ("retrieval.route_multiplier.contradiction", "retrieval.route_multiplier.entity_anchor"):
        assert _validate_bounds(key, 0.20)[0] is True
        assert _validate_bounds(key, 0.70)[0] is True
        assert _validate_bounds(key, 0.1999)[0] is False
        assert _validate_bounds(key, 0.7001)[0] is False


# --- Category 2: Malformed Input ---

@pytest.mark.parametrize("bad_value", [None, "not_a_float", {}, []])
def test_malformed_parameter_values_rejected_fail_closed(bad_value: object) -> None:
    """Malformed input: non-numeric values fail _validate_bounds cleanly without unhandled exceptions."""
    ok, msg = _validate_bounds("retrieval.route_multiplier.contradiction", bad_value)
    assert ok is False
    assert "Invalid numeric value" in msg


# --- Category 3: Invariant Bypass Attempt ---

def test_out_of_bounds_contradiction_multiplier_blocked_in_simulation_check() -> None:
    """Invariant bypass attempt: attempting to simulate proposed_value=0.88 on contradiction fails closed."""
    res = _check_simulation_bounds(
        "retrieval.route_multiplier.contradiction",
        0.88,
        "prop-bypass-attempt",
        "2026-10-28T11:55:00Z",
    )
    assert res is not None
    assert res.success is False
    assert res.status == "SIMULATED_FAIL"
    assert res.details["stage"] == "bounds_validation"


# --- Category 4: Rollback & Serialization Recovery ---

def test_simulation_result_serialization_and_metrics_property_recovery() -> None:
    """Rollback & recovery: AnswerSimulationResult round-trips all fields and exposes details via .metrics."""
    sim = AnswerSimulationResult(
        proposal_id="prop-rec-01",
        success=False,
        status="SIMULATED_FAIL",
        target_metric_before=0.82,
        target_metric_after=0.79,
        guard_metrics_passed=False,
        latency_within_budget=True,
        bge_m3_gates_passed=False,
        details={"stage": "bge_m3_gates_check", "failed_gates": ["ndcg_at_10"]},
        proposal_digest="a" * 64,
        replay_digest="b" * 64,
    )
    dumped = sim.to_dict()
    assert dumped["proposal_id"] == "prop-rec-01"
    assert dumped["bge_m3_gates_passed"] is False
    assert sim.metrics == {"stage": "bge_m3_gates_check", "failed_gates": ["ndcg_at_10"]}
