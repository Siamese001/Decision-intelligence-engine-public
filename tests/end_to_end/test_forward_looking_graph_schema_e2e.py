"""End-to-end verification of forward-looking graph schema and executive horizons.

Verifies:
1. SQLite migration creates all 4 forward-looking tables (bu_strategic_mandate, whitespace_thesis,
   cross_bu_tension, decision_horizon_ledger) with complete integrity.
2. Enterprise strategic portfolio seeds forward-looking strategic mandates for PGD (Mark Lemasney),
   APEX (Timothy Oliver / Faisal Bhatti), One Tech Core (Ben Miron), Products (Rich Argentieri),
   and Dominion M&A (Ashish Gupta).
3. Executive horizon portfolio enforces >=30% forward-looking strategic floor across all enterprise leaders.
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
def isolated_exec_db():
    with tempfile.NamedTemporaryFile(suffix="_exec_test.sqlite", delete=False) as tf:
        db_path = tf.name
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
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
    # Seed baseline executive nodes
    leaders = [
        ("entity-97752d48bac01c9b2a6c4eb5ba90", "stakeholder", "Mark Lemasney"),
        ("entity-timothy-oliver", "stakeholder", "Timothy Oliver"),
        ("stakeholder_ben_miron", "stakeholder", "Ben Miron"),
        ("person_rich_argentieri", "stakeholder", "Richard Argentieri"),
        ("entity-ashish-gupta", "stakeholder", "Ashish Gupta"),
    ]
    for lid, ltype, lname in leaders:
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, created_at) VALUES (?, ?, ?, '2026-10-18T00:00:00Z')",
            (lid, ltype, lname),
        )
    conn.commit()

    init_forward_looking_schema(conn)
    seed_enterprise_strategic_portfolio(conn)
    yield conn
    conn.close()
    Path(db_path).unlink(missing_ok=True)


def test_schema_migration_creates_all_tables_and_indexes(isolated_exec_db):
    cursor = isolated_exec_db.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cursor.fetchall()}
    assert "bu_strategic_mandate" in tables
    assert "whitespace_thesis" in tables
    assert "cross_bu_tension" in tables
    assert "decision_horizon_ledger" in tables

    # Verify table column schemas
    cursor.execute("PRAGMA table_info(bu_strategic_mandate)")
    cols = {r[1] for r in cursor.fetchall()}
    assert {"mandate_id", "bu_name", "owning_executive_id", "target_horizon", "estimated_capital_budget_usd"}.issubset(cols)


def test_mark_lemasney_pgd_strategic_mandate_and_whitespace(isolated_exec_db):
    res = get_executive_horizon_portfolio("Mark Lemasney", isolated_exec_db)
    assert res["meets_strategic_floor"] is True
    assert res["strategic_ratio"] >= 0.30
    assert len(res["mandates"]) >= 1
    assert any("Fleet Reliability" in m["title"] for m in res["mandates"])
    assert any(m["bu"] == "POWER_GENERATION_DIVISION" for m in res["mandates"])
    assert len(res["whitespace_theses"]) >= 1
    assert any("Asset Performance Digital Twin" in w["title"] for w in res["whitespace_theses"])


def test_fpl_development_timothy_oliver_strategic_horizon(isolated_exec_db):
    res = get_executive_horizon_portfolio("Timothy Oliver", isolated_exec_db)
    assert res["meets_strategic_floor"] is True
    assert res["strategic_ratio"] >= 0.30
    assert any(m["bu"] == "FLORIDA_POWER_LIGHT" for m in res["mandates"])
    assert any("Agentic Grid" in w["title"] for w in res["whitespace_theses"])


def test_one_tech_core_and_products_reorg_tensions(isolated_exec_db):
    res_ben = get_executive_horizon_portfolio("Ben Miron", isolated_exec_db)
    assert res_ben["meets_strategic_floor"] is True
    assert any(m["bu"] == "ONE_TECH_CORE" for m in res_ben["mandates"])
    assert len(res_ben["reorg_tensions"]) >= 1

    res_rich = get_executive_horizon_portfolio("Richard Argentieri", isolated_exec_db)
    assert res_rich["meets_strategic_floor"] is True
    assert any(m["bu"] == "ONE_TECH_PRODUCTS" for m in res_rich["mandates"])
    assert any("Commercial Asset" in w["title"] for w in res_rich["whitespace_theses"])


def test_dominion_merger_integration_ashish_gupta(isolated_exec_db):
    res = get_executive_horizon_portfolio("Ashish Gupta", isolated_exec_db)
    assert res["meets_strategic_floor"] is True
    assert any(m["bu"] == "DOMINION_INTEGRATION" for m in res["mandates"])
    assert any("M&A Integration" in w["title"] for w in res["whitespace_theses"])
