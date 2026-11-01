"""
tests/adversarial/test_hitl_title_adversarial.py

Adversarial Stress Test Suite for HITL Title Resolution.
Covers all 18 attack vectors identified across Trigger Bypass (TB), Propagation Leak (PL),
and Gate Evasion (GE) surfaces.
"""

import pytest

from dual_pipeline.transcript_pipeline_core.context_builder import load_prior_meeting_context
from dual_pipeline.transcript_pipeline_core.stakeholder_validator import (
    StakeholderResolutionValidator,
)
from dual_pipeline.transcript_qa_core.gate1_alr import _check_hitl_receipt_binding
from dual_pipeline.transcript_qa_core.gate1_attribution import _check_attendee_title_completeness
from dual_pipeline.transcript_qa_core.gate2_titles import check_gate_2_12_attendee_titles
from dual_pipeline.transcript_qa_core.gate3_roles import (
    lint_member_of_consistency,
    lint_person_descriptions,
)
from dual_pipeline.transcript_qa_core.semantic_linters import lint_predicate_domain
from dual_pipeline.transcript_stages_core.c0_gates import _is_placeholder
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError

# ==============================================================================
# 1. TRIGGER BYPASS (TB-1 through TB-7)
# ==============================================================================

def test_tb1_dead_code_acronym_fix():
    """TB-1: Short all-caps acronyms must not bypass _is_placeholder."""
    for acronym in ("PGD", "APEXS", "NEA", "APEX", "SLM", "NEP"):
        assert _is_placeholder(acronym) is True


def test_tb2_org_tokens_with_suffixes():
    """TB-2: Org tokens with corporate suffixes must be caught as placeholders."""
    for org_str in ("APEX Group", "Apex Global Inc.", "PartnerAdvisory Consulting", "Apex Global Enterprises, LLC"):
        assert _is_placeholder(org_str) is True


def test_tb3_multi_word_meaningless_titles():
    """TB-3: Multi-word generic nouns must not pass as professional titles."""
    for generic in ("Senior Participant", "Executive Attendee", "Team Lead Contributor", "Project Member"):
        assert _is_placeholder(generic) is True


def test_tb4_embedded_invalidator_keywords():
    """TB-4: Trailing or embedded invalidators must trigger placeholder detection."""
    for inv in ("Project Role TBD", "Director (pending confirmation)", "Role Unknown", "Specific title not provided"):
        assert _is_placeholder(inv) is True


def test_tb5_hybrid_org_role_groupings():
    """TB-5: Generic organizational grouping nouns must be flagged."""
    for hybrid in ("NEA Leadership", "Client Stakeholder", "APEX Management", "Business Stakeholder"):
        assert _is_placeholder(hybrid) is True


def test_tb6_unicode_and_punctuation_tricks():
    """TB-6: Zero-width spaces, non-breaking spaces, and trailing punctuation must not bypass matching."""
    for sneaky in ("Participant\u200b", "Attendee\u00a0", "TBD.", "Unknown...", "Participant:"):
        assert _is_placeholder(sneaky) is True


def test_tb7_directory_priors_contain_true_titles():
    """TB-7: Directory priors must be true professional titles, not Contributor/Stakeholder."""
    from dual_pipeline.transcript_metadata_core.entity_resolver import (
        _CANONICAL_ENTERPRISE_DIRECTORY,
    )
    for _key, (canon_name, _org, role) in _CANONICAL_ENTERPRISE_DIRECTORY.items():
        assert not _is_placeholder(role), f"Directory prior for '{canon_name}' has placeholder role '{role}'"


# ==============================================================================
# 2. PROPAGATION LEAK (PL-1 through PL-5)
# ==============================================================================

def test_pl1_c05_evaluates_raw_roles_before_enrichment():
    """PL-1: C0.5 must inspect raw pre_enrichment_roles so stale directory priors cannot auto-resolve."""
    l0_ctx = {
        "metadata": {
            "roster": [{"name": "Unknown Tester", "role": "APEX", "org": "Apex Global Enterprises (NEA)"}],
        }
    }
    gate = C05TitleCompletenessGate()
    with pytest.raises(HITLRequiredError) as exc:
        gate.execute(l0_ctx)
    assert "Unknown Tester" in exc.value.prompt
    assert exc.value.hitl_type == "role_clarification"


def test_pl2_prior_context_contamination_suppression(tmp_path):
    """PL-2: Prior context loader must skip cached output files when invalidate_prior_outputs is set."""
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    (out_dir / "01_sample_hitl_resolved_transcript.md").write_text("prior f1 with placeholders", encoding="utf-8")
    (out_dir / "02_sample_executive_recap.md").write_text("prior f2 with placeholders", encoding="utf-8")

    meta = {"meeting_name": "sample", "invalidate_prior_outputs": True}
    load_prior_meeting_context(str(out_dir), meta)
    assert "prior_forensic_transcript" not in meta
    assert "prior_executive_recap" not in meta


def test_pl3_has_role_predicate_canonical_conformance():
    """PL-3: HAS_ROLE must be accepted by ontology predicate linters without error."""
    sig = [{"signal_id": "rel-1", "relationship": "HAS_ROLE", "from_id": "person:p1", "to_id": "role:r1"}]
    passed, violations, _ = lint_predicate_domain(sig)
    assert passed
    assert len(violations) == 0


def test_pl4_stakeholder_validator_hitl_receipt_bypass(tmp_path):
    """PL-4: SSOT divergence must not crash pipeline when C0.5 HITL receipt authorizes the role."""
    validator = StakeholderResolutionValidator(repo_root=tmp_path)
    o1 = "Attendees:\n- Amit Ayer (Product Marketing Director)"
    o2 = "Attendees:\n- Amit Ayer (Product Marketing Director)"
    o3 = {"entity_upserts": [{"id": "person:amit_ayer", "name": "Amit Ayer", "role": "Product Marketing Director"}]}

    meta = {
        "hitl_role_receipts": {
            "Amit Ayer": {
                "decision_type": "ROLE_CLARIFICATION",
                "resolved_role": "Product Marketing Director",
                "hitl_receipt_bound": True,
            }
        }
    }
    report = validator.validate_deliverables(o1, o2, o3, fail_closed=False, metadata=meta)
    assert report.is_consistent
    assert len(report.discrepancies) == 0


def test_pl5_appendix_header_evasion_blocked():
    """PL-5: Generic summary roles in Output 02 Appendix headers must fail Gate 2.12."""
    f2_text = """# Executive Recap
Date: 09/28/2026
Attendees:
- Apex Global Enterprises (NEA): Daniel Brankin (Director of Digital Technology)

## Appendix
### Daniel Brankin — Apex Global Enterprises (NEA), APEX Leadership
Notes.
"""
    passed, failures, _ = check_gate_2_12_attendee_titles(f2_text)
    assert not passed
    assert any("GATE_2_12_APPENDIX_PLACEHOLDER_TITLE" in f for f in failures)


# ==============================================================================
# 3. GATE EVASION (GE-1 through GE-6)
# ==============================================================================

def test_ge1_pseudo_titles_blocked_by_gate_1_2a():
    """GE-1: Unresolved placeholder strings in Output 01 must fail Gate 1.2a."""
    f1 = "# Output 01\nAttendees:\n- Sharon Diaram (specific title not provided)\nTurn 1: Hi"
    failures = _check_attendee_title_completeness(f1, {})
    assert len(failures) > 0


def test_ge2_unbacked_placeholder_fails_gate_1_7a():
    """GE-2: Placeholder role without authentic receipt must fail Gate 1.7a."""
    meta = {"pre_enrichment_roles": {"Sharon Diaram": "APEX"}}
    failures = _check_hitl_receipt_binding(meta, ["Sharon Diaram"])
    assert len(failures) > 0


def test_ge3_section_scope_gap_covered_by_gate_2_12():
    """GE-3: Valid title in header but placeholder in Appendix must be caught by Gate 2.12."""
    f2 = """# Executive Recap
Attendees:
- PartnerAdvisory: Taylor Mercer (Delivery Lead / PM)

## Appendix
### Taylor Mercer — PartnerAdvisory, Team Member
Notes.
"""
    passed, failures, _ = check_gate_2_12_attendee_titles(f2)
    assert not passed


def test_ge4_generic_person_description_fails_gate_3_11():
    """GE-4: 'Review participant.' description on person:* must fail Gate 3.11."""
    entities = [{"id": "person:jill_cizewski", "name": "Jill Cizewski", "description": "Review participant."}]
    passed, failures, _ = lint_person_descriptions(entities)
    assert not passed
    assert "Gate 3.11" in failures[0]


def test_ge5_contradictory_member_of_fails_gate_3_12():
    """GE-5: Client attendee described as 'PartnerAdvisory team member' must fail Gate 3.12."""
    rels = [{
        "signal_id": "rel-sharon-apex",
        "predicate": "MEMBER_OF",
        "target_node_id": "org:apex",
        "description": "PartnerAdvisory team member.",
    }]
    passed, failures, _ = lint_member_of_consistency(rels)
    assert not passed
    assert "Gate 3.12" in failures[0]


def test_ge6_clean_outputs_pass_all_qa_gates():
    """GE-6: Clean, fully resolved deliverables must pass all gates cohesively."""
    f1 = "# Output 01\nAttendees:\n- Daniel Brankin (Director of Digital Technology)\nTurn 1: Hi"
    assert len(_check_attendee_title_completeness(f1, {})) == 0

    f2 = """# Executive Recap
Attendees:
- Apex Global Enterprises (NEA): Daniel Brankin (Director of Digital Technology)

## Appendix
### Daniel Brankin — Apex Global Enterprises (NEA), Director of Digital Technology
Notes.
"""
    p2, f2_errs, _ = check_gate_2_12_attendee_titles(f2)
    assert p2
    assert len(f2_errs) == 0

    entities = [{"id": "person:daniel_brankin", "name": "Daniel Brankin", "description": "Director of Digital Technology at APEX."}]
    p3, f3_errs, _ = lint_person_descriptions(entities)
    assert p3

    rels = [{"signal_id": "r1", "predicate": "MEMBER_OF", "target_node_id": "org:apex", "description": "APEX leadership team member."}]
    p4, f4_errs, _ = lint_member_of_consistency(rels)
    assert p4
