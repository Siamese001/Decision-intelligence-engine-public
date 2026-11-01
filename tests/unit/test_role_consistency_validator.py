"""Unit tests for Wave 3: Pre-Output-01 Role Consistency Validator."""

from dual_pipeline.transcript_qa_core.role_consistency_validator import (
    RoleConsistencyValidator,
)


def test_validator_detects_matching_roles():
    transcript = (
        "# Test Meeting -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/28/2026\n"
        "**Participants:**\n"
        "- PartnerAdvisory: Amit Ayer (Chief Architecture Officer)\n"
        "- Apex Global Enterprises (NEA): Daniel Brankin (Director of Digital Technology)\n\n"
        "---\nTurn 1: Hi"
    )
    metadata = {
        "graph_role_verification_receipt": {
            "attendees_verified": {
                "Amit Ayer": {"role": "Chief Architecture Officer", "status": "GRAPH_VERIFIED"},
                "Daniel Brankin": {"role": "Director of Digital Technology", "status": "GRAPH_VERIFIED"},
            }
        }
    }
    healed, report = RoleConsistencyValidator.validate_and_heal(transcript, metadata)
    assert report.is_consistent is True
    assert report.roles_checked == 2
    assert report.roles_verified == 2
    assert report.roles_healed == 0
    assert healed == transcript


def test_validator_detects_and_heals_hallucinated_role():
    # LLM hallucinated "Junior Software Consultant" for Amit Ayer
    transcript = (
        "# Test Meeting -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/28/2026\n"
        "**Participants:**\n"
        "- PartnerAdvisory: Amit Ayer (Junior Software Consultant)\n\n"
        "---\nTurn 1: Hi"
    )
    metadata = {
        "graph_role_verification_receipt": {
            "attendees_verified": {
                "Amit Ayer": {"role": "Chief Architecture Officer", "status": "GRAPH_VERIFIED"},
            }
        }
    }
    healed, report = RoleConsistencyValidator.validate_and_heal(transcript, metadata)
    assert report.is_consistent is True
    assert report.roles_healed == 1
    assert "Amit Ayer (Chief Architecture Officer)" in healed
    assert "Junior Software Consultant" not in healed
    assert len(report.discrepancies) == 1
    assert report.discrepancies[0].rendered_role == "Junior Software Consultant"
    assert report.discrepancies[0].canonical_role == "Chief Architecture Officer"
    assert report.discrepancies[0].healed is True


def test_validator_resolves_from_hitl_receipt_if_divergent():
    transcript = (
        "# Test Meeting -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/28/2026\n"
        "**Participants:**\n"
        "- Apex Global Enterprises (NEA): Daniel Brankin (General Participant)\n\n"
        "---\nTurn 1: Hi"
    )
    metadata = {
        "hitl_role_receipts": {
            "Daniel Brankin": {
                "decision_type": "ROLE_CLARIFICATION",
                "resolved_role": "Director of Digital Technology",
                "hitl_receipt_bound": True,
            }
        }
    }
    healed, report = RoleConsistencyValidator.validate_and_heal(transcript, metadata)
    assert report.roles_healed == 1
    assert "Daniel Brankin (Director of Digital Technology)" in healed
    assert "General Participant" not in healed
