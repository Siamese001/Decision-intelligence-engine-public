"""
tests/adversarial/test_role_hallucination_adversarial.py

Adversarial Edge-Case Test Suite for Role Hallucination & GraphDB Sourcing.
Covers 5 attack vectors and failure modes across LLM hallucination healing,
placeholder graph role handling, punctuation/casing evasion, database degradation,
and multi-attendee partial resolution.
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
)
from dual_pipeline.transcript_qa_core.role_consistency_validator import (
    RoleConsistencyValidator,
)
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


def _setup_adversarial_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER
        )
    """)
    cur.execute("""
        INSERT INTO node VALUES
        ('person:amit_ayer', 'person', 'Amit Ayer', 'Amit Ayer', 'Role: Chief Architecture Officer. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:placeholder_user', 'person', 'Placeholder User', 'Placeholder User', 'Role: Participant. Affiliation label(s): NEA.', 'active', 1),
        ('person:tbd_user', 'person', 'TBD User', 'TBD User', 'Source roles: TBD. Affiliation label(s): NEA.', 'active', 1),
        ('person:daniel_brankin', 'stakeholder', 'Daniel Brankin', 'Daniel Brankin', 'Role: Director of Digital Technology. Affiliation label(s): NEA.', 'active', 1)
    """)
    conn.commit()
    conn.close()


def test_adv1_llm_hallucinated_role_auto_healed():
    """ADV-1: LLM hallucinating a fabricated role in Output 01 must be auto-healed to GraphDB SSOT."""
    forensic_transcript = (
        "# Strategic Sync -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/28/2026\n"
        "**Participants:**\n"
        "- PartnerAdvisory: Amit Ayer (Lead AI Prompt Synthesizer)\n\n"
        "---\n"
        "**Amit Ayer:** Here is our plan."
    )
    metadata = {
        "graph_role_verification_receipt": {
            "attendees_verified": {
                "Amit Ayer": {"role": "Chief Architecture Officer", "status": "GRAPH_VERIFIED"}
            }
        }
    }
    healed, report = RoleConsistencyValidator.validate_and_heal(forensic_transcript, metadata)
    assert report.is_consistent is True
    assert report.roles_healed == 1
    assert "Amit Ayer (Chief Architecture Officer)" in healed
    assert "Lead AI Prompt Synthesizer" not in healed
    assert report.discrepancies[0].healed is True


def test_adv2_empty_or_placeholder_graph_role_falls_to_hitl(tmp_path, monkeypatch):
    """ADV-2: Attendee with placeholder role in GraphDB node must not clear C0.6; must fire HITL."""
    db_path = tmp_path / "graph.sqlite"
    _setup_adversarial_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, repo_root=None: resolver.resolve(name),
    )

    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Placeholder User", "role": "Participant", "org": "Apex Global Enterprises (NEA)"},
            ]
        }
    }
    gate = C06GraphDBRoleVerificationGate()
    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_context)

    assert exc_info.value.hitl_type == "graphdb_role_verification"
    assert "Placeholder User" in exc_info.value.prompt
    assert "role:Placeholder User" in exc_info.value.missing_fields


def test_adv3_case_and_punctuation_role_matching():
    """ADV-3: Minor case and punctuation differences must not cause false-positive hallucinations."""
    transcript = (
        "# Sync -- HITL-Resolved Transcript\n\n"
        "**Participants:**\n"
        "- PartnerAdvisory: Amit Ayer (chief architecture officer)\n"
    )
    metadata = {
        "graph_role_verification_receipt": {
            "attendees_verified": {
                "Amit Ayer": {"role": "Chief Architecture Officer", "status": "GRAPH_VERIFIED"}
            }
        }
    }
    healed, report = RoleConsistencyValidator.validate_and_heal(transcript, metadata)
    assert report.is_consistent is True
    assert report.roles_healed == 0
    assert report.roles_verified == 1


def test_adv4_graphdb_disconnection_sets_fail_loud_degradation():
    """ADV-4: ForensicNormalizer must flag UNGROUNDED when graph context is degraded."""
    class MockProvider:
        def synthesize_forensic(self, prompt, user_content):
            return "# Sync\n**Participants:**\n- PartnerAdvisory: Amit\nTurn 1: Hi"

    normalizer = ForensicNormalizer()
    u0_ctx = {
        "clean_transcript": "Speaker 1: Hello",
        "metadata": {
            "meeting_name": "Sync",
            "date": "09/28/2026",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "speaker_map": {"Speaker 1": "Amit Ayer"},
            "graph_context_degraded": True,
            "roster": [{"name": "Amit Ayer", "role": "Architect", "org": "PartnerAdvisory"}],
        },
    }
    plan = MagicMock()
    plan.diarization_status = "UNDIARIZED"

    normalizer.execute(u0_ctx, plan, MockProvider())
    assert u0_ctx["metadata"].get("graph_grounding_status") == "UNGROUNDED"


def test_adv5_multi_attendee_partial_graph_resolution(tmp_path, monkeypatch):
    """ADV-5: When 2 attendees are in GraphDB and 1 is novel, C0.6 verifies 2 and raises HITL for the 1."""
    db_path = tmp_path / "graph.sqlite"
    _setup_adversarial_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, repo_root=None: resolver.resolve(name),
    )

    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Amit Ayer", "role": "Architect", "org": "PartnerAdvisory"},
                {"name": "Daniel Brankin", "role": "Director", "org": "Apex Global Enterprises (NEA)"},
                {"name": "External Auditor", "role": "Auditor", "org": "Third Party"},
            ]
        }
    }
    gate = C06GraphDBRoleVerificationGate()
    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_context)

    # Must specifically target the 1 unverified attendee
    assert exc_info.value.missing_fields == ["role:External Auditor"]
    assert "External Auditor" in exc_info.value.prompt
    assert "Amit Ayer" not in exc_info.value.missing_fields
    assert "Daniel Brankin" not in exc_info.value.missing_fields
