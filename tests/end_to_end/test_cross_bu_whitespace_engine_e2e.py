"""End-to-end verification of Cross-BU White-Space & November Reorg Tension Engine.

Verifies:
1. Proactive strategic briefings across PGD (Mark Lemasney / KK Venkataraman),
   APEX (Timothy Oliver / Faisal Bhatti), One Tech Core (Ben Miron / logan Miller / Chakra),
   One Tech Products (Rich Argentieri / Matt Barrows), and Dominion M&A (Ashish Gupta).
2. Every executive receives top-down BU mandates, November reorg friction alerts,
   and at least 3 forward-looking white-space plays ($1M-$10M+).
3. The query router suppresses ground-level L4 bug noise and guarantees >=30% strategic foresight.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.cross_bu_whitespace_engine import CrossBUWhiteSpaceEngine
from dual_pipeline.query_router import route_executive_briefing


@pytest.fixture
def sandbox_db():
    with tempfile.NamedTemporaryFile(suffix="_ws_engine_test.sqlite", delete=False) as tf:
        db_path = tf.name
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
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
    # Seed noisy L4 bug assertion to test suppression
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, created_at) VALUES ('person_rich_argentieri', 'stakeholder', 'Richard Argentieri', '2026-10-18')"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, created_at) VALUES ('a_noisy_ocr', 'person_rich_argentieri', 'HAS_CLAIM_TEXT', 'LENS OCR confidence 82.4% on serial table defect #401', '2026-10-18')"
    )
    conn.commit()

    yield conn
    conn.close()
    Path(db_path).unlink(missing_ok=True)


def test_mark_lemasney_pgd_strategic_briefing_e2e(sandbox_db):
    engine = CrossBUWhiteSpaceEngine(sandbox_db)
    brief = engine.generate_strategic_executive_briefing("Mark Lemasney")

    assert brief["executive_name"] == "Mark Lemasney"
    assert brief["primary_bu"] == "POWER_GENERATION_DIVISION"
    assert brief["reporting_leader"] == "John Ketchum"
    assert brief["meets_strategic_floor"] is True
    assert brief["strategic_foresight_ratio"] >= 0.30

    # Top-Down BU Mandates
    assert len(brief["top_down_mandates"]) >= 1
    assert any("Fleet Reliability" in m["title"] for m in brief["top_down_mandates"])

    # Reorg Tensions
    assert len(brief["cross_bu_reorg_tensions"]) >= 1
    assert any("ONE_TECH_PRODUCTS" in t.get("target_bu", "") for t in brief["cross_bu_reorg_tensions"])

    # Ranked Proactive White-Space Plays (At least 3, ranked by SOW value descending)
    assert len(brief["proactive_whitespace_plays"]) >= 3
    sow_values = [p.get("sow_value", 0.0) for p in brief["proactive_whitespace_plays"]]
    assert sow_values == sorted(sow_values, reverse=True)
    assert any("Asset Performance Digital Twin" in p["title"] for p in brief["proactive_whitespace_plays"])


def test_timothy_oliver_fpl_development_briefing_e2e(sandbox_db):
    engine = CrossBUWhiteSpaceEngine(sandbox_db)
    brief = engine.generate_strategic_executive_briefing("Timothy Oliver")

    assert brief["executive_name"] == "Timothy Oliver"
    assert brief["primary_bu"] == "FLORIDA_POWER_LIGHT"
    assert brief["meets_strategic_floor"] is True
    assert len(brief["top_down_mandates"]) >= 1
    assert any("Storm Hardening" in m["title"] for m in brief["top_down_mandates"])
    assert any("Agentic Grid" in p["title"] for p in brief["proactive_whitespace_plays"])


def test_one_tech_core_and_products_leadership_briefings_e2e(sandbox_db):
    engine = CrossBUWhiteSpaceEngine(sandbox_db)

    # Ben Miron (CIO)
    brief_ben = engine.generate_strategic_executive_briefing("Ben Miron")
    assert brief_ben["primary_bu"] == "ONE_TECH_CORE"
    assert "Chief Information Officer" in brief_ben["one_tech_role"]
    assert any("GenAI Token Governance" in p["title"] for p in brief_ben["proactive_whitespace_plays"])

    # Rich Argentieri (CPO)
    brief_rich = engine.generate_strategic_executive_briefing("Richard Argentieri")
    assert brief_rich["primary_bu"] == "ONE_TECH_PRODUCTS"
    assert "Chief Product Officer" in brief_rich["one_tech_role"]
    assert any("Commercial Asset" in p["title"] for p in brief_rich["proactive_whitespace_plays"])


def test_operational_l4_bug_suppression_and_query_router_e2e(sandbox_db):
    # Route through query_router facade with L4 suppression
    brief = route_executive_briefing("Richard Argentieri", conn=sandbox_db, suppress_l4_delivery_noise=True)

    assert brief["meets_strategic_floor"] is True
    assert brief["strategic_foresight_ratio"] >= 0.30

    # Confirm the noisy OCR bug was filtered out of grounded context facts
    raw_claims = [f.get("claim", "") for f in brief["grounded_context_facts"]]
    assert not any("LENS OCR confidence 82.4%" in c for c in raw_claims)
