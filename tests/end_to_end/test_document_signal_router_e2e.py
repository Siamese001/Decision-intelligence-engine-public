"""End-to-end verification of Wave 5 Two-Stage Hybrid 7-Archetype Document Signal Router (DEC-INGEST-01)."""
from __future__ import annotations

from dual_pipeline.document_pipeline_core.document_signal_router import (
    ARCHETYPE_POLICIES,
    DocumentArchetype,
    apply_document_signal_budget_to_candidates,
    evaluate_document_signal_routing,
)
from dual_pipeline.project_status import build_snapshot_profile


def test_seven_archetypes_stage1_fingerprinting_and_policies_e2e() -> None:
    """Verifies Stage 1 structural/metadata fingerprinting across all 7 document archetypes."""
    cases = [
        (
            "Master Services Agreement & Statement of Work",
            "## Scope of Work\nDeliverable 1: Lakehouse migration cutover SLA by Q4.",
            DocumentArchetype.SOW_CONTRACT,
            9,
            True,
        ),
        (
            "Target Architecture Design Specification",
            "## System Design Spec\nTarget state architecture for streaming ingestion.",
            DocumentArchetype.ARCH_DESIGN_SPEC,
            8,
            True,
        ),
        (
            "Q4 Executive Steering Committee Readout",
            "## Executive Briefing\nStrategic roadmap and budget commitment.",
            DocumentArchetype.EXEC_STEERING_DECK,
            7,
            True,
        ),
        (
            "Platform CLI & SDK API Reference Manual",
            "## Configuration Guide\nCommand line flags and endpoint parameters.",
            DocumentArchetype.TECH_REFERENCE_DOC,
            6,
            False,
        ),
        (
            "Weekly Status Report - Sprint 14 RAG Status",
            "## Weekly Snapshot\nGreen status on ingestion pipeline.",
            DocumentArchetype.PROJECT_STATUS_SNAPSHOT,
            5,
            False,
        ),
        (
            "Whiteboard Brainstorm & Rough Working Notes",
            "## Ideation Session\nExploring possible future caching ideas.",
            DocumentArchetype.WORKING_NOTES_BRAINSTORM,
            3,
            False,
        ),
        (
            "Standard Corporate Brochure & Marketing Collateral",
            "## About PartnerAdvisory\nAll rights reserved. Confidentiality notice and disclaimer.",
            DocumentArchetype.BOILERPLATE_COLLATERAL,
            1,
            False,
        ),
    ]

    for title, body, expected_arch, expected_rank, expected_auto_approve in cases:
        decision = evaluate_document_signal_routing(body, title=title)
        assert decision.archetype == expected_arch
        assert decision.policy.authority_rank == expected_rank
        assert decision.policy.auto_approve_eligible is expected_auto_approve


def test_stage2_mixed_sow_separates_binding_clauses_from_boilerplate_e2e() -> None:
    """Verifies Stage 2 C0 section scoring separates binding SOW clauses from legal boilerplate in the same doc."""
    mixed_sow_text = (
        "# Statement of Work: Enterprise Data Platform\n\n"
        "## Scope of Work and Deliverables\n"
        "The team will deliver the production Lakehouse architecture and cutover SLA commitment "
        "with accountable owner Speaker_Alpha Caldwell and target timeline Q4.\n\n"
        "## Confidentiality Notice and Governing Law\n"
        "All rights reserved. Indemnification, force majeure, and terms and conditions apply.\n"
    )
    decision = evaluate_document_signal_routing(mixed_sow_text, title="Statement of Work")
    assert decision.archetype == DocumentArchetype.SOW_CONTRACT
    sec_by_heading = {s.section_heading: s for s in decision.sections}
    assert sec_by_heading["## Scope of Work and Deliverables"].is_boilerplate is False
    assert sec_by_heading["## Scope of Work and Deliverables"].recommended_lane == "APPROVED"
    assert sec_by_heading["## Confidentiality Notice and Governing Law"].is_boilerplate is True
    assert sec_by_heading["## Confidentiality Notice and Governing Law"].recommended_lane == "SOURCE_ONLY"

    candidates = [
        {
            "candidate_id": "cand-binding-01",
            "candidate_type": "architecture_relationship",
            "materiality": "high",
            "source_locator": {"kind": "section", "value": "## Scope of Work and Deliverables"},
        },
        {
            "candidate_id": "cand-bp-01",
            "candidate_type": "assertion",
            "materiality": "medium",
            "source_locator": {"kind": "section", "value": "## Confidentiality Notice and Governing Law"},
        },
    ]
    calibrated = apply_document_signal_budget_to_candidates(candidates, decision)
    assert calibrated[0]["document_signal_disposition"] == "APPROVED"
    assert calibrated[1]["document_signal_disposition"] == "SOURCE_ONLY"
    assert calibrated[1]["materiality"] == "low"


def test_project_status_snapshot_temporal_decay_and_tech_reference_inverted_c17_e2e() -> None:
    """Verifies PROJECT_STATUS_SNAPSHOT 14-day decay policy and TECH_REFERENCE_DOC inverted C-17 cap."""
    snap_policy = ARCHETYPE_POLICIES[DocumentArchetype.PROJECT_STATUS_SNAPSHOT]
    assert snap_policy.stale_after_days == 14
    assert snap_policy.fact_promotion_eligible is False
    snap_profile = build_snapshot_profile(scope_id="workstream-lakehouse", as_of_date="2026-10-25")
    assert snap_profile["fact_promotion_eligible"] is False

    ref_text = "## API Reference Manual\n" + ("Endpoint parameter specification word. " * 200)
    ref_decision = evaluate_document_signal_routing(ref_text, title="SDK API Reference Manual")
    assert ref_decision.archetype == DocumentArchetype.TECH_REFERENCE_DOC
    assert ref_decision.max_promoted_mutations == 3

    ref_candidates = [
        {"candidate_id": f"c-{i}", "candidate_type": "architecture_component", "materiality": "high", "source_locator": {"kind": "section", "value": "## API Reference Manual"}}
        for i in range(6)
    ]
    calibrated = apply_document_signal_budget_to_candidates(ref_candidates, ref_decision)
    promoted = [c for c in calibrated if c["document_signal_disposition"] != "EVIDENCE_ONLY"]
    evidence_only = [c for c in calibrated if c["document_signal_disposition"] == "EVIDENCE_ONLY"]
    assert len(promoted) == 3
    assert len(evidence_only) == 3
