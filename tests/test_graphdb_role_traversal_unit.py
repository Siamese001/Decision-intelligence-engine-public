"""
tests/test_graphdb_role_traversal_unit.py

Wave 1 unit tests: GraphDB entity and identity resolution multi-role traversal,
candidate ranking by currentness, confidence, and recency, and ambiguity margin gating.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.attendees import KNOWN_ROLES, parse_attendees
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    EntityCandidate,
    PreSynthesisEntityResolver,
    resolve_person_entity,
)


def _setup_isolated_graph_db(db_path: Path) -> None:
    """Provisions hermetic SQLite database with person, role_group nodes, and assertions."""
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

    # Seed Person Nodes
    cur.execute("""
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active)
        VALUES 
            ('person_amit', 'stakeholder', 'Amit Ayer', 'Amit Ayer', 'PartnerAdvisory Partner & Architecture Lead', '{"org": "PartnerAdvisory"}', 1),
            ('person_mandy', 'stakeholder', 'harper Hoffman', 'harper Hoffman', 'Apex Global Product Manager', '{"org": "Apex Global Enterprises (NEA)"}', 1),
            ('person_clear', 'stakeholder', 'Clear Winner', 'Clear Winner', 'Strategy Leader', '{"org": "PartnerAdvisory"}', 1);
    """)

    # Seed Role Group Nodes
    cur.execute("""
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active)
        VALUES
            ('entity-443f', 'role_group', 'Lead Client Partner', 'Lead Client Partner', 'Role group', 1),
            ('entity-dfc8', 'role_group', 'Principal Architect & Technology Strategist', 'Principal Architect & Technology Strategist', 'Role group', 1),
            ('entity-db16', 'role_group', 'Lead Enterprise Architect', 'Lead Enterprise Architect', 'Role group', 1),
            ('entity-mandy-role', 'role_group', 'Product Manager', 'Product Manager', 'Role group', 1),
            ('entity-clear-high', 'role_group', 'Executive Director', 'Executive Director', 'Role group', 1),
            ('entity-clear-low', 'role_group', 'Senior Contributor', 'Senior Contributor', 'Role group', 1);
    """)

    # Seed Assertions for Amit Ayer:
    # 1. Lead Client Partner (0.90, current, 2026-10-13)
    # 2. Principal Architect & Technology Strategist (0.90, current, 2026-10-12)
    # 3. Lead Enterprise Architect (0.50, current, 2026-10-09)
    # 4. Outdated Historical (0.95, historical / superseded)
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, superseded_by_assertion_id, scope_text)
        VALUES
            ('asrt-amit-1', 'person_amit', 'RELATED_TO', 'entity-443f', 0.90, 'current', 'needs_review', '2026-10-13', NULL, 'Amit Ayer HAS_ROLE Lead Client Partner.'),
            ('asrt-amit-2', 'person_amit', 'RELATED_TO', 'entity-dfc8', 0.90, 'current', 'needs_review', '2026-10-12', NULL, 'Amit Ayer HAS_ROLE Principal Architect & Technology Strategist.'),
            ('asrt-amit-3', 'person_amit', 'HAS_ROLE_IN', 'entity-db16', 0.50, 'current', 'needs_review', '2026-10-09', NULL, 'Amit Ayer HAS_ROLE_IN Lead Enterprise Architect.'),
            ('asrt-amit-hist', 'person_amit', 'RELATED_TO', 'entity-db16', 0.95, 'historical', 'needs_review', '2026-09-01', 'asrt-amit-1', 'Amit Ayer HAS_ROLE Outdated Role.');
    """)

    # Seed Assertions for harper Hoffman (single unambiguous role)
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, superseded_by_assertion_id, scope_text)
        VALUES
            ('asrt-mandy-1', 'person_mandy', 'HAS_ROLE_IN', 'entity-mandy-role', 0.95, 'current', 'needs_review', '2026-10-01', NULL, 'harper Hoffman HAS_ROLE_IN Product Manager.');
    """)

    # Seed Assertions for Clear Winner (margin = 0.95 - 0.50 = 0.45 > 0.30)
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, validation_state, valid_from, superseded_by_assertion_id, scope_text)
        VALUES
            ('asrt-clear-1', 'person_clear', 'RELATED_TO', 'entity-clear-high', 0.95, 'current', 'needs_review', '2026-10-01', NULL, 'Clear Winner HAS_ROLE Executive Director.'),
            ('asrt-clear-2', 'person_clear', 'RELATED_TO', 'entity-clear-low', 0.50, 'current', 'needs_review', '2026-09-01', NULL, 'Clear Winner HAS_ROLE Senior Contributor.');
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


def test_graphdb_multi_role_traversal_amit_ayer(tmp_path):
    """Verifies all 3 active role_group assertions are loaded, ranked, and conflict-flagged."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("Amit Ayer")
    assert ent is not None
    assert ent.canonical_name == "Amit Ayer"
    assert ent.org == "PartnerAdvisory"
    assert ent.status == "AMBIGUOUS_CONFLICT"
    assert ent.requires_operator_confirmation is True
    assert ent.confidence == 0.90

    # Verify ranked candidates: ordered by currentness, confidence, and recency
    assert len(ent.candidates) == 3
    c0 = ent.candidates[0]
    assert c0.role == "Lead Client Partner"
    assert c0.confidence == 0.90
    assert c0.valid_from == "2026-10-13"

    c1 = ent.candidates[1]
    assert c1.role == "Principal Architect & Technology Strategist"
    assert c1.confidence == 0.90
    assert c1.valid_from == "2026-10-12"

    c2 = ent.candidates[2]
    assert c2.role == "Lead Enterprise Architect"
    assert c2.confidence == 0.50
    assert c2.valid_from == "2026-10-09"

    # Verify candidate name contains the competing top titles
    assert "Lead Client Partner vs Principal Architect & Technology Strategist" in (ent.candidate_name or "")


def test_graphdb_alias_resolution_preserves_candidates(tmp_path):
    """Verifies aliases resolve to canonical person and preserve full candidate list."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent_alias = resolver.resolve("Amit")
    assert ent_alias is not None
    assert ent_alias.canonical_name == "Amit Ayer"
    assert ent_alias.status == "AMBIGUOUS_CONFLICT"
    assert len(ent_alias.candidates) == 3

    ent_email = resolver.resolve("amit.ayer@consulting.example.com")
    assert ent_email is not None
    assert ent_email.canonical_name == "Amit Ayer"
    assert len(ent_email.candidates) == 3


def test_independent_sql_check_matches_candidates(tmp_path):
    """Independent check: raw SQL over assertion JOIN node matches EntityCandidate list."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("Amit Ayer")
    assert ent is not None

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    raw_rows = cur.execute("""
        SELECT o.canonical_name, a.confidence_score, a.valid_from
        FROM assertion a
        JOIN node o ON a.object_node_id = o.node_id
        WHERE a.subject_node_id = 'person_amit'
          AND a.currentness_state = 'current'
          AND (a.superseded_by_assertion_id IS NULL OR a.superseded_by_assertion_id = '')
        ORDER BY a.confidence_score DESC, a.valid_from DESC
    """).fetchall()
    conn.close()

    assert len(raw_rows) == len(ent.candidates)
    for raw_r, cand in zip(raw_rows, ent.candidates):
        assert cand.role == raw_r[0]
        assert cand.confidence == raw_r[1]
        assert cand.valid_from == raw_r[2]


def test_single_role_person_resolves_cleanly(tmp_path):
    """Verifies a person with a single unambiguous GraphDB role is marked RESOLVED."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("harper Hoffman")
    assert ent is not None
    assert ent.canonical_name == "harper Hoffman"
    assert ent.role == "Product Manager"
    assert ent.confidence == 0.95
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False
    assert len(ent.candidates) == 1


def test_clear_winner_margin_resolves_without_conflict(tmp_path):
    """Verifies that when margin > 0.30, top role wins with RESOLVED status."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    ent = resolver.resolve("Clear Winner")
    assert ent is not None
    assert ent.canonical_name == "Clear Winner"
    assert ent.role == "Executive Director"
    assert ent.confidence == 0.95
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False
    assert len(ent.candidates) == 2


def test_zero_hardcoded_conflicting_titles_in_codebase():
    """Verifies that static dictionaries no longer hardcode conflicting roles for Amit Ayer."""
    assert "amit" not in KNOWN_ROLES

    from dual_pipeline.identity_resolver_core.sources_loader import load_base_identities
    collected_roles = []
    load_base_identities(lambda **kw: collected_roles.append((kw.get("canonical_name"), kw.get("role"))))
    amit_tuples = [t for t in collected_roles if t[0] == "Amit Ayer"]
    assert len(amit_tuples) == 1
    assert amit_tuples[0][1] is None or "Partner / Technical Director" not in str(amit_tuples[0][1])


def test_explicit_role_mismatch_flags_ambiguous_conflict(tmp_path):
    """Verifies explicit conflicting input role triggers AMBIGUOUS_CONFLICT."""
    db_path = tmp_path / "graph.sqlite"
    _setup_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    resolver._get_active_graph_path = lambda: db_path

    # harper Hoffman has GraphDB role "Product Manager"
    # Providing a conflicting input title should flag conflict
    ent = resolver.resolve("harper Hoffman", explicit_role="Vice President of Engineering")
    assert ent is not None
    assert ent.status == "AMBIGUOUS_CONFLICT"
    assert ent.requires_operator_confirmation is True
    assert "Vice President of Engineering vs Product Manager" in (ent.candidate_name or "")
