"""Wave 4 E2E tests for governed corpus-wide document and GraphDB parity remediation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from tools.remediate_document_graph_parity import execute_remediation_on_db
from tools.verify_document_graph_parity import audit_source_parity


def _create_v48_graph_db(db_path: Path) -> Path:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT UNIQUE,
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
            source_type TEXT,
            source_name TEXT,
            source_date TEXT,
            authority_rank INTEGER,
            source_posture TEXT,
            cold_archive_ref TEXT,
            notes TEXT,
            created_at TEXT
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
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            normalized_name TEXT,
            description TEXT,
            status TEXT,
            confidence_score REAL,
            created_at TEXT,
            updated_at TEXT,
            active INTEGER
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
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            status TEXT,
            superseded_by TEXT,
            updated_at TEXT,
            active INTEGER
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
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            materiality TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            hitl_required INTEGER,
            validation_priority TEXT,
            qualifiers_json TEXT,
            legacy_record_type TEXT,
            legacy_record_ids_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            approval_reference TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            independent_corroboration INTEGER,
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
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
        CREATE VIRTUAL TABLE search_fts USING fts5(
            search_document_id UNINDEXED,
            object_type UNINDEXED,
            object_id UNINDEXED,
            title,
            body
        );
        INSERT INTO source (source_id, source_type, source_name, source_date, authority_rank, source_posture, cold_archive_ref, notes, created_at)
        VALUES ('source_miro', 'document', 'Miro Org Chart', '2026-10-20', 5, 'derivative_internal', 'output/miro/source_document.md', 'Seed', '2026-10-20T00:00:00Z');
        INSERT INTO node (node_id, node_type, canonical_name, normalized_name, description, status, confidence_score, created_at, updated_at, active)
        VALUES ('org_nea', 'org', 'Apex Global Analytics', 'apex_global analytics', 'Apex Global Analytics organization.', 'canonical', 0.95, '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z', 1);
        INSERT INTO assertion (
            assertion_id, assertion_fingerprint, subject_node_id, predicate, object_literal, object_literal_type,
            assertion_type, atomicity_state, scope_text, primary_source_id, validation_state, currentness_state,
            valid_from, recorded_at, resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
            created_at, updated_at, approval_reference
        ) VALUES (
            'asrt-seed-1', 'fp-seed-1', 'org_nea', 'HAS_CLAIM_TEXT', 'Apex Global Analytics is an identified org in Miro.',
            'text', 'extracted_claim', 'atomic', 'Apex Global Analytics is an identified org in Miro.', 'source_miro',
            'unreviewed', 'current', '2026-10-20', '2026-10-20T00:00:00Z', 'resolved', 'candidate', 1, 0,
            '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z', 'wave4-corpus-parity-approval-20261026'
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.e2e
def test_wave4_corpus_parity_remediation_repairs_all_cohorts_and_seals_5_check_proof_e2e(tmp_path: Path) -> None:
    """Seed an isolated graph DB with all 5 defect cohorts, run execute_remediation_on_db, and verify 5-check proof + strict Gate C4 pass."""
    db_path = _create_v48_graph_db(tmp_path / "wave4_e2e.sqlite")

    out_dir = tmp_path / "output" / "hrit" / "10-20-2026" / "Position_Integration_Field_Mapping_Creat"
    out_dir.mkdir(parents=True)
    (out_dir / "source_document.md").write_text(
        "### Sheet: Position Integration Field Mapping\n\n"
        "| Field ID | Workday Field | SAP Target | Rule |\n"
        "| --- | --- | --- | --- |\n"
        "| POS-01 | Position Title | PLANS_TXT | Sync on hire |\n"
        "| POS-02 | Job Family | STELL_ID | Map via lookup |\n"
        "| POS-03 | Cost Center | KOSTL | Validate active |\n"
        "| POS-04 | Supervisory Org | ORGEH | Inherit parent |\n"
        "| POS-05 | Location Code | WERKS | Direct copy |\n",
        encoding="utf-8",
    )
    (out_dir / "03_Position_Integration_Field_Mapping_Creat_10-20-2026_canonical_signal_projection.json").write_text(
        json.dumps(
            {
                "meeting_id": "source-w4-pos-map",
                "evidence_index": {
                    "ev-pos-1": {"exact_text": "POS-01 | Position Title | PLANS_TXT | Sync on hire"},
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {
                            "id": "document:pos-map",
                            "type": "document",
                            "name": "Position Integration Field Mapping",
                            "description": "Authoritative HR360 Position Integration Field Mapping workbook.",
                        }
                    ],
                    "relationship_signals": [],
                },
            }
        ),
        encoding="utf-8",
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO source (source_id, source_type, source_name, source_date, authority_rank, source_posture, cold_archive_ref, notes, created_at) "
        "VALUES ('source-w4-staging', 'meeting', 'Staging Leak Meeting', '2026-10-26', 5, 'derivative_internal', '.staging_Aruna_81b351c0/email.md', 'Test', '2026-10-26T00:00:00Z')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO source (source_id, source_type, source_name, source_date, authority_rank, source_posture, cold_archive_ref, notes, created_at) "
        "VALUES ('source-w4-pos-map', 'document', 'Document Position Integration Field Mapping', '2026-10-20', 5, 'derivative_internal', ?, 'Test', '2026-10-20T00:00:00Z')",
        (str(out_dir / "03_Position_Integration_Field_Mapping_Creat_10-20-2026_canonical_signal_projection.json"),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO node (node_id, node_type, canonical_name, normalized_name, description, status, confidence_score, created_at, updated_at, active) "
        "VALUES ('node:w4-cand', 'workstream', 'Candidate Workstream', 'candidate workstream', 'Substantive workstream description.', 'candidate', 0.9, '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z', 1)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO node (node_id, node_type, canonical_name, normalized_name, description, status, confidence_score, created_at, updated_at, active) "
        "VALUES ('node:w4-orphan', 'capability', 'Orphan Capability', 'orphan capability', 'Substantive capability description.', 'canonical', 0.9, '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z', 1)"
    )
    conn.commit()
    conn.close()

    res = execute_remediation_on_db(db_path, tmp_path / "output")
    assert res["terminal_outcome"] == "PASS"
    assert res["physical_proof"]["overall"] == "PASS"
    assert res["stats"]["staging_refs_sanitized"] >= 1
    assert res["stats"]["promoted_candidate_nodes"] >= 1

    parity = audit_source_parity(db_path, tmp_path / "output", strict=True)
    assert parity["status"] == "PASS"
    assert parity["violation_count"] == 0
