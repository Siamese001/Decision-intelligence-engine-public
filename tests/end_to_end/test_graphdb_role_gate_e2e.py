"""
tests/end_to_end/test_graphdb_role_gate_e2e.py

End-to-End Test Suite for GraphDB-Sourced Attendee Role Gate Architecture.
Verifies the full pipeline lifecycle from Stage U0 Ingress through Stage C0.6
Role Verification, Stage 01 Synthesis, and Pre-Freeze Role Consistency Check.
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
)
from dual_pipeline.transcript_pipeline_core.context_builder import (
    _execute_c0_phase,
)
from dual_pipeline.transcript_qa_core.role_consistency_validator import (
    RoleConsistencyValidator,
)
from dual_pipeline.transcript_stages_core.c0_gates import (
    C0ConformanceGate,
    C0RosterContextGate,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer


def _build_test_graph_db(db_path: Path) -> None:
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
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            claim_text TEXT,
            is_quarantined INTEGER
        )
    """)
    cur.execute("""
        INSERT INTO node VALUES
        ('person:amit_ayer', 'person', 'Amit Ayer', 'Amit Ayer', 'Role: Chief Architecture Officer. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:jennifer_pan', 'person', 'Jennifer Pan', 'Jen Pan', 'Role: Principal Engagement Director. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:daniel_brankin', 'stakeholder', 'Daniel Brankin', 'Daniel Brankin', 'Source roles: Director of Digital Technology. Affiliation label(s): NEA.', 'active', 1)
    """)
    conn.commit()
    conn.close()


def test_e2e_graphdb_verified_roles_flow_through_output1(tmp_path, monkeypatch):
    """E2E Test: Verified GraphDB roles flow from C0.6 into Output 01 and pass consistency check."""
    db_path = tmp_path / "graph.sqlite"
    _build_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, repo_root=None: resolver.resolve(name),
    )

    plan = MagicMock()
    plan.diarization_status = "UNDIARIZED"
    plan.roster_gate_needed = False
    plan.source_type = "MEETING_TRANSCRIPT"
    plan.meeting_type_hint = "architectural_review"

    metadata = {
        "meeting_name": "Nexus Architecture Review",
        "date": "09/28/2026",
        "platform": "Microsoft Teams",
        "source_type": "MEETING_TRANSCRIPT",
        "speaker_map": {"Speaker 1": "Amit Ayer", "Speaker 2": "Daniel Brankin"},
        "roster": [
            {"name": "Amit Ayer", "role": "Architect", "org": "PartnerAdvisory"},
            {"name": "Daniel Brankin", "role": "Director", "org": "Apex Global Enterprises (NEA)"},
        ],
    }
    u0_context = {
        "clean_transcript": "Speaker 1: Welcome everyone.\nSpeaker 2: Thanks Amit.",
        "hash": "test_hash_123",
        "metadata": metadata,
    }
    l0_context = {
        "transcript_text": u0_context["clean_transcript"],
        "metadata": metadata,
    }

    # 1. Execute C0 phase (which runs C0, C0.5, and C0.6)
    _execute_c0_phase(
        plan=plan,
        u0_context=u0_context,
        l0_context=l0_context,
        transcript_text=u0_context["clean_transcript"],
        c0_native=C0ConformanceGate(),
        c0_undiarized=C0RosterContextGate(),
        auto_scaffolder=MagicMock(),
    )

    # Verify C0.6 cleared and enriched metadata roster with GraphDB roles
    assert l0_context.get("c06_role_gate_cleared") is True
    receipt = l0_context["metadata"].get("graph_role_verification_receipt")
    assert receipt is not None
    assert receipt["status"] == "CLEARED"
    assert receipt["attendees_verified"]["Amit Ayer"]["role"] == "Chief Architecture Officer"
    assert receipt["attendees_verified"]["Daniel Brankin"]["role"] == "Director of Digital Technology"

    # 2. Stage 01 Forensic Normalizer
    class MockProvider:
        def synthesize_forensic(self, prompt, user_content):
            return (
                "# Nexus Architecture Review -- HITL-Resolved Transcript\n\n"
                "**Date:** 09/28/2026\n"
                "**Platform Reference:** Microsoft Teams\n"
                "**Participants:**\n"
                "- PartnerAdvisory: Amit Ayer (Chief Architecture Officer)\n"
                "- Apex Global Enterprises (NEA): Daniel Brankin (Director of Digital Technology)\n\n"
                "---\n"
                "**Amit Ayer:** Welcome everyone.\n"
                "**Daniel Brankin:** Thanks Amit.\n"
            )

    normalizer = ForensicNormalizer()
    forensic_out = normalizer.execute(u0_context, plan, MockProvider())

    # 3. Post-synthesis pre-freeze consistency check
    healed, report = RoleConsistencyValidator.validate_and_heal(forensic_out, metadata)
    assert report.is_consistent is True
    assert report.roles_checked == 2
    assert report.roles_verified == 2
    assert report.roles_healed == 0


def test_e2e_stale_directory_overridden_by_graphdb_in_pipeline(tmp_path, monkeypatch):
    """E2E Test: Stale directory prior is overridden by fresh GraphDB entry across pipeline."""
    db_path = tmp_path / "graph.sqlite"
    _build_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, repo_root=None: resolver.resolve(name),
    )

    plan = MagicMock()
    plan.diarization_status = "UNDIARIZED"
    plan.roster_gate_needed = False
    plan.source_type = "MEETING_TRANSCRIPT"

    # In _CANONICAL_ENTERPRISE_DIRECTORY, Jen Pan is "Product Manager / Lead Engineer"
    # In our graph.sqlite, she was promoted to "Principal Engagement Director"
    metadata = {
        "meeting_name": "Executive Strategy Session",
        "date": "09/28/2026",
        "platform": "Microsoft Teams",
        "source_type": "MEETING_TRANSCRIPT",
        "roster": [
            {"name": "Jen Pan", "role": "Product Manager", "org": "PartnerAdvisory"},
        ],
    }
    l0_context = {"metadata": metadata}
    u0_context = {"metadata": metadata, "hash": "h123", "clean_transcript": "Jen: Hello"}

    _execute_c0_phase(
        plan=plan,
        u0_context=u0_context,
        l0_context=l0_context,
        transcript_text="Jen: Hello",
        c0_native=C0ConformanceGate(),
        c0_undiarized=C0RosterContextGate(),
        auto_scaffolder=MagicMock(),
    )

    # GraphDB verified role must win over the hardcoded directory prior
    verified_entry = l0_context["metadata"]["graph_role_verification_receipt"]["attendees_verified"]["Jen Pan"]
    assert verified_entry["role"] == "Principal Engagement Director"
    assert verified_entry["status"] == "GRAPH_VERIFIED"


def test_e2e_unregistered_attendee_triggers_hitl_and_heals(tmp_path, monkeypatch):
    """E2E Test: Novel attendee not in GraphDB triggers HITL and heals into Output 01."""
    db_path = tmp_path / "graph.sqlite"
    _build_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, repo_root=None: resolver.resolve(name),
    )

    # Interactive resolver simulates ask_user_question answering operator prompt
    def operator_answer_resolver(envelope):
        assert envelope["attendee"] == "Sarah Connor"
        return "VP of Cloud Platform Operations"

    plan = MagicMock()
    plan.diarization_status = "UNDIARIZED"
    plan.roster_gate_needed = False
    plan.source_type = "MEETING_TRANSCRIPT"

    metadata = {
        "meeting_name": "Executive Strategy Session",
        "date": "09/28/2026",
        "platform": "Microsoft Teams",
        "source_type": "MEETING_TRANSCRIPT",
        "interactive_resolver": operator_answer_resolver,
        "roster": [
            {"name": "Sarah Connor", "role": "Cloud Operations Architect", "org": "Apex Global Enterprises (NEA)"},
        ],
    }
    l0_context = {"metadata": metadata}
    u0_context = {"metadata": metadata, "hash": "h123", "clean_transcript": "Sarah: Hello"}

    _execute_c0_phase(
        plan=plan,
        u0_context=u0_context,
        l0_context=l0_context,
        transcript_text="Sarah: Hello",
        c0_native=C0ConformanceGate(),
        c0_undiarized=C0RosterContextGate(),
        auto_scaffolder=MagicMock(),
    )

    assert l0_context.get("c06_role_gate_cleared") is True
    receipt = l0_context["metadata"]["graph_role_verification_receipt"]
    assert receipt["attendees_verified"]["Sarah Connor"]["status"] == "HITL_RESOLVED"
    assert receipt["attendees_verified"]["Sarah Connor"]["role"] == "VP of Cloud Platform Operations"
