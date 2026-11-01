"""Adversarial edge-case tests for Wave 1: Router Scoring Calibration & Pre-Execution Plan Emission."""
from __future__ import annotations

from dual_pipeline.document_pipeline_core.ingestion_planner import build_document_ingestion_plan
from dual_pipeline.document_pipeline_core.signal_value_router import (
    SignalTier,
    assess_document_signal_profile,
    reconstruct_document_text_from_metadata,
)


def test_boundary_empty_and_whitespace_payloads() -> None:
    prof_empty = assess_document_signal_profile("", {})
    assert prof_empty.strategic_value_score == 0.0
    assert prof_empty.dynamic_mutation_budget == 30
    assert prof_empty.page_count == 0

    prof_ws = assess_document_signal_profile("   \n\t  ", {"slide_units": []})
    assert prof_ws.strategic_value_score == 0.0


def test_malformed_slide_units_and_budget_overrides() -> None:
    meta = {
        "slide_units": [None, "invalid", {"unit_number": "bad", "content": "HR360 Tyk Gateway API integration pipeline"}],
        "max_mutation_budget": "not-an-int",
    }
    text = reconstruct_document_text_from_metadata("", meta)
    assert "HR360 Tyk Gateway API" in text
    prof = assess_document_signal_profile("", meta)
    assert prof.page_count >= 1
    assert prof.dynamic_mutation_budget >= 35


def test_spoofed_portal_url_and_overflow_budget_clamping() -> None:
    spoof_text = (
        "https://planning.apex_globalanalytics.com/mock-url example.com lorem ipsum "
        "PM: Fake Lead SM: Fake Scrum"
    )
    prof = assess_document_signal_profile(spoof_text, {})
    assert prof.strategic_value_score <= 0.45
    assert "spoof_guard_clamped" in prof.signal_reasons or prof.signal_tier != SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value

    overflow_prof = assess_document_signal_profile("Valid text", {"max_mutation_budget": 999999})
    assert overflow_prof.dynamic_mutation_budget == 2500


def test_idempotent_plan_generation_on_missing_source_text() -> None:
    meta = {
        "source_units": [
            {"page": 1, "text": "FORGE lifecycle architecture pipeline integration with Apex Global Analytics."},
        ]
    }
    plan1 = build_document_ingestion_plan("", meta)
    plan2 = build_document_ingestion_plan("", meta)
    assert plan1["plan_id"] == plan2["plan_id"]
    assert plan1["source_hash"] == plan2["source_hash"]
    assert plan1["total_pages_detected"] == 1
