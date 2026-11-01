"""Unit tests for Evidence Envelope Resolver and Fail-Closed Validator."""


import pytest

from dual_pipeline.graph_constrained_projection.evidence_envelope import EvidenceEnvelopeResolver
from dual_pipeline.graph_constrained_projection.graph_spine import GraphSpineResolver
from dual_pipeline.graph_constrained_projection.models import ProjectionRow
from dual_pipeline.graph_constrained_projection.validator import EntityAffiliationValidator


def test_evidence_envelope_indexing():
    """Verify that evidence envelopes are indexed from project deliverables."""
    resolver = EvidenceEnvelopeResolver(project_dir="output/project_lens")
    # Verify that either canonical projection JSONs or markdown transcripts were indexed
    assert resolver.total_spans > 0, "Should have indexed evidence spans from project_lens"

    # Search for known keyword
    spans = resolver.search_by_keyword("turbine", limit=3)
    assert len(spans) > 0, "Should find evidence mentions of turbine"


def test_validator_detects_and_blocks_jake_as_client_lead():
    """Fail-closed test: simulate the exact historical hallucination and verify it is BLOCKED."""
    spine = GraphSpineResolver()
    validator = EntityAffiliationValidator(spine)

    hallucinated_row = ProjectionRow(
        dimension="Commercial & Milestone Governance",
        sub_dimension="Scope Boundary & Extension Discipline",
        standard="Enforce strict SOW boundary control and condition all follow-on expansion on formal acceptance.",
        field_reality="Circulated Phase 2 SOW proposals while Phase 1 extraction accuracy was actively unresolved.",
        impact="Frustrated the client lead (Jake), who felt discussions regarding follow-on work were premature while Phase 1 remained unfinished.",
        coaching="Freeze all follow-on commercial pitches until current deliverables achieve formal sign-off.",
    )

    report = validator.validate_matrix([hallucinated_row])
    assert report.passed is False, "Validator MUST fail closed when Jake (PartnerAdvisory) is labeled as client lead"
    assert len(report.violations) == 1
    v = report.violations[0]
    assert v.violation_type == "CROSS_ORG_AFFILIATION_COLLISION"
    assert "Jake Stark" in v.entity_name
    assert v.expected_org == "PartnerAdvisory"
    assert v.severity == "FATAL"

    with pytest.raises(AssertionError) as exc_info:
        validator.assert_valid(report)
    assert "CROSS_ORG_AFFILIATION_COLLISION" in str(exc_info.value)


def test_validator_approves_correct_mandy_hopmann_attribution():
    """Verify that correctly attributing client leadership to harper Hoffman passes validation."""
    spine = GraphSpineResolver()
    validator = EntityAffiliationValidator(spine)

    correct_row = ProjectionRow(
        dimension="Commercial & Milestone Governance",
        sub_dimension="Scope Boundary & Extension Discipline",
        standard="Enforce strict SOW boundary control and condition all follow-on expansion on formal acceptance.",
        field_reality="Circulated Phase 2 SOW proposals while Phase 1 extraction accuracy was actively unresolved.",
        impact="Frustrated the client lead (harper Hoffman), who felt discussions regarding follow-on work were premature while Phase 1 remained unfinished.",
        coaching="Freeze all follow-on commercial pitches until current deliverables achieve formal sign-off.",
    )

    report = validator.validate_matrix([correct_row])
    assert report.passed is True, "Validator MUST pass when harper Hoffman is correctly identified as client lead"
    assert len(report.violations) == 0

    # assert_valid should not raise
    validator.assert_valid(report)


def test_validator_blocks_client_as_delivery_lead():
    """Verify that labeling a client stakeholder as PartnerAdvisory delivery lead is also blocked."""
    spine = GraphSpineResolver()
    validator = EntityAffiliationValidator(spine)

    inverted_row = ProjectionRow(
        dimension="Commercial & Milestone Governance",
        sub_dimension="Contractual Milestone Defense",
        standard="Enforce contractual acceptance gates before release.",
        field_reality="Delivery lead (harper Hoffman) failed to enforce milestones.",
        impact="Client was dissatisfied with progress.",
        coaching="Manage to signed metrics.",
    )

    report = validator.validate_matrix([inverted_row])
    assert report.passed is False
    assert len(report.violations) == 1
    assert "harper Hoffman" in report.violations[0].entity_name
    assert report.violations[0].expected_org == "Apex Global Enterprises"


def test_bound_oversized_evidence_statements():
    """Verify that evidence statements exceeding 3500/4000 characters are losslessly bounded."""
    from dual_pipeline.transcript_stages_core.graph_post_evidence_healer import finalize_evidence_citations
    from dual_pipeline.evidence_field_limits import validate_projection_field_lengths

    para1 = "Paragraph 1: Apex Global Enterprises operates a fleet of wind turbines. " * 30
    para2 = "Paragraph 2: The LENS system automates nameplate visual asset collection. " * 35
    full_text = f"{para1}\n\n{para2}"
    assert len(full_text) > 4000, f"Setup error: full_text len is {len(full_text)}"

    data = {
        "title": "Document Title",
        "evidence_index": {
            "ev-01": {
                "speaker": "Author",
                "source_classification": "FACT",
                "kind": "technical",
                "text": full_text,
                "exact_text": full_text,
                "locator": {"kind": "section", "value": "p1"},
            }
        },
        "evidence_only_statement_ids": [],
    }
    mutations = {
        "entity_upserts": [
            {
                "id": "org:apex_global_energy",
                "type": "org",
                "name": "Apex Global Enterprises",
                "mutation": "CREATE",
                "evidence_ids": ["ev-01"],
            }
        ]
    }
    finalize_evidence_citations(data, mutations)

    # Validate that every evidence item in evidence_index is <= 3500
    for eid, ev in data["evidence_index"].items():
        assert len(ev.get("text", "")) <= 3500
        assert len(ev.get("exact_text", "")) <= 3500

    # Ensure field length validation passes without ValueError
    validate_projection_field_lengths(data)

    # Ensure citations are still valid
    upsert = mutations["entity_upserts"][0]
    assert len(upsert["evidence_ids"]) == 1
    assert upsert["evidence_ids"][0] in data["evidence_index"]

