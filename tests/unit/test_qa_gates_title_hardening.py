"""Unit tests for Wave 3: QA Gate and Judge Rubric Hardening for Title Resolution."""

from dual_pipeline.transcript_qa_core.gate1_alr import _check_hitl_receipt_binding
from dual_pipeline.transcript_qa_core.gate1_attribution import _check_attendee_title_completeness
from dual_pipeline.transcript_qa_core.gate2_titles import check_gate_2_12_attendee_titles
from dual_pipeline.transcript_qa_core.gate3_roles import (
    lint_member_of_consistency,
    lint_person_descriptions,
)


def test_gate1_2a_fails_on_placeholder_string():
    f1_bad = "# Output 01\nAttendees:\n- Daniel Brankin (specific title not provided)\nTurn 1: Hello"
    failures = _check_attendee_title_completeness(f1_bad, {})
    assert len(failures) > 0
    assert "UNRESOLVED_ATTENDEE_TITLE" in failures[0]


def test_gate1_2a_passes_on_resolved_titles():
    f1_good = "# Output 01\nAttendees:\n- Daniel Brankin (Director of Digital Technology)\nTurn 1: Hello"
    failures = _check_attendee_title_completeness(f1_good, {})
    assert len(failures) == 0


def test_gate1_7a_fails_when_unresolved_placeholder_lacks_receipt():
    meta = {
        "pre_enrichment_roles": {
            "Daniel Brankin": "APEX",
            "Taylor Mercer": "Delivery Lead / PM",
        }
    }
    failures = _check_hitl_receipt_binding(meta, ["Daniel Brankin", "Taylor Mercer"])
    assert len(failures) > 0
    assert "AMBIGUITY_LOCK_REGISTER_NOT_HITL_BACKED" in failures[0]
    assert "Daniel Brankin" in failures[0]


def test_gate1_7a_passes_when_receipt_recorded():
    meta = {
        "pre_enrichment_roles": {
            "Daniel Brankin": "APEX",
            "Taylor Mercer": "Delivery Lead / PM",
        },
        "hitl_role_receipts": {
            "Daniel Brankin": {
                "decision_type": "ROLE_CLARIFICATION",
                "resolved_role": "Director of Digital Technology",
                "hitl_receipt_bound": True,
            }
        },
    }
    failures = _check_hitl_receipt_binding(meta, ["Daniel Brankin", "Taylor Mercer"])
    assert len(failures) == 0


def test_gate2_12_fails_on_header_or_appendix_placeholders():
    f2_bad_header = """# Executive Recap
Date: 09/28/2026
Attendees:
- Apex Global Enterprises (NEA): Daniel Brankin (APEX; specific title not provided)

## Appendix
### Daniel Brankin — Apex Global Enterprises (NEA), APEX Leadership
Notes here.
"""
    passed, failures, details = check_gate_2_12_attendee_titles(f2_bad_header)
    assert not passed
    assert any("GATE_2_12" in f for f in failures)


def test_gate2_12_passes_on_clean_titles():
    f2_good = """# Executive Recap
Date: 09/28/2026
Attendees:
- Apex Global Enterprises (NEA): Daniel Brankin (Director of Digital Technology)
- PartnerAdvisory: Taylor Mercer (Delivery Lead / PM)

## Appendix
### Daniel Brankin — Apex Global Enterprises (NEA), Director of Digital Technology
Notes here.
"""
    passed, failures, details = check_gate_2_12_attendee_titles(f2_good)
    assert passed
    assert len(failures) == 0


def test_gate3_11_fails_on_generic_person_description():
    entities = [
        {"id": "person:daniel_brankin", "name": "Daniel Brankin", "description": "Review participant."},
    ]
    passed, failures, details = lint_person_descriptions(entities)
    assert not passed
    assert len(failures) == 1
    assert "Gate 3.11" in failures[0]


def test_gate3_11_passes_on_substantive_description():
    entities = [
        {"id": "person:daniel_brankin", "name": "Daniel Brankin", "description": "Director of Digital Technology at APEX."},
    ]
    passed, failures, details = lint_person_descriptions(entities)
    assert passed
    assert len(failures) == 0


def test_gate3_12_fails_on_cross_org_contradiction():
    relationships = [
        {
            "relationship_id": "rel:db_fpl",
            "source_node_id": "person:daniel_brankin",
            "predicate": "MEMBER_OF",
            "target_node_id": "org:apex",
            "description": "PartnerAdvisory team member.",
        }
    ]
    passed, failures, details = lint_member_of_consistency(relationships)
    assert not passed
    assert len(failures) == 1
    assert "Gate 3.12" in failures[0]


def test_gate3_12_passes_on_consistent_description():
    relationships = [
        {
            "relationship_id": "rel:db_fpl",
            "source_node_id": "person:daniel_brankin",
            "predicate": "MEMBER_OF",
            "target_node_id": "org:apex",
            "description": "APEX leadership team member.",
        }
    ]
    passed, failures, details = lint_member_of_consistency(relationships)
    assert passed
    assert len(failures) == 0
