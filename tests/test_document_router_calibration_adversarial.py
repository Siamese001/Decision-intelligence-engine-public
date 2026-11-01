"""Adversarial edge-case tests for Wave 5 Document Signal Router calibration and routing gates."""
from __future__ import annotations

from dual_pipeline.document_pipeline_core.document_signal_router import (
    DocumentArchetype,
    apply_document_signal_budget_to_candidates,
    evaluate_document_signal_routing,
)
from dual_pipeline.intake_core.routing import _route_for_candidate


def test_boilerplate_collateral_produces_zero_promoted_graph_mutations() -> None:
    """Invariant bypass attempt: BOILERPLATE_COLLATERAL must force 0 promoted mutations and SOURCE_ONLY."""
    decision = evaluate_document_signal_routing(
        "## Corporate Overview\nAbout PartnerAdvisory corporate brochure and standard disclaimer.",
        title="Corporate Brochure",
    )
    assert decision.archetype == DocumentArchetype.BOILERPLATE_COLLATERAL
    assert decision.max_promoted_mutations == 0

    candidates = [
        {
            "candidate_id": "cand-01",
            "candidate_type": "assertion",
            "materiality": "high",
            "source_locator": {"kind": "section", "value": "## Corporate Overview"},
        }
    ]
    calibrated = apply_document_signal_budget_to_candidates(candidates, decision)
    assert calibrated[0]["document_signal_disposition"] == "SOURCE_ONLY"
    route, _ = _route_for_candidate(calibrated[0], [], {"exact_matches": [], "partial_overlaps": []})
    assert route == "SOURCE_ONLY"


def test_empty_and_minimal_document_payloads_handle_gracefully() -> None:
    """Boundary payload: Empty or 1-word document text does not divide-by-zero and returns bounded policy."""
    decision = evaluate_document_signal_routing("", title="", metadata={"document_archetype": "WORKING_NOTES_BRAINSTORM"})
    assert decision.archetype == DocumentArchetype.WORKING_NOTES_BRAINSTORM
    assert decision.word_count >= 1
    assert len(decision.sections) == 1


def test_explicit_metadata_archetype_override_and_unknown_fallback() -> None:
    """Malformed input: Invalid archetype strings fall back cleanly to structural heuristic."""
    decision = evaluate_document_signal_routing(
        "## Weekly Status Report\nSprint status update.",
        title="Status",
        metadata={"document_archetype": "INVALID_UNKNOWN_TYPE"},
    )
    assert decision.archetype == DocumentArchetype.PROJECT_STATUS_SNAPSHOT
