"""Tests for Project LENS evaluation data contracts and specification."""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.lens_evaluation.models import (
    ModelExtraction,
    PreFlightQualityResult,
    TurbinePhotoRecord,
)
from dual_pipeline.lens_evaluation.thresholds import (
    PROPOSED_RELAXED,
    STRICT_BASELINE,
)


def test_specification_exists_and_declares_denominators() -> None:
    spec_path = Path("governance/lens/LENS_SINGLE_TURBINE_EVALUATION_SPEC.md")
    assert spec_path.exists(), "Evaluation specification must exist"
    content = spec_path.read_text()
    assert "N_{\\text{total}}" in content or "N_{total}" in content
    assert "N_{\\text{defect}}" in content or "N_{defect}" in content
    assert "N_{\\text{legible}}" in content or "N_{legible}" in content
    assert "N_{\\text{auto}}" in content or "N_{auto}" in content
    assert "N_{\\text{review}}" in content or "N_{review}" in content
    assert "correct" in content and "auto" in content
    assert "ERR_BLUR" in content
    assert "ERR_LIGHTING" in content
    assert "ERR_NO_TEXT" in content


def test_preflight_quality_result_serialization() -> None:
    res = PreFlightQualityResult(
        is_legible=False,
        sharpness_score=45.2,
        luminance_score=110.0,
        has_text_tokens=True,
        defect_code="ERR_BLUR",
    )
    d = res.to_dict()
    assert d["is_legible"] is False
    assert d["defect_code"] == "ERR_BLUR"
    assert d["sharpness_score"] == 45.2


def test_turbine_photo_record_roundtrip() -> None:
    model_a = ModelExtraction(
        manufacturer="HIRSCHMANN",
        model_number="RS20-0800T1T1T0AUMM",
        serial_number="943434001",
        confidence=0.92,
        bounding_box_area=0.85,
    )
    model_b = ModelExtraction(
        manufacturer="HIRSCHMANN",
        model_number="RS20-0800T1T1TDAU??",
        serial_number="943434001",
        confidence=0.89,
        bounding_box_area=0.80,
    )
    rec = TurbinePhotoRecord(
        photo_id="photo_001",
        turbine_id="turbine_wtg_04",
        component_type="network_switch",
        mean_luminance=120.5,
        laplacian_variance=145.2,
        detected_token_count=18,
        model_a=model_a,
        model_b=model_b,
        ground_truth_manufacturer="HIRSCHMANN",
        ground_truth_model_number="RS20-0800T1T1T0AUMM",
        ground_truth_serial_number="943434001",
        has_factory_serial=True,
    )
    d = rec.to_dict()
    assert d["photo_id"] == "photo_001"
    reconstructed = TurbinePhotoRecord.from_dict(d)
    assert reconstructed.photo_id == rec.photo_id
    assert reconstructed.model_a.manufacturer == "HIRSCHMANN"
    assert reconstructed.model_b.confidence == 0.89


def test_threshold_configurations() -> None:
    assert STRICT_BASELINE.name == "STRICT_BASELINE"
    assert STRICT_BASELINE.require_exact_manufacturer is True
    assert STRICT_BASELINE.min_prefix_match_len > 100
    assert STRICT_BASELINE.min_serial_levenshtein == 1.0

    assert PROPOSED_RELAXED.name == "PROPOSED_RELAXED"
    assert PROPOSED_RELAXED.use_bounding_box_dominance is True
    assert PROPOSED_RELAXED.min_prefix_match_len == 8
    assert PROPOSED_RELAXED.min_serial_levenshtein == 0.80
    assert PROPOSED_RELAXED.allow_zero_serial_exception is True
    assert PROPOSED_RELAXED.stp_confidence_threshold < STRICT_BASELINE.stp_confidence_threshold
