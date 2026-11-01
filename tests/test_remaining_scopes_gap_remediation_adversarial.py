"""Adversarial and edge-case tests for remaining scopes gap remediation."""
from __future__ import annotations

import sqlite3

from tools.tier2_dedup_core.remaining_scopes_gap_remediation import (
    _promote_remaining_scopes_candidates,
    _rebind_remaining_scopes_sources,
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
            extraction_run_id TEXT PRIMARY KEY, extractor_version TEXT,
            source_fingerprint TEXT, config_hash TEXT, started_at TEXT,
            completed_at TEXT, status TEXT, deterministic_mode INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY, evidence_fingerprint TEXT, source_id TEXT,
            source_version_id TEXT, locator_kind TEXT, locator_value TEXT,
            section_name TEXT, evidence_directness TEXT, source_authority_rank INTEGER,
            extraction_confidence REAL, corroboration_group TEXT, location_status TEXT,
            created_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT,
            independent_corroboration INTEGER, created_at TEXT
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
            updated_at TEXT, approval_reference TEXT,
            superseded_by_assertion_id TEXT, superseded_at TEXT
        );
        CREATE UNIQUE INDEX ux_assertion_active_tuple
            ON assertion(subject_node_id, predicate, object_node_id)
            WHERE canonical_status = 'canonical' AND currentness_state = 'current' AND object_node_id IS NOT NULL;
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, object_type TEXT, object_id TEXT,
            title TEXT, body TEXT, canonical_status TEXT, currentness_state TEXT,
            retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT, updated_at TEXT
        );
        """
    )


def test_boundary_empty_database_and_idempotent_rerun() -> None:
    """Category 1 (Boundary payload) & Category 6 (Idempotent recovery): Empty tables and repeat execution."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_isolated_schema(conn)

    dates_backfilled, rebound = _rebind_remaining_scopes_sources(conn)
    assert dates_backfilled == 0
    assert rebound == 0

    first = _promote_remaining_scopes_candidates(conn, "2026-10-26T17:01:00Z")
    assert first["sources_ingested"] == 8
    assert first["assertions_ingested"] == 24
    assert first["promoted_active_assertions"] + first["normalized_tuple_collisions"] == 24

    second = _promote_remaining_scopes_candidates(conn, "2026-10-26T17:02:00Z")
    assert second["sources_ingested"] == 0
    assert second["assertions_ingested"] == 0
    assert second["promoted_active_assertions"] == 0
    conn.close()


def test_malformed_undated_source_and_missing_evidence_collision_recovery() -> None:
    """Category 2 (Malformed input) & Category 5 (Invariant bypass / collision recovery)."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    _create_isolated_schema(conn)

    # Malformed empty string source_date and staging path
    conn.execute(
        "INSERT INTO source VALUES ('source-5603cf8f5154b25dfbf5f33c', 'dual_pipeline_intake', "
        "'Internal_Standup', '', 5, 'derivative_internal', '.staging_broken/file.md', '', '2026-09-22')"
    )
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-fallback-1', 'fp-1', 'source-5603cf8f5154b25dfbf5f33c', "
        "'sv-1', 'section', 'Sec', 'Sec', 'direct', 5, 0.9, 'grp', 'exact', '2026-09-22')"
    )
    # Two duplicate-tuple candidate assertions without assertion_evidence rows
    for idx, aid in enumerate(("a-unlinked-1", "a-unlinked-2")):
        conn.execute(
            "INSERT INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, predicate, object_node_id, "
            "primary_source_id, validation_state, currentness_state, canonical_status, canonical_eligible, retrieval_eligible) "
            "VALUES (?, ?, 'org_partner_advisory', 'SUPPORTS', 'org_fpl', 'source-5603cf8f5154b25dfbf5f33c', "
            "'needs_review', 'current', 'candidate', 0, 0)",
            (aid, f"fp-{idx}"),
        )

    dates_backfilled, rebound = _rebind_remaining_scopes_sources(conn)
    assert dates_backfilled == 1
    assert rebound == 1

    stats = _promote_remaining_scopes_candidates(conn, "2026-10-26T17:03:00Z")
    assert stats["promoted_active_assertions"] + stats["normalized_tuple_collisions"] == 26
    assert stats["normalized_tuple_collisions"] >= 1

    ev_links = conn.execute(
        "SELECT COUNT(*) FROM assertion_evidence WHERE assertion_id IN ('a-unlinked-1', 'a-unlinked-2') AND evidence_relationship = 'supports'"
    ).fetchone()[0]
    assert ev_links == 2

    hist_cnt = conn.execute(
        "SELECT COUNT(*) FROM assertion WHERE assertion_id IN ('a-unlinked-1', 'a-unlinked-2') AND currentness_state = 'historical' AND retrieval_eligible = 0"
    ).fetchone()[0]
    assert hist_cnt == 1
    conn.close()
