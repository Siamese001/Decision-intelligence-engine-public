"""Adversarial and edge-case testing for forward-looking executive graph schema.

Covers mandatory categories per governance rules:
1. Boundary payloads: Empty queries, null inputs, unknown stakeholder lookups.
2. Malformed input & constraints: Invalid horizon enums, invalid severity scores, constraint violations.
3. Overflow & invariant protection: Tactical assertion flooding and 30% strategic floor calculations.
4. Transaction rollback & recovery: Integrity check and clean rollbacks on malformed inserts.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.forward_horizon_synthesizer import (
    init_forward_looking_schema,
    seed_enterprise_strategic_portfolio,
    get_executive_horizon_portfolio,
)


@pytest.fixture
def clean_db():
    with tempfile.NamedTemporaryFile(suffix="_adversarial_exec.sqlite", delete=False) as tf:
        db_path = tf.name
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_fingerprint TEXT,
            subject_node_id TEXT,
            predicate TEXT NOT NULL,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            created_at TEXT NOT NULL
        );
    """)
    init_forward_looking_schema(conn)
    seed_enterprise_strategic_portfolio(conn)
    yield conn
    conn.close()
    Path(db_path).unlink(missing_ok=True)


def test_boundary_empty_and_unknown_executive_queries(clean_db):
    # Query non-existent executive
    res = get_executive_horizon_portfolio("NonExistent_Executive_999", clean_db)
    assert res["forward_horizon_counts"]["mandates_count"] == 0
    assert res["forward_horizon_counts"]["whitespace_count"] == 0
    assert res["mandates"] == []
    assert res["strategic_ratio"] == 1.0  # empty facts defaults to 1.0 safely

    # Query empty string or whitespace
    res_empty = get_executive_horizon_portfolio("   ", clean_db)
    assert isinstance(res_empty["mandates"], list)


def test_malformed_horizon_enum_constraint_violation(clean_db):
    # Inserting invalid target_horizon must fail closed per SQLite CHECK constraint
    with pytest.raises(sqlite3.IntegrityError):
        clean_db.execute(
            """
            INSERT INTO bu_strategic_mandate (
                mandate_id, bu_name, owning_executive_id, owning_executive_name,
                title, target_horizon, strategic_objective, created_at
            ) VALUES ('bad_mandate', 'BU_TEST', 'exec_1', 'Bad Exec', 'Title', 'H99_INVALID', 'Obj', '2026-10-18')
            """
        )


def test_malformed_severity_score_constraint_violation(clean_db):
    # Severity score must be between 1 and 5
    with pytest.raises(sqlite3.IntegrityError):
        clean_db.execute(
            """
            INSERT INTO cross_bu_tension (
                tension_id, source_bu, target_bu, source_executive_id, target_executive_id,
                tension_category, description, november_reorg_impact, mitigation_strategy,
                severity_score, created_at
            ) VALUES ('bad_tension', 'BU_A', 'BU_B', 'e1', 'e2', 'BUDGET_ALLOCATION', 'Desc', 'Impact', 'Mit', 10, '2026-10-18')
            """
        )


def test_tactical_overflow_and_strategic_floor_invariant(clean_db):
    # Setup test node for flood test
    clean_db.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, created_at) VALUES ('test_flooded_exec', 'stakeholder', 'Flooded Exec', '2026-10-18')"
    )
    # Flood with 50 tactical H0 claims
    for i in range(50):
        clean_db.execute(
            f"INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, created_at) VALUES ('claim_{i}', 'test_flooded_exec', 'HAS_CLAIM_TEXT', 'Tactical note {i}', '2026-10-18')"
        )
    clean_db.commit()

    # Without any mandates, strategic ratio must drop below 0.30 floor
    res = get_executive_horizon_portfolio("Flooded Exec", clean_db)
    assert res["meets_strategic_floor"] is False
    assert res["strategic_ratio"] < 0.30

    # Add 15 strategic mandates and 15 whitespace theses (30 strategic items)
    for i in range(15):
        clean_db.execute(
            f"""
            INSERT INTO bu_strategic_mandate (
                mandate_id, bu_name, owning_executive_id, owning_executive_name,
                title, target_horizon, strategic_objective, created_at
            ) VALUES ('flood_mandate_{i}', 'FLOOD_BU', 'test_flooded_exec', 'Flooded Exec', 'Mandate {i}', 'H1_90_DAY', 'Obj', '2026-10-18')
            """
        )
    clean_db.commit()

    # Now with 15 strategic vs 25 tactical (capped query limit), strategic ratio must exceed 30%
    res_boosted = get_executive_horizon_portfolio("Flooded Exec", clean_db)
    assert res_boosted["strategic_ratio"] >= 0.30
    assert res_boosted["meets_strategic_floor"] is True


def test_database_integrity_and_clean_rollback(clean_db):
    cursor = clean_db.cursor()
    cursor.execute("PRAGMA integrity_check;")
    status = cursor.fetchone()[0]
    assert status == "ok"

    # Transaction rollback safety
    try:
        clean_db.execute("BEGIN TRANSACTION;")
        clean_db.execute(
            "INSERT INTO bu_strategic_mandate (mandate_id, bu_name, owning_executive_id, owning_executive_name, title, target_horizon, strategic_objective, created_at) VALUES ('m_trans', 'BU', 'e', 'E', 'T', 'H1_90_DAY', 'O', '2026-10-18')"
        )
        # Force intentional failure
        clean_db.execute(
            "INSERT INTO bu_strategic_mandate (mandate_id, bu_name, owning_executive_id, owning_executive_name, title, target_horizon, strategic_objective, created_at) VALUES ('m_trans', 'BU', 'e', 'E', 'T', 'H_INVALID', 'O', '2026-10-18')"
        )
    except sqlite3.IntegrityError:
        clean_db.rollback()

    cursor.execute("SELECT COUNT(*) FROM bu_strategic_mandate WHERE mandate_id='m_trans'")
    assert cursor.fetchone()[0] == 0
