"""
E2E tests for Wave 6: Knowledge Graph Hygiene Proof Integrity, UWG Role Dedup & Storage Serialization (CAT-5).
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.graph_consolidation_core.repoint import (
    repoint_node_assertions,
    repoint_node_entities_and_routes,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role
from storage.connection import create_sqlite_conn
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def _bootstrap_test_graph(db_path: Path) -> None:
    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                display_name TEXT,
                normalized_name TEXT,
                description TEXT,
                status TEXT DEFAULT 'canonical',
                active INTEGER DEFAULT 1,
                properties_json TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
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
                intentional_orphan INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE node_fts (
                node_id TEXT,
                canonical_name TEXT,
                description TEXT,
                aliases TEXT,
                node_type TEXT
            );
            CREATE TABLE alias (
                alias_id TEXT PRIMARY KEY,
                node_id TEXT,
                alias_name TEXT,
                normalized_alias TEXT,
                alias_type TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
            );
            CREATE TABLE canonical_alias (
                alias_id TEXT PRIMARY KEY,
                entity_id TEXT,
                alias_key TEXT,
                display_alias TEXT,
                alias_type TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
            );
            CREATE TABLE entity_redirect (
                from_node_id TEXT PRIMARY KEY,
                to_node_id TEXT,
                merge_event_id TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
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
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                confidence_score REAL DEFAULT 0.9,
                confidence_label TEXT DEFAULT 'HIGH',
                currentness_state TEXT DEFAULT 'current',
                validation_state TEXT DEFAULT 'accepted_legacy',
                canonical_status TEXT DEFAULT 'canonical',
                canonical_eligible INTEGER DEFAULT 1,
                retrieval_eligible INTEGER DEFAULT 1,
                superseded_by_assertion_id TEXT,
                superseded_at TEXT,
                valid_from TEXT,
                valid_to TEXT,
                primary_source_id TEXT,
                extraction_run_id TEXT,
                approval_reference TEXT,
                origin_type TEXT DEFAULT 'human_operator',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT DEFAULT 'supports',
                independent_corroboration INTEGER DEFAULT 1,
                created_at TEXT,
                PRIMARY KEY (assertion_id, evidence_id)
            );
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                evidence_fingerprint TEXT,
                source_id TEXT,
                source_version_id TEXT,
                locator_kind TEXT,
                locator_value TEXT,
                direct_quote TEXT,
                evidence_directness TEXT,
                source_authority_rank INTEGER,
                extraction_confidence REAL,
                corroboration_group TEXT,
                location_status TEXT,
                created_at TEXT
            );
            CREATE TABLE search_document (
                search_document_id TEXT PRIMARY KEY,
                object_type TEXT,
                object_id TEXT,
                title TEXT,
                body TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                source_id TEXT,
                assertion_id TEXT,
                updated_at TEXT
            );
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT,
                edge_type TEXT,
                to_node_id TEXT,
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
            CREATE TABLE validation_queue (
                queue_id TEXT PRIMARY KEY,
                edge_id TEXT,
                node_id TEXT
            );
            CREATE TABLE legacy_edge_assertion_map (
                edge_id TEXT PRIMARY KEY,
                assertion_id TEXT,
                migration_confidence REAL,
                migration_state TEXT,
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
                active INTEGER DEFAULT 1,
                created_at TEXT
            );
            CREATE TABLE audit_event (
                audit_event_id TEXT PRIMARY KEY,
                actor_or_process TEXT,
                action TEXT,
                record_type TEXT,
                record_id TEXT,
                previous_value_json TEXT,
                new_value_json TEXT,
                source_id TEXT,
                event_timestamp TEXT,
                reason TEXT,
                validation_reference TEXT,
                approval_reference TEXT,
                migration_or_ingestion_run_id TEXT,
                release_version TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, normalized_name, description, status, active) "
            "VALUES ('stakeholder_dan_okray', 'stakeholder', 'Daniel Okray', 'Drew Oakley', 'daniel okray', 'Executive Sponsor', 'canonical', 1)"
        )
        conn.execute(
            "INSERT INTO alias (alias_id, node_id, alias_name, normalized_alias, alias_type, active, created_at) "
            "VALUES ('al_1', 'stakeholder_dan_okray', 'Drew Oakley', 'Drew Oakley', 'nickname', 1, '2026-10-20T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO node_fts (node_id, canonical_name, description, aliases, node_type) "
            "VALUES ('stakeholder_dan_okray', 'Daniel Okray', 'Executive Sponsor', 'Drew Oakley', 'stakeholder')"
        )
        conn.commit()
    finally:
        conn.close()


def test_uwg_role_resolves_stakeholder_and_alias_without_split_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "uwg_role_test.sqlite"
    _bootstrap_test_graph(db_path)

    res1 = persist_validated_role(db_path, "Drew Oakley", "SVP Enterprise Data & AI")
    assert res1["status"] == "PERSISTED_SSOT"
    assert res1["subject_node_id"] == "stakeholder_dan_okray"
    assert res1["role_node_id"].startswith("role-")
    assert res1["physical_proof"]["overall"] == "PASS"

    # Calling a second time with same role reuses deterministic role node and subject node
    res2 = persist_validated_role(db_path, "Daniel Okray", "SVP Enterprise Data & AI")
    assert res2["subject_node_id"] == "stakeholder_dan_okray"
    assert res2["role_node_id"] == res1["role_node_id"]

    ro_conn = create_sqlite_conn(db_path, readonly=True)
    try:
        person_nodes = ro_conn.execute(
            "SELECT node_id FROM node WHERE node_id LIKE 'person_%' OR node_id LIKE 'stakeholder_%'"
        ).fetchall()
        assert [r[0] for r in person_nodes] == ["stakeholder_dan_okray"]
    finally:
        ro_conn.close()


def test_repoint_removes_retired_from_node_fts_and_updates_search_doc_names(tmp_path: Path) -> None:
    db_path = tmp_path / "repoint_test.sqlite"
    _bootstrap_test_graph(db_path)

    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, normalized_name, description, status, active) "
            "VALUES ('person_dan_okray_dup', 'stakeholder', 'Danny Okray', 'Danny Okray', 'danny okray', 'Duplicate', 'canonical', 1)"
        )
        conn.execute(
            "INSERT INTO node_fts (node_id, canonical_name, description, aliases, node_type) "
            "VALUES ('person_dan_okray_dup', 'Danny Okray', 'Duplicate', '', 'stakeholder')"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, currentness_state) "
            "VALUES ('asrt_1', 'person_dan_okray_dup', 'LEADS', 'stakeholder_dan_okray', 'Danny Okray leads AI', 'current')"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state) "
            "VALUES ('asrt_2', 'person_dan_okray_dup', 'HAS_CLAIM_TEXT', 'Danny Okray leads AI', 'Danny Okray leads AI', 'current')"
        )
        conn.execute(
            "INSERT INTO search_document (search_document_id, object_type, object_id, title, body, canonical_status, currentness_state, retrieval_eligible, assertion_id, updated_at) "
            "VALUES ('sd_2', 'assertion', 'asrt_2', 'Danny Okray — Claim', 'Danny Okray leads AI', 'canonical', 'current', 1, 'asrt_2', '2026-10-20T00:00:00Z')"
        )
        now = "2026-10-28T00:00:00Z"
        repoint_node_assertions(conn, "person_dan_okray_dup", "stakeholder_dan_okray", "stakeholder", now)
        repoint_node_entities_and_routes(conn, "person_dan_okray_dup", "stakeholder_dan_okray", now)
        conn.commit()
    finally:
        conn.close()

    ro_conn = create_sqlite_conn(db_path, readonly=True)
    try:
        fts_retired = ro_conn.execute("SELECT COUNT(*) FROM node_fts WHERE node_id = 'person_dan_okray_dup'").fetchone()[0]
        assert fts_retired == 0
        fts_surv = ro_conn.execute("SELECT aliases FROM node_fts WHERE node_id = 'stakeholder_dan_okray'").fetchone()[0]
        assert "Danny Okray" in fts_surv
        sd_row = ro_conn.execute("SELECT title, body FROM search_document WHERE assertion_id = 'asrt_2'").fetchone()
        assert "Daniel Okray" in sd_row[0]
        assert "Daniel Okray" in sd_row[1]
    finally:
        ro_conn.close()


def test_apply_hygiene_remediation_strict_delta_and_scoped_promotion(tmp_path: Path) -> None:
    db_path = tmp_path / "hygiene_test.sqlite"
    _bootstrap_test_graph(db_path)

    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, confidence_score, currentness_state, canonical_status, validation_state) "
            "VALUES ('cand_unapproved', 'stakeholder_dan_okray', 'HAS_CLAIM_TEXT', 'Unapproved high-conf candidate', 0.95, 'current', 'candidate', 'unreviewed')"
        )
        conn.execute(
            "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) "
            "VALUES ('cand_unapproved', 'ev_1', 'supports', '2026-10-20T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, confidence_score, currentness_state, canonical_status, validation_state) "
            "VALUES ('cand_stale', 'stakeholder_dan_okray', 'HAS_CLAIM_TEXT', 'Stale candidate', 0.40, 'current', 'candidate', 'unreviewed')"
        )
        conn.commit()
    finally:
        conn.close()

    pkg_path = tmp_path / "pkg.json"
    pkg_path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "finding_id": "f1",
                        "detective": "stale_candidate",
                        "proposed_fix": "archive_candidate_assertion",
                        "raw_data": {"assertion_id": "cand_stale"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path)
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["remediation_stats"]["candidates_promoted"] == 0

    ro_conn = create_sqlite_conn(db_path, readonly=True)
    try:
        unapproved_status = ro_conn.execute(
            "SELECT canonical_status FROM assertion WHERE assertion_id = 'cand_unapproved'"
        ).fetchone()[0]
        assert unapproved_status == "candidate"
    finally:
        ro_conn.close()
