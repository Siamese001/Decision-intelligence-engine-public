"""
tests/test_wave1_graph_traversal_confidence_unit.py

Wave 1 unit tests: GraphDB traversal validation-tier dominance,
elimination of compound slash role concatenation, and IdentityResolver retention.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
from dual_pipeline.identity_resolver_core.sources_loader import load_sqlite_identities
from dual_pipeline.live_meeting_recorder_core.attendees import _infer_role
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_person_entity,
)
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    clean_attendee_entry,
)


def _setup_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
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
            object_literal TEXT,
            scope_text TEXT,
            confidence_score REAL NOT NULL,
            currentness_state TEXT NOT NULL,
            validation_state TEXT NOT NULL,
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

    # Seed Nodes
    cur.execute("""
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active)
        VALUES
            ('person_verified', 'stakeholder', 'Verified Person', 'Verified Person', 'Role test', '{"org": "PartnerAdvisory"}', 1),
            ('person_tied', 'stakeholder', 'Tied Person', 'Tied Person', 'Role test', '{"org": "PartnerAdvisory"}', 1),
            ('person_historical', 'stakeholder', 'Historical Person', 'Historical Person', 'Role test', '{"org": "PartnerAdvisory"}', 1),
            ('role_primary', 'role_group', 'Principal Architect & Technology Strategist', 'Principal Architect & Technology Strategist', 'Role', '{}', 1),
            ('role_secondary', 'role_group', 'Lead Client Partner', 'Lead Client Partner', 'Role', '{}', 1),
            ('role_old', 'role_group', 'Junior Consultant', 'Junior Consultant', 'Role', '{}', 1);
    """)

    # Assertions for person_verified:
    # Verified 1.0 vs unverified needs_review 0.90
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, scope_text)
        VALUES
            ('asrt-v1', 'person_verified', 'RELATED_TO', 'role_primary', 1.0, 'current', 'verified', '2026-10-16', 'Verified Person HAS_ROLE Principal Architect & Technology Strategist.'),
            ('asrt-v2', 'person_verified', 'RELATED_TO', 'role_secondary', 0.90, 'current', 'needs_review', '2026-10-14', 'Verified Person HAS_ROLE Lead Client Partner.');
    """)

    # Assertions for person_tied:
    # Two current needs_review assertions at 0.90
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, scope_text)
        VALUES
            ('asrt-t1', 'person_tied', 'RELATED_TO', 'role_secondary', 0.90, 'current', 'needs_review', '2026-10-14', 'Tied Person HAS_ROLE Lead Client Partner.'),
            ('asrt-t2', 'person_tied', 'RELATED_TO', 'role_primary', 0.90, 'current', 'needs_review', '2026-10-12', 'Tied Person HAS_ROLE Principal Architect & Technology Strategist.');
    """)

    # Assertions for person_historical:
    # 0.90 current vs 0.95 historical
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, scope_text)
        VALUES
            ('asrt-h1', 'person_historical', 'RELATED_TO', 'role_primary', 0.90, 'current', 'needs_review', '2026-10-12', 'Historical Person HAS_ROLE Principal Architect & Technology Strategist.'),
            ('asrt-h2', 'person_historical', 'RELATED_TO', 'role_old', 0.95, 'historical', 'needs_review', '2026-08-01', 'Historical Person HAS_ROLE Junior Consultant.');
    """)

    conn.commit()
    conn.close()


def test_verified_assertion_dominates_unverified(tmp_path):
    """Verified operator assertion (1.0) must dominate needs_review (0.90) without conflict."""
    db_path = tmp_path / "graph.sqlite"
    _setup_test_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("Verified Person")
    assert ent is not None
    assert ent.canonical_name == "Verified Person"
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False
    assert ent.role == "Principal Architect & Technology Strategist"
    assert ent.confidence == 1.0
    assert " / " not in ent.role


def test_equal_tier_conflict_preserves_atomic_role(tmp_path):
    """Equal-tier conflict flags AMBIGUOUS_CONFLICT while preserving atomic role without slash."""
    db_path = tmp_path / "graph.sqlite"
    _setup_test_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("Tied Person")
    assert ent is not None
    assert ent.status == "AMBIGUOUS_CONFLICT"
    assert ent.requires_operator_confirmation is True
    assert ent.role == "Lead Client Partner"
    assert " / " not in ent.role
    assert "Lead Client Partner vs Principal Architect & Technology Strategist" in (ent.candidate_name or "")
    assert len(ent.candidates) == 2


def test_historical_cannot_erode_current(tmp_path):
    """Historical assertion with higher raw score (0.95) cannot erode current assertion (0.90)."""
    db_path = tmp_path / "graph.sqlite"
    _setup_test_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("Historical Person")
    assert ent is not None
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False
    assert ent.role == "Principal Architect & Technology Strategist"
    assert ent.confidence == 0.90
    assert " / " not in ent.role


def test_sources_loader_retains_top_assertion(tmp_path):
    """load_sqlite_identities retains highest-ranked assertion and does not overwrite with last."""
    db_path = tmp_path / "graph.sqlite"
    _setup_test_db(db_path)

    identities = {}
    load_sqlite_identities(
        tmp_path,
        lambda **kw: identities.update({kw["canonical_name"]: kw}),
        db_path=db_path,
    )

    assert "Verified Person" in identities
    assert identities["Verified Person"]["role"] == "Principal Architect & Technology Strategist"


def test_attendee_parser_rejects_compound_role(tmp_path, monkeypatch):
    """Attendee parser resolves atomic role even when candidate has conflict status."""
    db_path = tmp_path / "graph.sqlite"
    _setup_test_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, **kw: resolver.resolve(name, **kw),
    )

    # Resolving Tied Person
    name, role, org = clean_attendee_entry("Tied Person (PartnerAdvisory)")
    assert name == "Tied Person"
    assert " / " not in role
    assert role == "Lead Client Partner"
