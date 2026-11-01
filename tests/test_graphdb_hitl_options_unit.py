"""Unit tests for Wave 2: Mandatory GraphDB HITL Validation & Confidence-Scored Options.

Verifies that C0, C0.5, C0.6, U0, and D6 gates traverse GraphDB, detect role ambiguities
and mismatches, and ascribe exact GraphDB confidence percentages to all HITL options.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.ambiguity_core.detector_d6_entity import EntityClarificationDetector
from dual_pipeline.canonical_pipeline.u0_ingress import batch_validate_preflight_checks
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.c0_gates import _check_single_role_hitl
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    _build_c05_role_options,
)
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
    _build_role_recommendation_options,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import _normalize_roster_entities


def _setup_isolated_graph_db(db_path: Path) -> None:
    """Sets up an isolated SQLite GraphDB with multi-role assertions for Amit Ayer."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT,
            properties_json TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );
    """)

    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            confidence_score REAL NOT NULL,
            currentness_state TEXT NOT NULL,
            validation_state TEXT NOT NULL,
            scope_text TEXT,
            observed_at TEXT,
            valid_from TEXT,
            valid_to TEXT,
            superseded_by_assertion_id TEXT,
            is_quarantined INTEGER DEFAULT 0
        );
    """)

    cur.execute("""
        CREATE TABLE alias (
            alias_id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
    """)

    # Seed Person Nodes
    cur.execute("""
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active)
        VALUES 
            ('person_amit', 'stakeholder', 'Amit Ayer', 'Amit Ayer', 'PartnerAdvisory Partner & Architecture Lead', '{"org": "PartnerAdvisory"}', 1),
            ('person_mandy', 'stakeholder', 'harper Hoffman', 'harper Hoffman', 'Apex Global Product Manager', '{"org": "Apex Global Enterprises (NEA)"}', 1);
    """)

    # Seed Role Group Nodes
    cur.execute("""
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active)
        VALUES
            ('entity-443f', 'role_group', 'Lead Client Partner', 'Lead Client Partner', 'Role group', 1),
            ('entity-dfc8', 'role_group', 'Principal Architect & Technology Strategist', 'Principal Architect & Technology Strategist', 'Role group', 1),
            ('entity-db16', 'role_group', 'Lead Enterprise Architect', 'Lead Enterprise Architect', 'Role group', 1),
            ('entity-mandy-role', 'role_group', 'Product Manager', 'Product Manager', 'Role group', 1);
    """)

    # Seed Assertions for Amit Ayer:
    # 1. Lead Client Partner (0.90, current, 2026-10-13)
    # 2. Principal Architect & Technology Strategist (0.90, current, 2026-10-12)
    # 3. Lead Enterprise Architect (0.50, current, 2026-10-09)
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, superseded_by_assertion_id, scope_text)
        VALUES
            ('asrt-amit-1', 'person_amit', 'RELATED_TO', 'entity-443f', 0.90, 'current', 'needs_review', '2026-10-13', NULL, 'Amit Ayer HAS_ROLE Lead Client Partner.'),
            ('asrt-amit-2', 'person_amit', 'RELATED_TO', 'entity-dfc8', 0.90, 'current', 'needs_review', '2026-10-12', NULL, 'Amit Ayer HAS_ROLE Principal Architect & Technology Strategist.'),
            ('asrt-amit-3', 'person_amit', 'HAS_ROLE_IN', 'entity-db16', 0.50, 'current', 'needs_review', '2026-10-09', NULL, 'Amit Ayer HAS_ROLE_IN Lead Enterprise Architect.');
    """)

    # Seed harper Hoffman (single unambiguous role at 0.95)
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, superseded_by_assertion_id, scope_text)
        VALUES
            ('asrt-mandy-1', 'person_mandy', 'HAS_ROLE_IN', 'entity-mandy-role', 0.95, 'current', 'needs_review', '2026-10-01', NULL, 'harper Hoffman HAS_ROLE_IN Product Manager.');
    """)

    # Seed Aliases
    cur.execute("""
        INSERT INTO alias (alias_id, node_id, alias, active)
        VALUES
            ('al-amit-1', 'person_amit', 'Amit', 1),
            ('al-amit-2', 'person_amit', 'amit.ayer@consulting.example.com', 1);
    """)

    conn.commit()
    conn.close()


def test_c06_gate_emits_graphdb_confidence_options(tmp_path, monkeypatch):
    """Verifies C0.6 gate generates HITL options matching GraphDB confidence scores."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda raw, **kw: resolver.resolve(raw, explicit_role=kw.get("explicit_role")),
    )

    gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)
    l0_context = {
        "metadata": {
            "roster": [{"name": "Amit Ayer", "role": "Lead Enterprise Architect"}],
            "repo_root": str(tmp_path),
        }
    }

    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_context)

    opts = exc_info.value.options
    assert len(opts) == 4

    # Option 0: Top candidate (Recommended) at 0.90
    assert opts[0]["role"] == "Lead Client Partner"
    assert opts[0]["confidence"] == 0.90
    assert opts[0]["confidence_level"] == "HIGH (90%)"
    assert opts[0]["is_recommended"] is True
    assert "(Recommended)" in opts[0]["label"]

    # Option 1: Competing candidate at 0.90
    assert opts[1]["role"] == "Principal Architect & Technology Strategist"
    assert opts[1]["confidence"] == 0.90
    assert opts[1]["confidence_level"] == "HIGH (90%)"
    assert opts[1]["is_recommended"] is False

    # Option 2: Third candidate at 0.50
    assert opts[2]["role"] == "Lead Enterprise Architect"
    assert opts[2]["confidence"] == 0.50
    assert opts[2]["confidence_level"] == "MEDIUM (50%)"
    assert opts[2]["is_recommended"] is False

    # Confirm static percentages are absent
    for o in opts:
        assert "85%" not in o["label"]
        assert "75%" not in o["label"]
        assert "60%" not in o["label"]


def test_c05_options_builder_matches_graphdb_confidences(tmp_path):
    """Verifies C0.5 options builder accurately maps EntityCandidate tuples to options."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("Amit Ayer")
    assert ent is not None

    opts = _build_c05_role_options("Amit Ayer", ent)
    assert len(opts) == 4

    assert opts[0]["role"] == "Lead Client Partner"
    assert opts[0]["confidence"] == 0.90
    assert opts[0]["confidence_level"] == "HIGH (90%)"
    assert opts[0]["is_recommended"] is True

    assert opts[1]["role"] == "Principal Architect & Technology Strategist"
    assert opts[1]["confidence"] == 0.90

    assert opts[2]["role"] == "Lead Enterprise Architect"
    assert opts[2]["confidence"] == 0.50


def test_c0_gate_check_single_role_hitl_flags_ambiguity(tmp_path, monkeypatch):
    """Verifies C0 gate _check_single_role_hitl halts on competing GraphDB titles."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda raw, **kw: resolver.resolve(raw, explicit_role=kw.get("explicit_role")),
    )

    metadata: Dict[str, Any] = {"repo_root": str(tmp_path)}

    with pytest.raises(HITLRequiredError) as exc_info:
        _check_single_role_hitl("Amit Ayer", "Lead Enterprise Architect", metadata)

    opts = exc_info.value.options
    assert len(opts) == 4
    assert opts[0]["role"] == "Lead Client Partner"
    assert opts[0]["confidence"] == 0.90
    assert "(Recommended)" in opts[0]["label"]


def test_u0_ingestion_roster_normalization_captures_candidates(tmp_path, monkeypatch):
    """Verifies U0 ingestion attaches GraphDB candidates and marks ambiguity conflict."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda raw, **kw: resolver.resolve(raw, explicit_role=kw.get("explicit_role")),
    )

    metadata = {
        "roster": [{"name": "Amit Ayer", "role": "Lead Enterprise Architect"}],
        "repo_root": str(tmp_path),
    }

    _normalize_roster_entities(metadata)
    item = metadata["roster"][0]

    assert item["name"] == "Amit Ayer"
    assert item.get("requires_operator_confirmation") is True
    assert item.get("status") == "AMBIGUOUS_CONFLICT"
    assert len(item.get("candidates", ())) == 3


def test_u0_ingress_preflight_catches_graphdb_conflict(tmp_path, monkeypatch):
    """Verifies U0 ingress batch preflight flags unverified ambiguous roles."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda raw, **kw: resolver.resolve(raw, explicit_role=kw.get("explicit_role")),
    )

    metadata = {
        "time": "10:00 AM - 10:30 AM ET",
        "pre_enrichment_roles": {"Amit Ayer": "Lead Enterprise Architect"},
        "repo_root": str(tmp_path),
    }

    report = batch_validate_preflight_checks(metadata)
    assert report.is_valid is False
    assert "attendee_roles" in report.missing_fields


def test_d6_entity_detector_uses_graphdb_confidences(tmp_path, monkeypatch):
    """Verifies D6 referenced entity detector dynamically discovers Amit Ayer with GraphDB confidence scores."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda raw, **kw: resolver.resolve(raw, explicit_role=kw.get("explicit_role")),
    )

    detector = EntityClarificationDetector(margin_threshold=0.30)
    turns = [
        {"text": "We should check with Amit regarding the cloud architecture review."},
        {"text": "Amit mentioned the deployment timeline yesterday."},
    ]

    records = detector.detect(turns, known_attendees=[])
    assert len(records) >= 1

    rec = next(r for r in records if "Amit" in r.source_excerpt)
    assert rec.requires_hitl is True
    assert rec.delta_margin <= 0.30

    # Options should have GraphDB roles
    opt_roles = [o.attributes.get("role") for o in rec.candidates if o.attributes]
    assert any("Lead Client Partner" in str(r) for r in opt_roles)
    assert any("Principal Architect" in str(r) for r in opt_roles)


def test_independent_sql_check_verifies_option_provenance(tmp_path):
    """Independent check: verifies emitted option scores strictly match SQLite assertion confidence scores."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT a.assertion_id, o.canonical_name, a.confidence_score
        FROM assertion a
        JOIN node o ON a.object_node_id = o.node_id
        WHERE a.subject_node_id = 'person_amit'
        ORDER BY a.confidence_score DESC, a.valid_from DESC
    """).fetchall()
    conn.close()

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path
    ent = resolver.resolve("Amit Ayer")
    assert ent is not None

    opts = _build_role_recommendation_options("Amit Ayer", ent)

    # First 3 options must exactly equal the raw SQL rows
    for raw_r, opt in zip(rows, opts[:3]):
        raw_asrt_id, raw_role, raw_conf = raw_r
        assert opt["role"] == raw_role
        assert opt["confidence"] == raw_conf
        assert opt["provenance"] == raw_asrt_id
