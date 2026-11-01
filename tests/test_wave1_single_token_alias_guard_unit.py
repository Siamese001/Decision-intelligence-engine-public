"""
tests/test_wave1_single_token_alias_guard_unit.py

Unit tests for Wave 1: Eliminate silent first-name GraphDB alias & project roster expansion.
Verifies that single-token first-name queries preserve candidate metadata while enforcing
requires_operator_confirmation=True, and that two-token lookups continue to resolve cleanly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_person_entity,
)


def _seed_isolated_graph_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            node_type TEXT DEFAULT 'person',
            active INTEGER DEFAULT 1,
            properties_json TEXT
        );
        CREATE TABLE IF NOT EXISTS assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            confidence_score REAL,
            status TEXT,
            observed_at TEXT,
            stale_after TEXT,
            currentness_state TEXT,
            superseded_by_assertion_id TEXT,
            validation_state TEXT,
            valid_from TEXT
        );
        CREATE TABLE IF NOT EXISTS alias (
            alias_name TEXT,
            canonical_name TEXT,
            active INTEGER DEFAULT 1
        );
    """)

    # Seed Amanda Knarr
    cur.execute(
        "INSERT INTO node (node_id, canonical_name, display_name, description, properties_json) VALUES (?, ?, ?, ?, ?)",
        (
            "person_amanda_knarr",
            "Amanda Knarr",
            "Amanda Knarr",
            "Solutions Consultant at PartnerAdvisory.",
            json.dumps({"org": "PartnerAdvisory", "role": "Solutions Consultant"}),
        ),
    )
    cur.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_literal, scope_text,
            confidence_score, status, currentness_state, validation_state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "ass_amanda_role",
            "person_amanda_knarr",
            "HAS_ROLE",
            "Solutions Consultant",
            "Amanda Knarr HAS_ROLE Solutions Consultant.",
            0.95,
            "CONFIRMED",
            "current",
            "verified",
        ),
    )

    # Seed Calvin Gilmartin
    cur.execute(
        "INSERT INTO node (node_id, canonical_name, display_name, description, properties_json) VALUES (?, ?, ?, ?, ?)",
        (
            "person_calvin_gilmartin",
            "Calvin Gilmartin",
            "Calvin Gilmartin",
            "Senior Director, Trading Risk & Analytics at Apex Global Enterprises.",
            json.dumps({"org": "Apex Global Enterprises (NEA)", "role": "Senior Director, Trading Risk & Analytics"}),
        ),
    )
    cur.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_literal, scope_text,
            confidence_score, status, currentness_state, validation_state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "ass_calvin_role",
            "person_calvin_gilmartin",
            "HAS_ROLE",
            "Senior Director, Trading Risk & Analytics",
            "Calvin Gilmartin HAS_ROLE Senior Director, Trading Risk & Analytics.",
            0.95,
            "CONFIRMED",
            "current",
            "verified",
        ),
    )

    conn.commit()
    conn.close()


def test_single_token_amanda_requires_operator_confirmation(tmp_path: Path):
    """Verifies that 'Amanda' returns candidate suggestion with requires_operator_confirmation=True."""
    db_path = tmp_path / "graph.sqlite"
    _seed_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Amanda")

    assert ent is not None
    assert ent.canonical_name == "Amanda Knarr"
    assert ent.candidate_name == "Amanda Knarr"
    assert ent.status == "UNRESOLVED_CANDIDATE_ONLY"
    assert ent.requires_operator_confirmation is True
    assert ent.role == "Solutions Consultant"
    assert ent.org == "PartnerAdvisory"


def test_single_token_calvin_requires_operator_confirmation(tmp_path: Path):
    """Verifies that 'Calvin' returns candidate suggestion with requires_operator_confirmation=True."""
    db_path = tmp_path / "graph.sqlite"
    _seed_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Calvin")

    assert ent is not None
    assert ent.canonical_name == "Calvin Gilmartin"
    assert ent.candidate_name == "Calvin Gilmartin"
    assert ent.status == "UNRESOLVED_CANDIDATE_ONLY"
    assert ent.requires_operator_confirmation is True
    assert "Senior Director" in ent.role


def test_full_name_amanda_knarr_resolves_cleanly(tmp_path: Path):
    """Verifies that 'Amanda Knarr' resolves with status=RESOLVED and requires_operator_confirmation=False."""
    db_path = tmp_path / "graph.sqlite"
    _seed_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Amanda Knarr")

    assert ent is not None
    assert ent.canonical_name == "Amanda Knarr"
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False
    assert ent.role == "Solutions Consultant"
    assert ent.org == "PartnerAdvisory"


def test_full_name_calvin_gilmartin_resolves_cleanly(tmp_path: Path):
    """Verifies that 'Calvin Gilmartin' resolves with status=RESOLVED and requires_operator_confirmation=False."""
    db_path = tmp_path / "graph.sqlite"
    _seed_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Calvin Gilmartin")

    assert ent is not None
    assert ent.canonical_name == "Calvin Gilmartin"
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False
    assert "Senior Director" in ent.role


def test_explicit_role_on_single_token_does_not_clear_confirmation(tmp_path: Path):
    """Verifies that matching an explicit role does not clear requires_operator_confirmation for single-token names."""
    db_path = tmp_path / "graph.sqlite"
    _seed_isolated_graph_db(db_path)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Amanda", explicit_role="Solutions Consultant")

    assert ent is not None
    assert ent.canonical_name == "Amanda Knarr"
    assert ent.role == "Solutions Consultant"
    assert ent.requires_operator_confirmation is True
    assert ent.status == "UNRESOLVED_CANDIDATE_ONLY"


def test_project_roster_single_token_requires_confirmation(tmp_path: Path):
    """Verifies that project roster single-first-name lookup requires operator confirmation."""
    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Jennifer", project_name="project_lens")

    assert ent is not None
    assert "Jennifer Pan" in ent.canonical_name
    assert ent.requires_operator_confirmation is True
    assert ent.status == "UNRESOLVED_CANDIDATE_ONLY"


def test_project_roster_full_name_resolves_cleanly(tmp_path: Path):
    """Verifies that project roster full-name lookup resolves cleanly with requires_operator_confirmation=False."""
    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Jennifer Pan", project_name="project_lens")

    assert ent is not None
    assert ent.canonical_name == "Jennifer Pan"
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False


def test_nickname_single_token_requires_operator_confirmation(tmp_path: Path):
    """Verifies that single-token nickname in GraphDB requires operator confirmation."""
    db_path = tmp_path / "graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            node_type TEXT DEFAULT 'person',
            active INTEGER DEFAULT 1,
            properties_json TEXT
        );
        CREATE TABLE IF NOT EXISTS assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            confidence_score REAL,
            status TEXT,
            observed_at TEXT,
            stale_after TEXT,
            currentness_state TEXT,
            superseded_by_assertion_id TEXT,
            validation_state TEXT,
            valid_from TEXT
        );
    """)
    cur.execute(
        "INSERT INTO node (node_id, canonical_name, display_name, description, properties_json) VALUES (?, ?, ?, ?, ?)",
        (
            "person_dan_sabet",
            "Daniel (Dan) Sabet",
            "Dan Sabet",
            "Project Lead at Apex Global.",
            json.dumps({"org": "Apex Global Enterprises (NEA)", "role": "Project Lead"}),
        ),
    )
    conn.commit()
    conn.close()

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    ent = resolver.resolve("Dan")

    assert ent is not None
    assert "Dan" in ent.canonical_name or "Daniel" in ent.canonical_name
    assert ent.requires_operator_confirmation is True
    assert ent.status == "UNRESOLVED_CANDIDATE_ONLY"

