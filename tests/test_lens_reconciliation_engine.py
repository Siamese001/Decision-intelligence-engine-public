"""Tests for Project LENS Pre-Flight Quality Gate and Dual-Model Reconciliation Engine."""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.lens_evaluation.evaluator import SingleTurbineEvaluator
from dual_pipeline.lens_evaluation.models import (
    ModelExtraction,
    TurbinePhotoRecord,
)
from dual_pipeline.lens_evaluation.preflight import PreFlightQualityGate
from dual_pipeline.lens_evaluation.reconciler import (
    DualModelReconciler,
    common_prefix_length,
    string_similarity,
)
from dual_pipeline.lens_evaluation.thresholds import (
    PROPOSED_RELAXED,
    STRICT_BASELINE,
)


def test_string_similarity_and_prefix() -> None:
    assert string_similarity("943434001", "943434001") == 1.0
    assert string_similarity("943434001", "94343400O") > 0.85
    assert string_similarity("ABC", "XYZ") == 0.0

    assert common_prefix_length("RS20-0800T1T1T0AUMM", "RS20-0800T1T1TDAU??") >= 13
    assert common_prefix_length("IS200TRPAH1A", "IS200TRPAH1B") == 11


def test_preflight_gate_quarantines_capture_defects() -> None:
    base_rec = TurbinePhotoRecord(
        photo_id="test_01",
        turbine_id="WTG_04",
        component_type="network_switch",
        mean_luminance=120.0,
        laplacian_variance=150.0,
        detected_token_count=15,
        model_a=ModelExtraction("HIRSCHMANN", "RS20", "123", 0.9),
        model_b=ModelExtraction("HIRSCHMANN", "RS20", "123", 0.9),
        ground_truth_manufacturer="HIRSCHMANN",
        ground_truth_model_number="RS20",
        ground_truth_serial_number="123",
    )

    # 1. Blur defect
    blur_rec = TurbinePhotoRecord.from_dict({**base_rec.to_dict(), "laplacian_variance": 50.0})
    res_blur = PreFlightQualityGate.evaluate(blur_rec, PROPOSED_RELAXED)
    assert res_blur.is_legible is False
    assert res_blur.defect_code == "ERR_BLUR"

    # 2. Lighting defect - Dark
    dark_rec = TurbinePhotoRecord.from_dict({**base_rec.to_dict(), "mean_luminance": 20.0})
    res_dark = PreFlightQualityGate.evaluate(dark_rec, PROPOSED_RELAXED)
    assert res_dark.is_legible is False
    assert res_dark.defect_code == "ERR_LIGHTING"

    # 3. Lighting defect - Glare
    glare_rec = TurbinePhotoRecord.from_dict({**base_rec.to_dict(), "mean_luminance": 235.0})
    res_glare = PreFlightQualityGate.evaluate(glare_rec, PROPOSED_RELAXED)
    assert res_glare.is_legible is False
    assert res_glare.defect_code == "ERR_LIGHTING"

    # 4. Zero tokens
    blank_rec = TurbinePhotoRecord.from_dict({**base_rec.to_dict(), "detected_token_count": 0})
    res_blank = PreFlightQualityGate.evaluate(blank_rec, PROPOSED_RELAXED)
    assert res_blank.is_legible is False
    assert res_blank.defect_code == "ERR_NO_TEXT"

    # 5. Legible
    res_legible = PreFlightQualityGate.evaluate(base_rec, PROPOSED_RELAXED)
    assert res_legible.is_legible is True
    assert res_legible.defect_code is None


def test_reconciler_strict_vs_relaxed_behavior() -> None:
    # Bounding box dominance case
    rec_bbox = TurbinePhotoRecord(
        photo_id="test_bbox",
        turbine_id="WTG_04",
        component_type="network_switch",
        mean_luminance=120.0,
        laplacian_variance=150.0,
        detected_token_count=15,
        model_a=ModelExtraction("HIRSCHMANN", "RS20-0800", "943434001", 0.92, bounding_box_area=0.88),
        model_b=ModelExtraction("BUSSMANN", "RS20-0800", "943434001", 0.80, bounding_box_area=0.12),
        ground_truth_manufacturer="HIRSCHMANN",
        ground_truth_model_number="RS20-0800",
        ground_truth_serial_number="943434001",
    )

    outcome_strict = DualModelReconciler.reconcile(rec_bbox, STRICT_BASELINE)
    assert outcome_strict.is_auto_accepted is False
    assert outcome_strict.routing_tier != "ZONE_5A_STP"

    outcome_relaxed = DualModelReconciler.reconcile(rec_bbox, PROPOSED_RELAXED)
    assert outcome_relaxed.is_auto_accepted is True
    assert outcome_relaxed.resolved_manufacturer == "HIRSCHMANN"
    assert outcome_relaxed.is_correct is True


def test_evaluator_batch_mathematical_invariants() -> None:
    fixture_path = Path("tests/fixtures/lens/single_turbine_200_photos.json")
    assert fixture_path.exists(), "Turbine fixture must exist"

    with open(fixture_path) as f:
        data = json.load(f)

    records = [TurbinePhotoRecord.from_dict(d) for d in data]
    assert len(records) == 200

    # Evaluate Strict Baseline
    metrics_strict, outcomes_strict = SingleTurbineEvaluator.evaluate_batch(records, STRICT_BASELINE)
    assert metrics_strict.total_photos == 200
    assert metrics_strict.capture_defects == 20
    assert metrics_strict.legible_photos == 180
    assert metrics_strict.total_photos == metrics_strict.capture_defects + metrics_strict.legible_photos
    assert metrics_strict.legible_photos == metrics_strict.auto_accepted + metrics_strict.human_review
    assert metrics_strict.auto_accepted == metrics_strict.correct_auto_accepted + metrics_strict.false_acceptances
    assert metrics_strict.stp_rate < 40.0, "Strict baseline STP must be conservative (~30%)"

    # Evaluate Proposed Relaxed
    metrics_relaxed, outcomes_relaxed = SingleTurbineEvaluator.evaluate_batch(records, PROPOSED_RELAXED)
    assert metrics_relaxed.total_photos == 200
    assert metrics_relaxed.capture_defects == 20
    assert metrics_relaxed.legible_photos == 180
    assert metrics_relaxed.total_photos == metrics_relaxed.capture_defects + metrics_relaxed.legible_photos
    assert metrics_relaxed.legible_photos == metrics_relaxed.auto_accepted + metrics_relaxed.human_review
    assert metrics_relaxed.auto_accepted == metrics_relaxed.correct_auto_accepted + metrics_relaxed.false_acceptances

    # Relaxed must substantially increase automatic acceptance
    assert metrics_relaxed.auto_accepted > metrics_strict.auto_accepted
    assert metrics_relaxed.stp_rate > 70.0, "Relaxed STP should meet target >= 70%"
    assert metrics_relaxed.human_review < metrics_strict.human_review

    # Error trade-off verification
    assert len(metrics_relaxed.error_samples) > 0, "Relaxed must capture realistic error trade-offs"
    for err in metrics_relaxed.error_samples:
        assert err.photo_id.startswith("WTG04_")
        assert err.field_name in ("manufacturer", "model_number", "serial_number")
