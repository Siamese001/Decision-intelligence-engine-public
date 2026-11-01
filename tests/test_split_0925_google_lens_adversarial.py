"""Adversarial and edge-case tests for 09-25-2026 Google Partner Alliances and Project LENS split."""
from __future__ import annotations

import sqlite3

from tools.tier2_dedup_core.nexus_gap_remediation import (
    _GOOGLE_0925_SOURCE_ID,
    _LENS_0925_SOURCE_ID,
    _TRACI_0925_SOURCE_ID,
    _apply_0925_split_sql,
)


def _create_empty_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT,
            source_date TEXT, authority_rank INTEGER, source_posture TEXT,
            cold_archive_ref TEXT, notes TEXT, created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY, source_id TEXT, version_number INTEGER,
            source_fingerprint TEXT, content_hash TEXT, source_date_normalized TEXT,
            source_date_precision TEXT, source_date_status TEXT, authority_rank INTEGER,
            scope_text TEXT, posture_text TEXT, created_at TEXT
        );
        CREATE TABLE extraction_run (
            extraction_run_id TEXT PRIMARY KEY, adapter_name TEXT, config_hash TEXT,
            input_hash TEXT, started_at TEXT, completed_at TEXT, status TEXT, record_count INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY, source_id TEXT, source_version_id TEXT,
            locator_kind TEXT, locator_value TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
            object_node_id TEXT, primary_source_id TEXT, extraction_run_id TEXT,
            canonical_status TEXT, updated_at TEXT
        );
        CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT);
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, assertion_id TEXT, source_id TEXT, updated_at TEXT
        );
        """
    )


def test_boundary_empty_source_tables() -> None:
    """Category 1 (Boundary payload): Empty assertion/evidence tables still register LENS source cleanly."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_empty_schema(conn)
    stats = _apply_0925_split_sql(conn, "2026-10-29T10:55:00Z")
    assert stats["rebound_lens_assertions"] == 0
    assert stats["rebound_traci_assertions"] == 0
    cnt = conn.execute("SELECT COUNT(*) FROM source WHERE source_id = ?", (_LENS_0925_SOURCE_ID,)).fetchone()[0]
    assert cnt == 1
    conn.close()


def test_idempotent_reexecution_recovery() -> None:
    """Category 2 (Rollback & recovery): Re-running _apply_0925_split_sql is 100% idempotent with zero duplicate rows."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_empty_schema(conn)
    conn.execute("INSERT INTO source (source_id) VALUES (?)", (_GOOGLE_0925_SOURCE_ID,))
    conn.execute("INSERT INTO source (source_id) VALUES (?)", (_TRACI_0925_SOURCE_ID,))
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-lens-1', ?, 'sv-old', 'timestamp', '00:36:18')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-lens-1', 'person_jen_pan', 'HAS_CLAIM_TEXT', NULL, ?, 'er-old', 'canonical', '2026-10-01')",
        (_GOOGLE_0925_SOURCE_ID,),
    )

    first = _apply_0925_split_sql(conn, "2026-10-29T10:55:00Z")
    assert first["rebound_lens_assertions"] == 1
    assert first["rebound_lens_evidence_items"] == 1

    second = _apply_0925_split_sql(conn, "2026-10-29T10:56:00Z")
    assert second["rebound_lens_assertions"] == 0
    assert second["rebound_lens_evidence_items"] == 0
    conn.close()


def test_cross_workstream_bleed_prevention() -> None:
    """Category 3 (Invariant bypass /Exact production regression): 00:24:45 Google claim is not misrouted to LENS."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_empty_schema(conn)
    conn.execute("INSERT INTO source (source_id) VALUES (?)", (_GOOGLE_0925_SOURCE_ID,))
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-google-2445', ?, 'sv-old', 'timestamp', '00:24:45')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('assertion-f40cb163a2d64980d1b42f818af6', 'workstream_lens', 'HAS_CLAIM_TEXT', NULL, ?, 'er-old', 'canonical', '2026-10-01')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('assertion-f40cb163a2d64980d1b42f818af6', 'ev-google-2445')")

    _apply_0925_split_sql(conn, "2026-10-29T10:55:00Z")
    row = conn.execute(
        "SELECT primary_source_id, subject_node_id FROM assertion WHERE assertion_id = 'assertion-f40cb163a2d64980d1b42f818af6'"
    ).fetchone()
    assert row[0] == _GOOGLE_0925_SOURCE_ID
    assert row[1] == "entity-95d04ee9d1865ad1764f327bfe81"
    conn.close()
