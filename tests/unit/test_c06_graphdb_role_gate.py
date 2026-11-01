"""Unit tests for Wave 1: C0.6 Mandatory GraphDB Role Verification Gate."""

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


def _setup_test_graph_db(db_path: Path) -> None:
    """Create a test graph.sqlite with node and assertion tables."""
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
    # Insert graph nodes with verified roles
    cur.execute("""
        INSERT INTO node VALUES
        ('person:amit_ayer', 'person', 'Amit Ayer', 'Amit Ayer', 'Role: Chief Architecture Officer. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:daniel_brankin', 'stakeholder', 'Daniel Brankin', 'Daniel Brankin', 'Source roles: Director of Digital Technology. Affiliation label(s): NEA.', 'active', 1),
        ('person:sharon_diaram', 'person', 'Sharon Diaram', 'Sharon Diaram', 'Participant in meeting', 'active', 1)
    """)
    conn.commit()
    conn.close()


def test_c06_gate_passes_when_all_attendees_verified_in_graphdb(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    # Point resolver to this test database
    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Amit Ayer", "role": "Architect", "org": "PartnerAdvisory"},
                {"name": "Daniel Brankin", "role": "Director", "org": "Apex Global Enterprises (NEA)"},
            ]
        }
    }
    gate = C06GraphDBRoleVerificationGate()
    result = gate.execute(l0_context)

    assert result.get("c06_role_gate_cleared") is True
    receipt = l0_context["metadata"].get("graph_role_verification_receipt")
    assert receipt is not None
    assert receipt["status"] == "CLEARED"
    assert receipt["attendees_verified"]["Amit Ayer"]["status"] == "GRAPH_VERIFIED"
    assert receipt["attendees_verified"]["Amit Ayer"]["role"] == "Chief Architecture Officer"
    assert receipt["attendees_verified"]["Daniel Brankin"]["role"] == "Director of Digital Technology"


def test_graphdb_priority_over_hardcoded_directory(tmp_path, monkeypatch):
    """Verifies that GraphDB verified role takes strict priority over stale directory priors."""
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)

    # In _CANONICAL_ENTERPRISE_DIRECTORY, Amit Ayer is "Lead Enterprise Architect"
    # In our test graph.sqlite, he is "Chief Architecture Officer"
    resolved = resolver.resolve("Amit Ayer")
    assert resolved is not None
    assert resolved.role == "Chief Architecture Officer"
    assert resolved.source == "knowledge_graph"


def test_c06_gate_raises_hitl_when_attendee_not_in_graphdb(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Unknown Person", "role": "Some Generic Title", "org": "Unknown Org"},
            ]
        }
    }
    gate = C06GraphDBRoleVerificationGate()
    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_context)

    err = exc_info.value
    assert err.hitl_type == "graphdb_role_verification"
    assert "Unknown Person" in err.prompt
    assert "role:Unknown Person" in err.missing_fields
    assert len(err.options) >= 3
    assert any(opt.get("is_recommended") for opt in err.options)
    assert l0_context["metadata"].get("invalidate_prior_outputs") is True


def test_c06_gate_resolves_via_interactive_resolver(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    # Mock interactive resolver (e.g. simulates ask_user_question response)
    def mock_ask_user_question(envelope):
        assert envelope["attendee"] == "Novel Contributor"
        return "Principal Solutions Architect"

    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Novel Contributor", "role": "Staff", "org": "PartnerAdvisory"},
            ],
            "interactive_resolver": mock_ask_user_question,
        }
    }
    gate = C06GraphDBRoleVerificationGate()
    result = gate.execute(l0_context)

    assert result.get("c06_role_gate_cleared") is True
    receipt = l0_context["metadata"].get("graph_role_verification_receipt")
    assert receipt["attendees_verified"]["Novel Contributor"]["status"] == "HITL_RESOLVED"
    assert receipt["attendees_verified"]["Novel Contributor"]["role"] == "Principal Solutions Architect"


def test_c06_gate_idempotent_after_hitl_resolution(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Novel Contributor", "role": "", "org": "PartnerAdvisory"},
            ]
        }
    }
    gate = C06GraphDBRoleVerificationGate()
    with pytest.raises(HITLRequiredError):
        gate.execute(l0_context)

    # Operator confirms role
    apply_hitl_role_resolution(
        l0_context["metadata"],
        "Novel Contributor",
        "Lead Engagement Manager",
        repo_root=tmp_path,
    )

    # Rerun passes cleanly
    result = gate.execute(l0_context)
    assert result.get("c06_role_gate_cleared") is True
    assert l0_context["metadata"]["graph_role_verification_receipt"]["attendees_verified"]["Novel Contributor"]["status"] == "HITL_RESOLVED"
