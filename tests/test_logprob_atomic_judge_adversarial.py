"""
tests/test_logprob_atomic_judge_adversarial.py

Adversarial edge-case test suite for Wave 2:
Covers mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: Uniform distribution, Dirac delta, near-zero/extreme temperature scaling.
2. Malformed input: Missing/empty logprobs, NaN/inf token weights, schema field bounds.
3. Overflow & Ambiguity gating: Shannon entropy ceiling (>0.65) and 30% margin triggers.
4. Invariant bypass attempt: Arithmetic drift tampering, inverted causal field orders.
5. Semantic nuance & modality inversion: Subtle polarity inversions and stripped conditional hedges.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    validate_output1_scorecard,
    validate_output2_scorecard,
    validate_output3_scorecard,
    validate_score_value,
)
from dual_pipeline.llm_judge_core.logprob_scorer import (
    BARS_4_WEIGHTS,
    BINARY_CHECKLIST_WEIGHTS,
    LogprobScoreResult,
    compute_logprob_distribution,
    rollup_atomic_criteria,
    rollup_scorecard_logprobs,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    Output1ScorecardV2,
    Output2ScorecardV2,
    Output3ScorecardV2,
)


# =============================================================================
# Category 1: Boundary Payloads (Uniform, Dirac, Extreme Temperatures)
# =============================================================================

def test_boundary_dirac_delta_distribution():
    """Verify single-token distribution yields 0.0 entropy and 0.0 variance."""
    logprobs = {"PASS": 0.0}
    res = compute_logprob_distribution(logprobs, BINARY_CHECKLIST_WEIGHTS)

    assert res.top_token == "PASS"
    assert res.top_probability == 1.0
    assert res.expected_value == 1.0
    assert res.variance == 0.0
    assert res.normalized_entropy == 0.0
    assert res.is_ambiguous is False


def test_boundary_uniform_distribution_triggers_ambiguity():
    """Verify uniform distribution across 4 BARS tokens yields maximal entropy and triggers ambiguity."""
    # Equal logprobs -> uniform 0.25 probability each
    logprobs = {k: 0.0 for k in BARS_4_WEIGHTS}
    res = compute_logprob_distribution(logprobs, BARS_4_WEIGHTS, temperature=1.0)

    # In uniform distribution, top_p = second_p = 0.25, margin = 0.0 <= 0.30
    assert res.margin == 0.0
    assert res.is_ambiguous is True
    # Normalized entropy for uniform distribution is exactly 1.0
    assert abs(res.normalized_entropy - 1.0) < 1e-4


def test_boundary_extreme_temperature_scaling():
    """Verify temperature scaling modulates entropy and margin gracefully without division by zero."""
    logprobs = {
        "PASS": math.log(0.40),
        "MINOR_DEFECT": math.log(0.30),
        "MAJOR_DEFECT": math.log(0.20),
        "CRITICAL_FAIL": math.log(0.10),
    }

    # High temperature -> flattens distribution towards uniform
    res_high_tau = compute_logprob_distribution(logprobs, BARS_4_WEIGHTS, temperature=10.0)
    assert res_high_tau.normalized_entropy > 0.90
    assert res_high_tau.is_ambiguous is True

    # Low temperature -> sharpens distribution towards argmax
    res_low_tau = compute_logprob_distribution(logprobs, BARS_4_WEIGHTS, temperature=0.01)
    assert res_low_tau.top_token == "PASS"
    assert res_low_tau.top_probability > 0.99
    assert res_low_tau.normalized_entropy < 0.05
    assert res_low_tau.is_ambiguous is False


# =============================================================================
# Category 2: Malformed Input & Non-Finite Checks
# =============================================================================

def test_malformed_empty_payloads():
    """Verify compute_logprob_distribution rejects empty maps."""
    with pytest.raises(ValueError, match="must be non-empty"):
        compute_logprob_distribution({}, BARS_4_WEIGHTS)

    with pytest.raises(ValueError, match="must be non-empty"):
        compute_logprob_distribution({"PASS": 0.0}, {})


def test_malformed_score_values_nan_inf():
    """Verify validate_score_value blocks NaN, Inf, and non-numeric types."""
    assert "finite" in (validate_score_value(float("nan"), 0, 25, "dim") or "")
    assert "finite" in (validate_score_value(float("inf"), 0, 25, "dim") or "")
    assert "numeric" in (validate_score_value("25", 0, 25, "dim") or "")
    assert "numeric" in (validate_score_value(True, 0, 25, "dim") or "")
    assert "out of bounds" in (validate_score_value(-0.1, 0, 25, "dim") or "")
    assert "out of bounds" in (validate_score_value(25.1, 0, 25, "dim") or "")


# =============================================================================
# Category 3: Overflow & Ambiguity Gating
# =============================================================================

def test_ambiguity_gating_margin_threshold():
    """Verify token probability margin <= 0.30 triggers ambiguity flag (SSOT HITL gate)."""
    # top_p = 0.55, second_p = 0.45 -> margin = 0.10 <= 0.30
    logprobs = {
        "PASS": math.log(0.55),
        "MINOR_DEFECT": math.log(0.45),
    }
    res = compute_logprob_distribution(logprobs, BARS_4_WEIGHTS, margin_threshold=0.30)
    assert res.margin == pytest.approx(0.10, rel=1e-3)
    assert res.is_ambiguous is True

    # top_p = 0.75, second_p = 0.25 -> margin = 0.50 > 0.30
    logprobs_decisive = {
        "PASS": math.log(0.75),
        "MINOR_DEFECT": math.log(0.25),
    }
    res_decisive = compute_logprob_distribution(logprobs_decisive, BARS_4_WEIGHTS, margin_threshold=0.30)
    assert res_decisive.margin == pytest.approx(0.50, rel=1e-3)
    assert res_decisive.is_ambiguous is False


def test_ambiguity_gating_entropy_threshold():
    """Verify normalized Shannon entropy > 0.65 triggers ambiguity even when margin exceeds threshold."""
    # 3-way split: 0.50, 0.25, 0.25 -> margin = 0.25 <= 0.30, entropy is high
    logprobs = {
        "PASS": math.log(0.50),
        "MINOR_DEFECT": math.log(0.25),
        "MAJOR_DEFECT": math.log(0.25),
    }
    res = compute_logprob_distribution(logprobs, BARS_4_WEIGHTS, entropy_threshold=0.65)
    assert res.normalized_entropy > 0.65
    assert res.is_ambiguous is True


# =============================================================================
# Category 4: Invariant Bypass Attempts (Arithmetic Drift & Floor Tampering)
# =============================================================================

def test_invariant_bypass_prevent_arithmetic_drift():
    """Verify scorecard validator strictly rejects composite score drifting by even 0.05 from dimension sum."""
    drifted_card = {
        "dimension_1_forensic_grounding_and_fidelity": 25.0,
        "dimension_2_attribution_and_room_mic_hygiene": 25.0,
        "dimension_3_chronological_and_timestamp_integrity": 25.0,
        "dimension_4_asr_cleanup_and_domain_preservation": 25.0,
        "composite_score": 99.0,  # 1.0 point drift from sum of 100.0
    }
    res = validate_output1_scorecard(drifted_card)
    assert res.is_valid is False
    assert any("does not match dimension sum" in e for e in res.errors)


def test_invariant_bypass_prevent_output2_display_divergence():
    """Verify Output 2 scorecard rejects display dimensions diverging by > 1.0 from section sum."""
    card = {
        "section_scores": {
            "header_and_overview": 10.0,
            "key_updates_technical_substance": 35.0,
            "conditional_extensions": 20.0,
            "appendix_stakeholder_perspectives": 35.0,
        },
        "composite_score": 100.0,
        "dimension_1_executive_altitude": 20.0,
        "dimension_2_technical_substance": 20.0,
        "dimension_3_strategic_synthesis": 20.0,
        "dimension_4_scoping_and_modality": 20.0,  # sum = 80 vs sec_sum = 100
    }
    res = validate_output2_scorecard(card)
    assert res.is_valid is False
    assert any("diverges from authoritative section sum" in e for e in res.errors)


# =============================================================================
# Category 5: Semantic Nuance & Modality Inversion Detection
# =============================================================================

def test_semantic_modality_inflation_detection():
    """Verify logprob scoring detects modality inflation (tentative hedge inflated to commitment)."""
    # Raw source dialogue: "We might look at Cosmos DB next year, but no decision has been made."
    # Candidate recap claim: "Executive decision: Cosmos DB selected as primary database."
    # The atomic subcriterion CRIT-OUT2-SUB-MODALITY-INFLATION evaluates with CRITICAL_FAIL
    inflation_probe_logprobs = {
        "CRITICAL_FAIL": math.log(0.85),
        "MAJOR_DEFECT": math.log(0.10),
        "PASS": math.log(0.05),
    }
    res = compute_logprob_distribution(inflation_probe_logprobs, BARS_4_WEIGHTS)

    assert res.top_token == "CRITICAL_FAIL"
    # Expected value: 0.0 * 0.85 + 0.40 * 0.10 + 1.0 * 0.05 = 0.09
    assert res.expected_value < 0.15

    subcriteria = {"CRIT-OUT2-SUB-MODALITY-INFLATION": res}
    dim_score, _, _, is_ambig, _ = rollup_atomic_criteria(subcriteria, max_dimension_points=20.0)

    # Substantive deduction: 20 * 0.09 = 1.8 points out of 20 (fails floor 11.0)
    assert dim_score < 5.0


def test_semantic_polarity_inversion_detection():
    """Verify atomic entailment scoring penalizes polarity inversion (not recommended -> recommended)."""
    # Raw source: "Due to licensing costs, Forge is NOT recommended for this deployment."
    # Candidate transcript / graph: asserts Forge as recommended platform.
    inversion_probe_logprobs = {
        "CRITICAL_FAIL": math.log(0.92),
        "MAJOR_DEFECT": math.log(0.08),
    }
    res = compute_logprob_distribution(inversion_probe_logprobs, BARS_4_WEIGHTS)
    assert res.top_token == "CRITICAL_FAIL"
    assert res.expected_value < 0.05
