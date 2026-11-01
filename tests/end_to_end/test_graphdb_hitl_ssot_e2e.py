"""
tests/end_to_end/test_graphdb_hitl_ssot_e2e.py

End-to-end integration test verifying GraphDB as Single Source of Truth (SSOT)
for pipeline HITL names, titles, and roles across ingestion, interactive operator
ratification, GraphDB persistence, competing title supersession, and clean convergence.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.speaker_role_coverage import (
    _evaluate_single_role_fact,
    _inspect_person_role_assertions,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
from dual_pipeline.transcript_metadata_core.entity_resolver_write import persist_validated_role
from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
    HITLRequiredError,
)
from storage.connection import create_sqlite_conn


@pytest.fixture
def ssot_e2e_db() -> Path:
    """Provisions an isolated SQLite database seeded with multi-title Amit Ayer assertions."""
    temp_dir = Path(tempfile.mkdtemp(prefix="ssot_e2e_db_"))
    db_path = temp_dir / "canonical_graph.sqlite"

    conn = create_sqlite_conn(db_path, readonly=False)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL")

    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            properties_json TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
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
            confidence_label TEXT DEFAULT 'HIGH',
            currentness_state TEXT NOT NULL DEFAULT 'current',
            validation_state TEXT NOT NULL DEFAULT 'verified',
            valid_from TEXT,
            valid_to TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (subject_node_id) REFERENCES node(node_id)
        )
    """)

    cur.execute("""
        CREATE TABLE alias (
            alias_id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (node_id) REFERENCES node(node_id)
        )
    """)

    # Seed person Amit Ayer and role group nodes
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active) "
        "VALUES ('person_amit_ayer', 'person', 'Amit Ayer', 'Amit Ayer', 'PartnerAdvisory Practice Leader', ?, 1)",
        (json.dumps({"role": "Lead Enterprise Architect"}),),
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active) "
        "VALUES ('role_lcp', 'role_group', 'Lead Client Partner', 'Lead Client Partner', 'Role group', 1)"
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active) "
        "VALUES ('role_pats', 'role_group', 'Principal Architect & Technology Strategist', 'Principal Architect', 'Role group', 1)"
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active) "
        "VALUES ('role_lea', 'role_group', 'Lead Enterprise Architect', 'Lead Enterprise Architect', 'Role group', 1)"
    )

    # Seed aliases
    cur.execute("INSERT INTO alias (alias_id, node_id, alias, active) VALUES ('al_1', 'person_amit_ayer', 'Amit', 1)")
    cur.execute("INSERT INTO alias (alias_id, node_id, alias, active) VALUES ('al_2', 'person_amit_ayer', 'amit.ayer@consulting.example.com', 1)")

    # Seed 3 competing role assertions
    cur.execute("""
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            confidence_score, confidence_label, currentness_state, validation_state, valid_from, scope_text
        ) VALUES (
            'asrt_lcp', 'person_amit_ayer', 'HAS_ROLE', 'role_lcp',
            0.90, 'HIGH', 'current', 'verified', '2026-01-01', 'Amit Ayer HAS_ROLE Lead Client Partner.'
        )
    """)
    cur.execute("""
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            confidence_score, confidence_label, currentness_state, validation_state, valid_from, scope_text
        ) VALUES (
            'asrt_pats', 'person_amit_ayer', 'HAS_ROLE', 'role_pats',
            0.90, 'HIGH', 'current', 'verified', '2025-06-01', 'Amit Ayer HAS_ROLE Principal Architect & Technology Strategist.'
        )
    """)
    cur.execute("""
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            confidence_score, confidence_label, currentness_state, validation_state, valid_from, scope_text
        ) VALUES (
            'asrt_lea', 'person_amit_ayer', 'HAS_ROLE', 'role_lea',
            0.50, 'MEDIUM', 'current', 'needs_review', '2024-01-01', 'Amit Ayer HAS_ROLE Lead Enterprise Architect.'
        )
    """)

    conn.commit()
    conn.close()
    return db_path


def test_e2e_graphdb_hitl_traversal_ratification_and_convergence(ssot_e2e_db: Path) -> None:
    """
    End-to-End flow:
    1. Pre-meeting ingestion detects competing active GraphDB roles for Amit Ayer (0.90 vs 0.90).
    2. C0.6 Gate triggers HITL with GraphDB-derived confidence options.
    3. Interactive operator selects 'Lead Client Partner'.
    4. Operator resolution persists to GraphDB:
       - Selected role marked current at 1.0 confidence.
       - Competing roles marked superseded with superseded_by_assertion_id set.
    5. speaker_role_coverage confirms 0 duplicate active assertions.
    6. Second meeting run resolves cleanly without HITL interruption.
    """
    db_path = ssot_e2e_db
    rt_root = db_path.parent

    # Step 1: Initial resolution indicates AMBIGUOUS_CONFLICT due to delta <= 0.30
    ent = resolve_person_entity(
        "Amit Ayer",
        runtime_root=rt_root,
        explicit_role=None,
    )
    assert ent is not None
    assert ent.status == "AMBIGUOUS_CONFLICT"
    assert ent.requires_operator_confirmation is True
    assert len(ent.candidates) == 3
    assert ent.candidates[0].confidence == 0.90
    assert ent.candidates[1].confidence == 0.90
    assert ent.candidates[2].confidence == 0.50

    # Step 2: C0.6 Gate triggers HITL when interactive resolver is absent
    gate = C06GraphDBRoleVerificationGate(fail_closed=True, repo_root=rt_root)
    context: Dict[str, Any] = {
        "metadata": {
            "graph_path": str(db_path),
            "runtime_root": str(rt_root),
            "attendees": [{"name": "Amit Ayer", "role": ""}],
        }
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(context)

    options = exc_info.value.options
    assert len(options) >= 3
    assert "90%" in options[0]["confidence_level"]
    assert "(Recommended)" in options[0]["label"]

    # Step 3 & 4: Interactive resolution with operator selecting 'Lead Client Partner'
    def mock_operator_decision(req: Dict[str, Any]) -> str:
        assert req["attendee"] == "Amit Ayer"
        return "Lead Client Partner"

    context["metadata"]["interactive_resolver"] = mock_operator_decision
    res_gate = gate.execute(context)
    assert res_gate.get("c06_role_gate_cleared") is True
    assert context["metadata"]["role_resolutions"]["Amit Ayer"] == "Lead Client Partner"

    # Step 4b: Verify persistence in isolated SQLite database
    conn = create_sqlite_conn(db_path, readonly=True)
    cur = conn.cursor()

    cur.execute("""
        SELECT assertion_id, object_node_id, confidence_score, currentness_state, validation_state, superseded_by_assertion_id
        FROM assertion
        WHERE subject_node_id = 'person_amit_ayer'
        ORDER BY currentness_state ASC
    """)
    rows = cur.fetchall()

    active_rows = [r for r in rows if r[3] in ("current", "active") and not r[5]]
    superseded_rows = [r for r in rows if r[3] == "superseded"]

    assert len(active_rows) == 1, f"Expected exactly 1 active assertion, found: {active_rows}"
    ratified_id = active_rows[0][0]
    assert active_rows[0][2] == 1.0, "Ratified assertion must have confidence 1.0"
    assert active_rows[0][4] == "verified", "Ratified assertion must have validation_state 'verified'"

    assert len(superseded_rows) == 2, f"Expected 2 superseded assertions, found: {superseded_rows}"
    for sup in superseded_rows:
        assert sup[5] == ratified_id, f"Superseded row {sup[0]} should point to {ratified_id}"

    # Step 5: Validate speaker_role_coverage
    res_obj = {"resolved_role": "Lead Client Partner", "organization": "PartnerAdvisory"}
    fact_eval = _evaluate_single_role_fact(conn, "Amit Ayer", res_obj, base_snapshot=None)
    assert fact_eval["verified_in_graph"] is True
    assert fact_eval["disposition"] in ("COMMITTED", "SUPERSEDED_CORRECTION")
    assert fact_eval["disposition_reason"] != "DUPLICATE_ACTIVE_ROLE_ASSERTION"

    conn.close()

    # Step 6: Subsequent meeting ingestion achieves clean convergence without HITL
    second_ent = resolve_person_entity(
        "Amit Ayer",
        runtime_root=rt_root,
        explicit_role=None,
    )
    assert second_ent is not None
    assert second_ent.status == "RESOLVED"
    assert second_ent.requires_operator_confirmation is False
    assert second_ent.role == "Lead Client Partner"
    assert second_ent.confidence == 1.0
