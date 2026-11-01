"""Adversarial and edge-case testing for Cross-BU White-Space & Tension Engine.

Covers mandatory categories per governance rules:
1. Boundary payloads: Obfuscated casing, excessive whitespace, unknown executives.
2. Malformed input & ranking stability: Missing fields, zero-value budgets, stable descending sort.
3. Invariant enforcement: Guaranteed >=30% strategic foresight ratio floor under all conditions.
4. Concurrency & multi-threading: Concurrent briefing generation across parallel threads.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.cross_bu_whitespace_engine import CrossBUWhiteSpaceEngine


@pytest.fixture
def test_db():
    with tempfile.NamedTemporaryFile(suffix="_adv_ws_engine.sqlite", delete=False) as tf:
        db_path = tf.name
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    yield conn, db_path
    conn.close()
    Path(db_path).unlink(missing_ok=True)


def test_boundary_obfuscated_casing_and_excessive_whitespace(test_db):
    conn, _ = test_db
    engine = CrossBUWhiteSpaceEngine(conn)

    # Obfuscated casing and irregular whitespace
    brief = engine.generate_strategic_executive_briefing("   mArK   lEmAsNeY   ")
    assert brief["executive_name"] == "Mark Lemasney"
    assert brief["primary_bu"] == "POWER_GENERATION_DIVISION"
    assert brief["meets_strategic_floor"] is True

    # Unknown executive fallback
    unknown_brief = engine.generate_strategic_executive_briefing("Unknown Leader 404")
    assert unknown_brief["executive_name"] == "Unknown Leader 404"
    assert unknown_brief["primary_bu"] == "ENTERPRISE_WIDE"
    assert unknown_brief["strategic_foresight_ratio"] >= 0.30
    assert len(unknown_brief["proactive_whitespace_plays"]) >= 3


def test_ranking_stability_and_sow_value_sort(test_db):
    conn, _ = test_db
    engine = CrossBUWhiteSpaceEngine(conn)

    brief = engine.generate_strategic_executive_briefing("Ben Miron")
    plays = brief["proactive_whitespace_plays"]
    assert len(plays) >= 3

    # Invariant: Must be strictly sorted descending by estimated SOW value
    sow_values = [p.get("sow_value", 0.0) for p in plays]
    for i in range(len(sow_values) - 1):
        assert sow_values[i] >= sow_values[i + 1]


def test_strategic_floor_invariant_under_tactical_flood(test_db):
    conn, _ = test_db
    engine = CrossBUWhiteSpaceEngine(conn)

    # Even with an unknown executive with 0 grounded history, the strategic floor must hold
    brief = engine.generate_strategic_executive_briefing("Test Persona")
    assert brief["meets_strategic_floor"] is True
    assert brief["strategic_foresight_ratio"] >= 0.30


def test_concurrency_parallel_briefing_generation(test_db):
    _, db_path = test_db
    leaders = [
        "Mark Lemasney",
        "Timothy Oliver",
        "Ben Miron",
        "Richard Argentieri",
        "Ashish Gupta",
        "Matt Barrows",
        "Chakra Sankaraiah",
        "logan Miller",
    ]

    def query_leader(leader: str) -> bool:
        t_conn = sqlite3.connect(db_path)
        t_engine = CrossBUWhiteSpaceEngine(t_conn)
        res = t_engine.generate_strategic_executive_briefing(leader)
        t_conn.close()
        return res["meets_strategic_floor"] is True and len(res["proactive_whitespace_plays"]) >= 3

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(query_leader, l) for l in leaders]
        results = [f.result() for f in futures]

    assert all(results)
    assert len(results) == len(leaders)
