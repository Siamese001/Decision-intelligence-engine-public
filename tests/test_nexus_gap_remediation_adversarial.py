"""Adversarial and edge-case tests for Project NEXUS gap remediation."""
from __future__ import annotations

import sqlite3

from tools.tier2_dedup_core.nexus_gap_remediation import (
    _consolidate_nexus_duplicate_sources,
    _ingest_market_trends_1004,
    _promote_nexus_candidates,
)


def _create_isolated_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY, approval_reference TEXT, approval_type TEXT,
            scope_json TEXT, proposal_sha256 TEXT, approval_statement_sha256 TEXT,
            approved_at TEXT, expires_at TEXT, status TEXT, approved_by TEXT,
            release_version TEXT, active INTEGER, created_at TEXT
        );
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
            evidence_id TEXT PRIMARY KEY, evidence_fingerprint TEXT, source_id TEXT,
            source_version_id TEXT, locator_kind TEXT, locator_value TEXT, section_name TEXT,
            evidence_directness TEXT, source_authority_rank INTEGER, extraction_confidence REAL,
            corroboration_group TEXT, location_status TEXT, created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT, subject_node_id TEXT,
            predicate TEXT, object_node_id TEXT, object_literal TEXT, object_literal_type TEXT,
            assertion_type TEXT, atomicity_state TEXT, scope_text TEXT, speaker_or_author TEXT,
            primary_source_id TEXT, extraction_run_id TEXT, extraction_method TEXT,
            origin_type TEXT, confidence_label TEXT, confidence_score REAL,
            validation_state TEXT, currentness_state TEXT, valid_from TEXT,
            date_precision TEXT, date_status TEXT, recorded_at TEXT, resolution_state TEXT,
            canonical_status TEXT, canonical_eligible INTEGER, retrieval_eligible INTEGER,
            materiality TEXT, classification TEXT, allowed_audiences_json TEXT,
            hitl_required INTEGER, validation_priority TEXT, qualifiers_json TEXT,
            legacy_record_type TEXT, legacy_record_ids_json TEXT, created_at TEXT,
            updated_at TEXT, approval_reference TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT,
            independent_corroboration INTEGER, created_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, object_type TEXT, object_id TEXT,
            title TEXT, body TEXT, canonical_status TEXT, currentness_state TEXT,
            retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT, updated_at TEXT
        );
        CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, source_id TEXT);
        """
    )


def test_boundary_empty_database_consolidation() -> None:
    """Category 1 (Boundary payload): Empty source table produces 0 merges without error."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_isolated_schema(conn)
    assert _consolidate_nexus_duplicate_sources(conn) == 0
    conn.close()


def test_idempotent_re_ingestion_and_promotion() -> None:
    """Category 6 (Rollback & recovery / idempotent re-ingestion): Re-running 10-04 ingestion and promotion is idempotent."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_isolated_schema(conn)
    first = _promote_nexus_candidates(conn, "2026-10-25T20:00:00Z")
    assert first["ingested_1004_assertions"] == 3
    assert first["promoted_candidates"] == 3

    second = _promote_nexus_candidates(conn, "2026-10-25T20:05:00Z")
    assert second["ingested_1004_assertions"] == 0
    assert second["promoted_candidates"] == 0
    assert _ingest_market_trends_1004(conn, "2026-10-25T20:10:00Z") == 0
    conn.close()


def test_invariant_bypass_unrelated_sources_untouched() -> None:
    """Category 5 (Invariant bypass attempt): Candidate assertions from non-Nexus sources remain unpromoted."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_isolated_schema(conn)
    conn.execute("INSERT INTO source (source_id) VALUES ('source-unrelated-external')")
    conn.execute(
        "INSERT INTO assertion (assertion_id, primary_source_id, canonical_status, canonical_eligible, retrieval_eligible) "
        "VALUES ('a-unrelated', 'source-unrelated-external', 'candidate', 0, 0)"
    )
    _promote_nexus_candidates(conn, "2026-10-25T20:00:00Z")
    st, elig = conn.execute(
        "SELECT canonical_status, canonical_eligible FROM assertion WHERE assertion_id='a-unrelated'"
    ).fetchone()
    assert st == "candidate"
    assert elig == 0
    conn.close()
