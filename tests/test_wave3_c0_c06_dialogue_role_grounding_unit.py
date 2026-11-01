"""
tests/test_wave3_c0_c06_dialogue_role_grounding_unit.py

Wave 3 Acceptance Tests: Enforce Dialogue-Grounded Role Verification & Restore
Ambiguity Conflict Raising in C0 / C0.6.
Verifies that attendees with unstated roles or AMBIGUOUS_CONFLICT statuses in GraphDB
strictly require interactive HITL confirmation rather than silently auto-clearing with
is_human_confirmed=False.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    EntityCandidate,
    PreSynthesisEntityResolver,
    ResolvedEntity,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.c0_gates import (
    C0ConformanceGate,
    _check_single_role_hitl,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


def _setup_test_graph_db(db_path: Path) -> None:
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
        ('person:amanda_knarr', 'person', 'Amanda Knarr', 'Amanda Knarr', 'Role: Solutions Consultant. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:traci_metzger', 'person', 'Taylor Mercer', 'Taylor Mercer', 'Role: Delivery Lead-PM vs PM. Affiliation label(s): PartnerAdvisory.', 'active', 1)
    """)
    cur.execute("""
        INSERT INTO assertion VALUES
        ('assertion-amanda-01', 'person:amanda_knarr', 'HAS_ROLE', NULL, 'Solutions Consultant', 'Amanda Knarr is Solutions Consultant', 0),
        ('assertion-traci-01', 'person:traci_metzger', 'HAS_ROLE', NULL, 'Delivery Lead-PM', 'Taylor Mercer Delivery Lead-PM', 0),
        ('assertion-traci-02', 'person:traci_metzger', 'HAS_ROLE', NULL, 'PM', 'Taylor Mercer PM', 0)
    """)
    conn.commit()
    conn.close()


def test_c06_unstated_role_raises_hitl_with_graphdb_recommendation(tmp_path, monkeypatch):
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
            "meeting_name": "Google PartnerAdvisory Apex Global Sync",
            "repo_root": tmp_path,
            "roster": [
                {"name": "Amanda Knarr", "role": "", "org": "PartnerAdvisory"},
            ],
        }
    }
    gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)

    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_context)

    err = exc_info.value
    assert err.hitl_type == "graphdb_role_verification"
    assert "role:Amanda Knarr" in err.missing_fields
    assert len(err.options) >= 2
    # Verify GraphDB role is presented as top recommended option
    rec_opt = next((opt for opt in err.options if opt.get("is_recommended")), None)
    assert rec_opt is not None
    assert rec_opt["role"] == "Solutions Consultant"
    assert rec_opt["tier"] == "HIGH"
    assert rec_opt["confidence"] >= 0.80


def test_c06_ambiguous_conflict_raises_hitl_with_competing_options(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    # Mock resolver returning AMBIGUOUS_CONFLICT entity for Taylor Mercer
    candidates = (
        EntityCandidate(role="Delivery Lead-PM", confidence=0.85, currentness_state="current", validation_state="needs_review", valid_from="2026-10-14", assertion_id="a1", source_node_id="n1"),
        EntityCandidate(role="PM", confidence=0.85, currentness_state="current", validation_state="needs_review", valid_from="2026-10-12", assertion_id="a2", source_node_id="n1"),
    )
    ambiguous_ent = ResolvedEntity(
        canonical_name="Taylor Mercer",
        display_name="Taylor Mercer",
        org="PartnerAdvisory",
        role="Delivery Lead-PM vs PM",
        source="knowledge_graph",
        status="AMBIGUOUS_CONFLICT",
        requires_operator_confirmation=True,
        confidence=0.85,
        candidates=candidates,
    )
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: ambiguous_ent if "Traci" in name else None,
    )

    l0_context = {
        "metadata": {
            "meeting_name": "Project Status",
            "repo_root": tmp_path,
            "roster": [
                {"name": "Taylor Mercer", "role": "", "org": "PartnerAdvisory"},
            ],
        }
    }
    gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)

    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_context)

    err = exc_info.value
    assert err.hitl_type == "graphdb_role_verification"
    assert any(opt.get("role") == "Delivery Lead-PM" for opt in err.options)
    assert any(opt.get("role") == "PM" for opt in err.options)


def test_c06_clears_cleanly_when_human_confirmed_in_ledger(tmp_path, monkeypatch):
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
            "meeting_name": "Google PartnerAdvisory Apex Global Sync",
            "repo_root": tmp_path,
            "roster": [
                {"name": "Amanda Knarr", "role": "", "org": "PartnerAdvisory"},
            ],
        }
    }
    # Pre-confirm via human operator
    apply_hitl_role_resolution(
        l0_context["metadata"],
        "Amanda Knarr",
        "Solutions Consultant",
        organization="PartnerAdvisory",
        is_human_confirmed=True,
        repo_root=tmp_path,
    )

    gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)
    result = gate.execute(l0_context)

    assert result.get("c06_role_gate_cleared") is True
    receipt = l0_context["metadata"]["graph_role_verification_receipt"]
    assert receipt["status"] == "CLEARED"
    assert receipt["attendees_verified"]["Amanda Knarr"]["role"] == "Solutions Consultant"
    assert receipt["attendees_verified"]["Amanda Knarr"]["status"] == "HITL_RESOLVED"


def test_c0_conformance_gate_unstated_role_raises_hitl(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    meta = {
        "meeting_name": "Review",
        "repo_root": tmp_path,
        "roster": [
            {"name": "Amanda Knarr", "role": "", "org": "PartnerAdvisory"},
        ],
    }

    with pytest.raises(HITLRequiredError) as exc_info:
        _check_single_role_hitl("Amanda Knarr", "", meta)

    err = exc_info.value
    assert err.hitl_type == "role_clarification"
    assert "role:Amanda Knarr" in err.missing_fields
    rec_opt = next((opt for opt in err.options if opt.get("is_recommended")), None)
    assert rec_opt is not None
    assert rec_opt["role"] == "Solutions Consultant"


def test_c0_conformance_gate_clears_when_human_confirmed(tmp_path, monkeypatch):
    db_path = tmp_path / "graph.sqlite"
    _setup_test_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_path)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    meta = {
        "meeting_name": "Review",
        "repo_root": tmp_path,
        "roster": [
            {"name": "Amanda Knarr", "role": "", "org": "PartnerAdvisory"},
        ],
    }
    apply_hitl_role_resolution(
        meta,
        "Amanda Knarr",
        "Solutions Consultant",
        organization="PartnerAdvisory",
        is_human_confirmed=True,
        repo_root=tmp_path,
    )

    # Now must not raise
    _check_single_role_hitl("Amanda Knarr", "", meta)
