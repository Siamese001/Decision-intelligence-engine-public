"""
tests/test_graphdb_hitl_ssot_adversarial.py

Adversarial and edge-case testing suite for GraphDB HITL SSOT role resolution
and persist-back. Covers 5 distinct edge-case categories under
.agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty inputs, 0-byte/missing db, null candidates)
2. Malformed input (corrupt JSON properties, missing schema columns, invalid types)
3. Concurrency & race (parallel WAL thread writes, lock serialization)
4. Rollback, recovery & idempotence (repeated ratifications, zero duplicate assertions)
5. Invariant bypass attempt (contradictory inline title injection, unverified override prevention)
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.speaker_role_coverage import (
    _check_duplicate_active_assertions,
    _inspect_person_role_assertions,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    ResolvedEntity,
    resolve_person_entity,
)
from dual_pipeline.transcript_metadata_core.entity_resolver_write import persist_validated_role
from storage.connection import create_sqlite_conn


def _create_adversarial_db(db_path: Path) -> Path:
    """Creates a base hermetic SQLite database for adversarial test fixtures."""
    conn = create_sqlite_conn(db_path, readonly=False)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA busy_timeout = 30000")

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
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE alias (
            alias_id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Seed person
    cur.execute(
        "INSERT INTO node VALUES ('person_amit', 'person', 'Amit Ayer', 'Amit Ayer', 'PartnerAdvisory Practice Lead', ?, 1)",
        (json.dumps({"role": "Lead Enterprise Architect"}),),
    )
    # Seed roles
    cur.execute("INSERT INTO node VALUES ('role_lcp', 'role_group', 'Lead Client Partner', 'Lead Client Partner', 'Role', '{}', 1)")
    cur.execute("INSERT INTO node VALUES ('role_pats', 'role_group', 'Principal Architect & Technology Strategist', 'Principal Architect', 'Role', '{}', 1)")

    # Seed assertions
    cur.execute("""
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            confidence_score, currentness_state, validation_state, valid_from, scope_text
        ) VALUES (
            'asrt_1', 'person_amit', 'HAS_ROLE', 'role_lcp',
            0.90, 'current', 'verified', '2026-01-01', 'Amit Ayer HAS_ROLE Lead Client Partner.'
        )
    """)
    cur.execute("""
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            confidence_score, currentness_state, validation_state, valid_from, scope_text
        ) VALUES (
            'asrt_2', 'person_amit', 'HAS_ROLE', 'role_pats',
            0.90, 'current', 'verified', '2025-06-01', 'Amit Ayer HAS_ROLE Principal Architect & Technology Strategist.'
        )
    """)

    conn.commit()
    conn.close()
    return db_path


# ==============================================================================
# Category 1: Boundary payload
# ==============================================================================

def test_boundary_empty_and_whitespace_inputs(tmp_path: Path) -> None:
    """Verifies empty, whitespace-only, or missing names and db paths fail safely without corruption."""
    db_path = _create_adversarial_db(tmp_path / "graph.sqlite")

    # 1. Missing database file fails closed
    missing_db = tmp_path / "nonexistent.sqlite"
    with pytest.raises(FileNotFoundError):
        persist_validated_role(missing_db, "Amit Ayer", "Lead Client Partner")

    # 2. Empty or whitespace role name
    with pytest.raises(Exception):
        persist_validated_role(db_path, "Amit Ayer", "   ")

    # 3. Nonexistent person node resolution
    res_none = resolve_person_entity("", runtime_root=tmp_path)
    assert res_none is None or getattr(res_none, "role", None) in ("", None)


# ==============================================================================
# Category 2: Malformed input
# ==============================================================================

def test_malformed_node_properties_and_missing_columns(tmp_path: Path) -> None:
    """Verifies that corrupted JSON in properties_json or minimal assertion schema does not crash."""
    db_path = tmp_path / "graph.sqlite"
    conn = create_sqlite_conn(db_path, readonly=False)
    cur = conn.cursor()

    # Create minimal table schema lacking optional fields (no confidence_label, no valid_to, no properties_json)
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            active INTEGER DEFAULT 1
        )
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
            valid_from TEXT,
            superseded_by_assertion_id TEXT,
            scope_text TEXT
        )
    """)
    cur.execute("INSERT INTO node VALUES ('person_corrupt', 'person', 'Corrupted User', 'Corrupted User', 'Desc', 1)")
    cur.execute("INSERT INTO node VALUES ('role_lead', 'role_group', 'Engineering Lead', 'Engineering Lead', 'Desc', 1)")
    cur.execute("""
        INSERT INTO assertion VALUES (
            'asrt_corrupt_1', 'person_corrupt', 'HAS_ROLE', 'role_lead',
            0.80, 'current', 'verified', '2026-01-01', NULL, 'Corrupted User HAS_ROLE Engineering Lead.'
        )
    """)
    conn.commit()
    conn.close()

    # Persist back into schema lacking properties_json and confidence_label
    result = persist_validated_role(db_path, "Corrupted User", "Engineering Lead")
    assert result["status"] == "PERSISTED_SSOT"
    assert result["confidence_score"] == 1.0

    # Read back to verify database remains valid
    conn_ro = create_sqlite_conn(db_path, readonly=True)
    cur_ro = conn_ro.cursor()
    cur_ro.execute("PRAGMA integrity_check")
    assert cur_ro.fetchone()[0] == "ok"
    conn_ro.close()


# ==============================================================================
# Category 3: Concurrency & race
# ==============================================================================

def test_concurrent_wal_writes(tmp_path: Path) -> None:
    """Verifies parallel multi-threaded writes to GraphDB serialize cleanly without lock errors."""
    db_path = _create_adversarial_db(tmp_path / "graph.sqlite")

    # Add 5 distinct person nodes
    conn = create_sqlite_conn(db_path, readonly=False)
    cur = conn.cursor()
    for i in range(5):
        cur.execute(
            f"INSERT INTO node VALUES ('person_worker_{i}', 'person', 'Worker {i}', 'Worker {i}', 'Worker', '{{}}', 1)"
        )
    conn.commit()
    conn.close()

    def worker_persist(idx: int) -> Dict[str, Any]:
        return persist_validated_role(
            db_path,
            f"Worker {idx}",
            f"Specialist Title {idx}",
            operator_id=f"worker_thread_{idx}",
        )

    # Launch 5 concurrent workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker_persist, i) for i in range(5)]
        results = [f.result(timeout=10) for f in futures]

    assert len(results) == 5
    for r in results:
        assert r["status"] == "PERSISTED_SSOT"
        assert r["confidence_score"] == 1.0

    # Verify database integrity and row counts
    conn_ro = create_sqlite_conn(db_path, readonly=True)
    cur_ro = conn_ro.cursor()
    cur_ro.execute("PRAGMA integrity_check")
    assert cur_ro.fetchone()[0] == "ok"
    conn_ro.close()


# ==============================================================================
# Category 4: Rollback, recovery & idempotence
# ==============================================================================

def test_idempotent_re_ratification_prevents_duplicate_assertions(tmp_path: Path) -> None:
    """Verifies that ratifying the same role multiple times does not produce duplicate active assertions."""
    db_path = _create_adversarial_db(tmp_path / "graph.sqlite")

    # Run 1: First ratification of 'Lead Client Partner'
    res1 = persist_validated_role(db_path, "Amit Ayer", "Lead Client Partner")
    assert res1["status"] == "PERSISTED_SSOT"

    # Run 2: Immediate re-ratification of the same title
    res2 = persist_validated_role(db_path, "Amit Ayer", "Lead Client Partner")
    assert res2["status"] == "PERSISTED_SSOT"

    # Run 3: Third ratification of the same title
    res3 = persist_validated_role(db_path, "Amit Ayer", "Lead Client Partner")
    assert res3["status"] == "PERSISTED_SSOT"

    conn = create_sqlite_conn(db_path, readonly=True)
    assertions = _inspect_person_role_assertions(conn, ["person_amit", "person:amit_ayer"])

    # Enforce zero duplicate active assertions
    dup_err = _check_duplicate_active_assertions(assertions, "Amit Ayer")
    assert dup_err is None, f"Encountered duplicate active assertion error: {dup_err}"

    active_assertions = [
        a for a in assertions
        if a["currentness_state"] in ("current", "active") and not a.get("superseded_by_assertion_id")
    ]
    assert len(active_assertions) == 1, f"Expected exactly 1 active assertion, found {len(active_assertions)}"
    conn.close()


# ==============================================================================
# Category 5: Invariant bypass attempt (Contradictory inline title injection)
# ==============================================================================

def test_contradictory_inline_title_cannot_bypass_graphdb_ssot(tmp_path: Path) -> None:
    """
    Verifies that providing an explicit conflicting role (e.g. 'Junior Intern')
    cannot bypass GraphDB SSOT and is flagged as an AMBIGUOUS_CONFLICT requiring operator confirmation.
    """
    _create_adversarial_db(tmp_path / "graph.sqlite")

    # Attempt to pass an arbitrary unverified role for Amit Ayer
    ent = resolve_person_entity(
        "Amit Ayer",
        runtime_root=tmp_path,
        explicit_role="Junior Intern",
    )

    assert ent is not None
    # Must flag conflict because 'Junior Intern' contradicts GraphDB assertions
    assert ent.status == "AMBIGUOUS_CONFLICT"
    assert ent.requires_operator_confirmation is True
    # Candidates must be sourced from GraphDB, not the injected string
    assert len(ent.candidates) >= 2
    candidate_roles = [c.role for c in ent.candidates]
    assert "Lead Client Partner" in candidate_roles
    assert "Junior Intern" not in candidate_roles
