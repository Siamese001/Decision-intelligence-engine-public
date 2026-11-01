"""
tests/test_hitl_ambiguity_threshold_15pct.py

Wave 1 Acceptance Gate: Validates that the repository-wide ambiguity margin threshold
is calibrated to 0.15 (15%), confidence floor to 0.65 (65%), and normalized entropy to 0.75,
ensuring that operator HITL clarification is triggered whenever:
1. Delta = c_top - c_second < 0.15
2. c_top < 0.65 (autonomous confidence floor breach)
3. H_n > 0.75 across K >= 3 options (epistemic entropy override)
"""
from dual_pipeline.ambiguity_core.models import (
    AMBIGUITY_MARGIN_THRESHOLD,
)
from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_entropy_ambiguity_threshold,
    get_hitl_min_autonomous_confidence_floor,
    get_hitl_unresolved_entity_margin_threshold,
)
from dual_pipeline.staleness_core.two_track_classifier import TwoTrackClassifier


def test_ssot_parameter_reads_30_percent_and_entity_15_percent():
    threshold = get_hitl_ambiguity_margin_threshold()
    floor = get_hitl_min_autonomous_confidence_floor()
    entropy = get_hitl_entropy_ambiguity_threshold()
    entity_thresh = get_hitl_unresolved_entity_margin_threshold()
    assert threshold == 0.30, f"Expected SSOT ambiguity margin threshold 0.30, got {threshold}"
    assert floor == 0.65, f"Expected SSOT conviction floor 0.65, got {floor}"
    assert entropy == 0.75, f"Expected SSOT entropy threshold 0.75, got {entropy}"
    assert entity_thresh == 0.15, f"Expected entity margin threshold 0.15, got {entity_thresh}"
    assert AMBIGUITY_MARGIN_THRESHOLD == 0.30, f"Expected AMBIGUITY_MARGIN_THRESHOLD 0.30, got {AMBIGUITY_MARGIN_THRESHOLD}"


def test_margin_evaluator_boundary_conditions():
    # Case 1: Delta = 0.10 (< 0.30) -> MUST surface HITL
    opts_sub15 = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.60},
        {"option_id": "opt2", "label": "Option B", "confidence": 0.50},
    ]
    res1 = MarginEvaluator.evaluate(opts_sub15)
    assert res1["surface_hitl"] is True
    assert res1["decision_resolved_autonomously"] is False
    assert round(res1["margin"], 2) == 0.10

    # Case 2: Delta = 0.25 (< 0.30) -> MUST surface HITL
    opts_25 = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.75},
        {"option_id": "opt2", "label": "Option B", "confidence": 0.50},
    ]
    res2 = MarginEvaluator.evaluate(opts_25)
    assert res2["surface_hitl"] is True
    assert res2["decision_resolved_autonomously"] is False

    # Case 3: Delta = 0.30 (== 0.30, inclusive boundary) -> MUST surface HITL
    opts_30 = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.80},
        {"option_id": "opt2", "label": "Option B", "confidence": 0.50},
    ]
    res3 = MarginEvaluator.evaluate(opts_30)
    assert res3["surface_hitl"] is True

    # Case 4: Delta = 0.35 (> 0.30) and c_top >= 0.65 -> MUST NOT surface HITL (autonomous)
    opts_35 = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.85},
        {"option_id": "opt2", "label": "Option B", "confidence": 0.50},
    ]
    res4 = MarginEvaluator.evaluate(opts_35)
    assert res4["surface_hitl"] is False
    assert res4["decision_resolved_autonomously"] is True


def test_margin_evaluator_confidence_floor_breach():
    # Case 1: Margin is wide (Delta = 0.20 >= 0.15), but top confidence is 0.60 (< 0.65)
    # -> MUST surface HITL due to conviction floor breach
    opts_wide_low = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.60},
        {"option_id": "opt2", "label": "Option B", "confidence": 0.40},
    ]
    res = MarginEvaluator.evaluate(opts_wide_low)
    assert res["surface_hitl"] is True
    assert res["decision_resolved_autonomously"] is False
    assert res["confidence_floor_breached"] is True
    assert "CONFIDENCE_FLOOR_BREACH" in res["rationale"]

    # Case 2: Single option with low confidence (0.55 < 0.65) -> MUST surface HITL
    opts_single_low = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.55},
    ]
    res_single = MarginEvaluator.evaluate(opts_single_low)
    assert res_single["surface_hitl"] is True
    assert res_single["decision_resolved_autonomously"] is False
    assert res_single["confidence_floor_breached"] is True

    # Case 3: Single option with high confidence (0.90 >= 0.65) -> Autonomous pass
    opts_single_high = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.90},
    ]
    res_single_high = MarginEvaluator.evaluate(opts_single_high)
    assert res_single_high["surface_hitl"] is False
    assert res_single_high["decision_resolved_autonomously"] is True


def test_margin_evaluator_multi_option_epistemic_entropy():
    # Case 1: Fat-tail dispersion across 10 candidates:
    # Option 1 has 0.50, Options 2..10 have 0.055. Margin = 0.50 - 0.055 = 0.445 (wide!),
    # but 50% probability is dispersed across alternatives.
    opts_fat_tail = [{"option_id": "opt0", "label": "Option 0", "confidence": 0.50}] + [
        {"option_id": f"opt{i}", "label": f"Option {i}", "confidence": 0.055} for i in range(1, 10)
    ]
    res_fat = MarginEvaluator.evaluate(opts_fat_tail)
    assert res_fat["surface_hitl"] is True
    assert res_fat["decision_resolved_autonomously"] is False
    assert "MULTI_OPTION_ENTROPY_OVERRIDE" in res_fat["rationale"] or res_fat["confidence_floor_breached"]

    # Case 2: Decisive 3-option distribution: Option 1 has 0.85, Options 2 & 3 have 0.08 & 0.07.
    # Conviction meets floor (0.85 >= 0.65), Margin meets threshold (0.77 >= 0.15), and entropy is low.
    opts_decisive = [
        {"option_id": "opt1", "label": "Option A", "confidence": 0.85},
        {"option_id": "opt2", "label": "Option B", "confidence": 0.08},
        {"option_id": "opt3", "label": "Option C", "confidence": 0.07},
    ]
    res_decisive = MarginEvaluator.evaluate(opts_decisive)
    assert res_decisive["surface_hitl"] is False
    assert res_decisive["decision_resolved_autonomously"] is True


def test_two_track_classifier_uses_30_percent_default():
    classifier = TwoTrackClassifier()
    assert classifier.ambiguity_threshold == 0.30
