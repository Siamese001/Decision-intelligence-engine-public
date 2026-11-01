"""
Adversarial edge-case tests for Wave 1: Document Signal & Value Router.
Covers:
1. Boundary payloads (empty input, whitespace-only, None document text, 0-page metadata)
2. Malformed input (non-numeric max_mutation_budget, corrupted _source_coverage dicts, invalid explicit perspective)
3. Invariant bypass attempts (spoofed planning.apex_globalanalytics.com URL in placeholder/lorem text)
4. Overflow & exhaustion (extreme page counts and massive budget overrides clamped to safe ceiling)
"""
from __future__ import annotations

from typing import Any, Dict

from dual_pipeline.document_pipeline_core.archetype_classifier import DocumentArchetype
from dual_pipeline.document_pipeline_core.signal_value_router import (
    ExtractionMode,
    PerspectiveOrigin,
    SignalTier,
    apply_signal_profile_to_metadata,
    assess_document_signal_profile,
)


def test_boundary_empty_and_whitespace_payloads() -> None:
    for payload in ("", "   \n\t  ", None):
        profile = assess_document_signal_profile(payload)
        assert profile.signal_tier == SignalTier.LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE.value
        assert profile.strategic_value_score == 0.0
        assert profile.page_count == 0
        assert profile.dynamic_mutation_budget == 30
        assert profile.requires_graph_delta_hitl is False
        assert "empty_or_whitespace_document" in profile.signal_reasons


def test_malformed_metadata_fields_handled_gracefully() -> None:
    malformed_meta: Dict[str, Any] = {
        "pages_count": "not-an-int",
        "_source_coverage": "corrupted-coverage-string",
        "max_mutation_budget": "invalid-budget",
        "perspective_origin": "UNRECOGNIZED_PERSPECTIVE_TOKEN",
    }
    profile = apply_signal_profile_to_metadata(
        "Apex Global Analytics internal memo on Q4 workstreams and Est. NAV ($K) $500K.",
        malformed_meta,
    )
    assert profile.page_count == 1
    assert profile.perspective_origin == PerspectiveOrigin.CLIENT_INTERNAL_NEA.value
    assert isinstance(malformed_meta["max_mutation_budget"], int)
    assert malformed_meta["max_mutation_budget"] >= 30


def test_invariant_bypass_spoofed_portal_url_rejected() -> None:
    spoofed_text = (
        "Lorem ipsum placeholder link https://planning.apex_globalanalytics.com/quarterly-planning "
        "example.com test-spoof mock-url with no real workstreams or rosters."
    )
    profile = assess_document_signal_profile(spoofed_text)
    assert profile.archetype != DocumentArchetype.TABULAR_PORTFOLIO_EXPORT.value
    assert profile.signal_tier == SignalTier.LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE.value
    assert profile.requires_graph_delta_hitl is False
    assert "spoof_guard_clamped" in profile.signal_reasons or profile.strategic_value_score < 0.40


def test_overflow_budget_and_extreme_page_counts_clamped() -> None:
    huge_meta: Dict[str, Any] = {
        "pages_count": 50000,
        "max_mutation_budget": 9999999,
    }
    profile = assess_document_signal_profile(
        "Statement of Work with hourly rate and estimated service fees $2,500K.",
        huge_meta,
    )
    assert profile.dynamic_mutation_budget == 2500

    unoverridden_meta: Dict[str, Any] = {"pages_count": 10000}
    profile_auto = assess_document_signal_profile(
        "Statement of Work with hourly rate and estimated service fees $2,500K. " * 20,
        unoverridden_meta,
    )
    assert 90 <= profile_auto.dynamic_mutation_budget <= 600
