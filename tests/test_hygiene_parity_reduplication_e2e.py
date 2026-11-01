"""End-to-end regression test proving document parity does not re-inject merged entities or revive superseded assertions."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.apply_graph_hygiene_remediation import _remediate_duplicate_assertions, _remediate_entity_merges
from tools.tier2_dedup_core.document_graph_parity_core import (
    _ensure_node_exists,
    promote_all_candidates_and_empty_sources,
)


def _init_minimal_hygiene_schema(db_path: Path) -> sqlite3.Connection:
    conn = GovernedStorageContext.connect(db_path, readonly=False)
    conn.row_factory = Row
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            normalized_name TEXT,
            description TEXT,
            status TEXT DEFAULT 'canonical',
            confidence_score REAL DEFAULT 0.9,
            created_at TEXT,
            updated_at TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT,
            validation_state TEXT,
            currentness_state TEXT,
            approval_state TEXT,
            rag_state TEXT,
            resolution_state TEXT,
            retrieval_eligibility TEXT,
            publication_state TEXT,
            review_state TEXT,
            updated_at TEXT
        );
        CREATE TABLE entity_merge_event (
            merge_event_id TEXT PRIMARY KEY,
            canonical_node_id TEXT,
            event_type TEXT,
            status TEXT,
            approval_reference TEXT,
            reason TEXT,
            created_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE entity_redirect (
            from_node_id TEXT PRIMARY KEY,
            to_node_id TEXT,
            merge_event_id TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT,
            approval_type TEXT,
            scope_json TEXT,
            proposal_sha256 TEXT,
            approval_statement_sha256 TEXT,
            approved_at TEXT,
            expires_at TEXT,
            status TEXT,
            approved_by TEXT,
            release_version TEXT,
            active INTEGER,
            created_at TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT,
            source_date TEXT,
            notes TEXT,
            cold_archive_ref TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY,
            source_id TEXT,
            version_number INTEGER,
            source_fingerprint TEXT,
            content_hash TEXT,
            source_date_normalized TEXT,
            source_date_precision TEXT,
            source_date_status TEXT,
            authority_rank INTEGER,
            scope_text TEXT,
            posture_text TEXT,
            created_at TEXT
        );
        CREATE TABLE extraction_run (
            extraction_run_id TEXT PRIMARY KEY,
            algorithm_version TEXT,
            baseline_graph_sha256 TEXT,
            input_manifest_sha256 TEXT,
            started_at TEXT,
            completed_at TEXT,
            status TEXT,
            deterministic INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            evidence_fingerprint TEXT,
            source_id TEXT,
            source_version_id TEXT,
            locator_kind TEXT,
            locator_value TEXT,
            section_name TEXT,
            direct_quote TEXT,
            evidence_directness TEXT,
            source_authority_rank INTEGER,
            extraction_confidence REAL,
            corroboration_group TEXT,
            location_status TEXT,
            created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_fingerprint TEXT,
            subject_node_id TEXT,
            predicate TEXT,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            object_literal_type TEXT,
            assertion_type TEXT,
            atomicity_state TEXT,
            scope_text TEXT,
            speaker_or_author TEXT,
            primary_source_id TEXT,
            extraction_run_id TEXT,
            extraction_method TEXT,
            origin_type TEXT,
            confidence_label TEXT,
            confidence_score REAL,
            validation_state TEXT,
            currentness_state TEXT,
            valid_from TEXT,
            valid_to TEXT,
            date_precision TEXT,
            date_status TEXT,
            recorded_at TEXT,
            resolution_state TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            materiality TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            hitl_required INTEGER,
            validation_priority TEXT,
            qualifiers_json TEXT,
            legacy_record_type TEXT,
            legacy_record_ids_json TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            approval_reference TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            independent_corroboration INTEGER DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TABLE search_document (
            assertion_id TEXT PRIMARY KEY,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            updated_at TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            description TEXT,
            evidence_posture TEXT,
            confidence REAL,
            materiality TEXT,
            validation_priority TEXT,
            validation_owner TEXT,
            hitl_required INTEGER,
            source_id TEXT,
            superseded_by TEXT,
            updated_at TEXT
        );
        CREATE VIRTUAL TABLE node_fts USING fts5(
            node_id,
            canonical_name,
            display_name,
            description
        );
        """
    )
    return conn


def test_e2e_hygiene_merges_and_superseded_assertions_survive_parity_run(tmp_path: Path) -> None:
    """Verify that after hygiene merges entities and supersedes duplicate assertions, parity runs do not undo them."""
    db_path = tmp_path / "hygiene_parity_e2e.sqlite"
    conn = _init_minimal_hygiene_schema(db_path)
    now = "2026-10-27T06:00:00Z"

    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, active) "
        "VALUES ('entity-01fec4f44c25883d5bc2f203e0a4', 'stakeholder', 'Tony Morano', 'tony morano', 'Contractor specialist', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, active) "
        "VALUES ('person:tony-morano', 'stakeholder', 'Tony Morano', 'tony morano', 'Contractor specialist', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, active) "
        "VALUES ('org_nee', 'org', 'Apex Global Enterprises', 'apex_global energy', 'Enterprise utility', 'canonical', 1)"
    )
    conn.execute("INSERT INTO source (source_id, source_name, source_date) VALUES ('src-1', 'Meeting 1', '2026-10-26')")
    conn.execute("INSERT INTO evidence_item (evidence_id, source_id, direct_quote) VALUES ('ev-1', 'src-1', 'Quote 1')")

    # Insert two assertions that become duplicates once person:tony-morano merges into entity-01fec4f44c25883d5bc2f203e0a4
    for aid, subj, ts in [
        ("asrt-winner", "entity-01fec4f44c25883d5bc2f203e0a4", "2026-10-26T12:00:00Z"),
        ("asrt-loser", "person:tony-morano", "2026-10-25T12:00:00Z"),
    ]:
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, "
            "primary_source_id, confidence_score, validation_state, currentness_state, canonical_status, "
            "canonical_eligible, retrieval_eligible, recorded_at, created_at, updated_at) "
            "VALUES (?, ?, 'AFFILIATED_WITH', 'org_nee', 'Tony Morano MEMBER_OF Apex Global Enterprises.', "
            "'src-1', 0.95, 'accepted_legacy', 'current', 'canonical', 1, 1, ?, ?, ?)",
            (aid, subj, ts, ts, ts),
        )
        conn.execute(
            "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) "
            "VALUES (?, 'ev-1', 'supports', ?)",
            (aid, ts),
        )

    pkg = {
        "strict_package_gating": True,
        "findings": [
            {
                "proposed_fix": "merge_entities",
                "raw_data": {
                    "node_id_a": "entity-01fec4f44c25883d5bc2f203e0a4",
                    "node_id_b": "person:tony-morano",
                },
            },
            {
                "proposed_fix": "deduplicate_assertions",
                "raw_data": {
                    "winner_id": "asrt-winner",
                    "loser_id": "asrt-loser",
                },
            },
        ],
    }
    sample_ids = []
    _remediate_entity_merges(conn, pkg, now, sample_ids)
    _remediate_duplicate_assertions(conn, pkg, now)
    conn.commit()

    # Now simulate wave4 document parity running _ensure_node_exists and promote_all_candidates_and_empty_sources
    resolved_id = _ensure_node_exists(conn, "person:tony-morano", "person", "Tony Morano", "Contractor", now)
    resolved_new_slug = _ensure_node_exists(conn, "person:tony_morano_new", "person", "Tony Morano", "Contractor", now)
    promote_all_candidates_and_empty_sources(conn, now)
    conn.commit()

    assert resolved_id == "entity-01fec4f44c25883d5bc2f203e0a4"
    assert resolved_new_slug == "entity-01fec4f44c25883d5bc2f203e0a4"

    active_tony = conn.execute(
        "SELECT node_id FROM node WHERE canonical_name = 'Tony Morano' AND active = 1"
    ).fetchall()
    assert len(active_tony) == 1
    assert active_tony[0][0] == "entity-01fec4f44c25883d5bc2f203e0a4"

    loser_row = conn.execute(
        "SELECT currentness_state, validation_state, retrieval_eligible, superseded_by_assertion_id "
        "FROM assertion WHERE assertion_id = 'asrt-loser'"
    ).fetchone()
    assert loser_row[0] == "historical"
    assert loser_row[1] == "superseded"
    assert loser_row[2] == 0
    assert loser_row[3] == "asrt-winner"
    conn.close()


def test_e2e_role_survivor_selection_and_full_hygiene_invariants(tmp_path: Path) -> None:
    """Verify slash-formatted role survivor preference, verified state preservation, node_fts sync, lineage backfill, and inactive-node assertion cleanup."""
    from tools.tier2_dedup_core.sidecar_vector_recovery import repair_falsely_historical_merged_assertions

    db_path = tmp_path / "hygiene_full_rca_e2e.sqlite"
    conn = _init_minimal_hygiene_schema(db_path)
    now = "2026-10-27T07:00:00Z"

    conn.execute("INSERT INTO source (source_id, source_name, source_date) VALUES ('src-1', 'Meeting 1', '2026-10-26')")
    conn.execute("INSERT INTO extraction_run (extraction_run_id, status, deterministic) VALUES ('er-1', 'completed', 1)")
    conn.execute("INSERT INTO evidence_item (evidence_id, source_id, direct_quote) VALUES ('ev-1', 'src-1', 'Quote 1')")

    # Stakeholder and two competing role nodes: slash-formatted (1 assertion) vs hyphenated (2 assertions)
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, active) "
        "VALUES ('person_amit', 'stakeholder', 'Amit Ayer', 'amit ayer', 'Role: Senior Director / Lead Enterprise Architect', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, active) "
        "VALUES ('entity_slash_role', 'role_group', 'Senior Director / Lead Enterprise Architect', 'senior director lead enterprise architect', 'Role group', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, active) "
        "VALUES ('entity_hyphen_role', 'role_group', 'Senior Director-Lead Enterprise Architect', 'senior director lead enterprise architect', 'Role group', 'canonical', 1)"
    )
    # Inactive retired node with no redirect
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, active) "
        "VALUES ('person_retired', 'stakeholder', 'Retired Person', 'retired person', 'Retired', 'dropped_or_superseded', 0)"
    )

    # HITL-verified role assertion on slash role + two legacy assertions on hyphen role
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, "
        "primary_source_id, extraction_run_id, origin_type, confidence_score, validation_state, currentness_state, canonical_status, "
        "canonical_eligible, retrieval_eligible, recorded_at, created_at, updated_at) "
        "VALUES ('asrt-hitl-amit', 'person_amit', 'HAS_ROLE_IN', 'entity_slash_role', 'Amit Ayer HAS_ROLE Senior Director / Lead Enterprise Architect.', "
        "'src-1', 'er-1', 'human_source', 1.0, 'verified', 'current', 'canonical', 1, 1, ?, ?, ?)",
        (now, now, now),
    )
    for idx in (1, 2):
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, "
            "primary_source_id, extraction_run_id, origin_type, confidence_score, validation_state, currentness_state, canonical_status, "
            "canonical_eligible, retrieval_eligible, recorded_at, created_at, updated_at) "
            "VALUES (?, 'person_amit', 'HAS_ROLE_IN', 'entity_hyphen_role', 'Amit Ayer HAS_ROLE Senior Director-Lead Enterprise Architect.', "
            "'src-1', 'er-1', 'human_source', 0.9, 'accepted_legacy', 'current', 'canonical', 1, 1, ?, ?, ?)",
            (f"asrt-hyphen-{idx}", now, now, now),
        )
    # Non-legacy assertion missing extraction_run_id + assertion on inactive node
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, "
        "primary_source_id, extraction_run_id, origin_type, confidence_score, validation_state, currentness_state, canonical_status, "
        "canonical_eligible, retrieval_eligible, recorded_at, created_at, updated_at) "
        "VALUES ('assertion-sherpa-edge-1', 'person_amit', 'HAS_CLAIM_TEXT', 'Claim', 'Claim', "
        "'src-1', NULL, 'human_source', 0.9, 'accepted_legacy', 'current', 'canonical', 1, 1, ?, ?, ?)",
        (now, now, now),
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, "
        "primary_source_id, extraction_run_id, origin_type, confidence_score, validation_state, currentness_state, canonical_status, "
        "canonical_eligible, retrieval_eligible, recorded_at, created_at, updated_at) "
        "VALUES ('asrt-on-retired', 'person_retired', 'HAS_CLAIM_TEXT', 'Old claim', 'Old claim', "
        "'src-1', 'er-1', 'human_source', 0.9, 'accepted_legacy', 'current', 'canonical', 1, 1, ?, ?, ?)",
        (now, now, now),
    )
    for aid in ("asrt-hitl-amit", "asrt-hyphen-1", "asrt-hyphen-2", "assertion-sherpa-edge-1", "asrt-on-retired"):
        conn.execute("INSERT INTO assertion_evidence VALUES (?, 'ev-1', 'supports', 0, ?)", (aid, now))

    pkg = {
        "strict_package_gating": True,
        "findings": [
            {
                "proposed_fix": "merge_entities",
                "raw_data": {"node_id_a": "entity_hyphen_role", "node_id_b": "entity_slash_role"},
            }
        ],
    }
    _remediate_entity_merges(conn, pkg, now, [])
    promote_all_candidates_and_empty_sources(conn, now)
    repair_falsely_historical_merged_assertions(conn, now)
    conn.commit()

    # 1. Clean slash-formatted role name must survive
    active_roles = conn.execute(
        "SELECT canonical_name FROM node WHERE node_type = 'role_group' AND active = 1"
    ).fetchall()
    assert [r[0] for r in active_roles] == ["Senior Director / Lead Enterprise Architect"]

    # 2. HITL assertion must retain validation_state = 'verified'
    hitl_state = conn.execute(
        "SELECT validation_state, currentness_state FROM assertion WHERE assertion_id = 'asrt-hitl-amit'"
    ).fetchone()
    assert hitl_state[0] == "verified"
    assert hitl_state[1] == "current"

    # 3. node_fts count must equal node count, and all active nodes must have node_state
    assert conn.execute("SELECT count(*) FROM node_fts").fetchone()[0] == conn.execute("SELECT count(*) FROM node").fetchone()[0]
    assert conn.execute("SELECT count(*) FROM node WHERE active = 1 AND node_id NOT IN (SELECT node_id FROM node_state)").fetchone()[0] == 0

    # 4. Non-legacy assertion must have extraction_run_id backfilled, and inactive-node assertion must be historical
    assert conn.execute("SELECT count(*) FROM assertion WHERE origin_type != 'legacy_migration' AND extraction_run_id IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT currentness_state FROM assertion WHERE assertion_id = 'asrt-on-retired'").fetchone()[0] == "historical"
    conn.close()

