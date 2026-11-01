"""Wave 3 E2E & Adversarial Tests: Governed Remediation of Active Graph & Misrouted Output Container."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.graph_scout_core.scans import scan_duplicate_and_malformed_sources
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation
from tools.validate_output_hierarchy import validate_output_hierarchy


def _build_full_remediation_sandbox_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
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
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            source_version_id TEXT,
            direct_quote TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            primary_source_id TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            currentness_state TEXT,
            validation_state TEXT,
            confidence_score REAL,
            scope_text TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            approval_reference TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
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
        CREATE TABLE governance_approval (
            approval_reference TEXT PRIMARY KEY,
            status TEXT,
            active INTEGER
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            node_type TEXT,
            active INTEGER,
            status TEXT,
            updated_at TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            superseded_by TEXT
        );
        CREATE TABLE validation_queue (
            queue_id TEXT PRIMARY KEY,
            edge_id TEXT
        );
        CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT,
            assertion_id TEXT,
            migration_confidence REAL,
            migration_state TEXT,
            created_at TEXT
        );
        CREATE TABLE entity_merge_event (
            merge_event_id TEXT PRIMARY KEY,
            canonical_node_id TEXT
        );
        CREATE TABLE entity_redirect (
            from_node_id TEXT PRIMARY KEY,
            to_node_id TEXT
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
    # Seed 4 duplicate 10-22 LENS Staffing Transition sources + Unknown multi-date collision
    conn.executemany(
        "INSERT INTO source VALUES (?, 'dual_pipeline_intake', ?, ?, 5, 'derivative_internal', ?, 'notes', '2026-10-22T00:00:00Z')",
        [
            ("source-d335b3123d936158964e8f55", "LENS Staffing Transitions and Delivery Updates", "2026-10-22", "local://.local/input/lens_resourcing_transition_10_22_2026.md"),
            ("source-761215e9914c6518cd6fcc35", "Document 20261022 Lens Resourcing Transition", "2026-10-20", "repo_root/.local/input/lens_resourcing_transition_10_22_2026.md"),
            ("source-234fb5b63270645fe22c1344", "Document 20261022 Lens Staffing Transitions Delivery Updates", "2026-10-20", "repo_root/.local/input/lens_resourcing_transition_10_22_2026.md"),
            ("source-9619ee4078949a31625f7473", "Document 20261022 Lens Staffing Transitions Delivery Amit", "2026-10-22", "repo_root/.worktrees/ingest_doc_batch_reingest_last_20_documents/output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Amit/source_document.txt"),
            ("source-cb6118d820a3ebd9b8e6c576", "Unknown", "2026-10-01", "Rules_Creation_Jen_Amit_Alex_Jen/01_Rules_Creation_Jen_Amit_Alex_Jen_10-01-2026_0444PM0455PMET_hitl_resolved_transcript.md"),
            ("source-2af61f674935afe8672a6881", "20261001 Rules Creation Jen Amit", "2026-10-01", "Rules_Creation_Jen_Amit_Alex_Jen/01_Rules_Creation_Jen_Amit_Alex_Jen_10-01-2026_0444PM0455PMET_hitl_resolved_transcript.md"),
        ],
    )
    conn.executemany(
        "INSERT INTO source_version (source_version_id, source_id, version_number, source_date_normalized) VALUES (?, ?, ?, ?)",
        [
            ("sv-1022-1", "source-d335b3123d936158964e8f55", 1, "2026-10-22"),
            ("sv-1022-2", "source-761215e9914c6518cd6fcc35", 1, "2026-10-20"),
            ("sv-1022-3", "source-234fb5b63270645fe22c1344", 1, "2026-10-20"),
            ("sv-1022-4", "source-9619ee4078949a31625f7473", 1, "2026-10-22"),
            ("sv-unk-1", "source-cb6118d820a3ebd9b8e6c576", 1, "2026-10-01"),
            ("sv-unk-2", "source-cb6118d820a3ebd9b8e6c576", 2, "2026-10-06"),
            ("sv-unk-3", "source-cb6118d820a3ebd9b8e6c576", 3, "2025-11-19"),
            ("sv-rules-1", "source-2af61f674935afe8672a6881", 1, "2026-10-01"),
        ],
    )
    conn.executemany(
        "INSERT INTO evidence_item VALUES (?, ?, ?, 'quote')",
        [
            ("ev-1", "source-761215e9914c6518cd6fcc35", "sv-1022-2"),
            ("ev-2", "source-cb6118d820a3ebd9b8e6c576", "sv-unk-3"),
        ],
    )
    conn.executemany(
        "INSERT INTO assertion (assertion_id, primary_source_id, canonical_status, currentness_state) VALUES (?, ?, 'canonical', 'current')",
        [
            ("asrt-1", "source-761215e9914c6518cd6fcc35"),
            ("asrt-2", "source-cb6118d820a3ebd9b8e6c576"),
        ],
    )
    conn.executemany(
        "INSERT INTO assertion_evidence VALUES (?, ?, 'supports', '2026-10-22T00:00:00Z')",
        [
            ("asrt-1", "ev-1"),
            ("asrt-2", "ev-2"),
        ],
    )
    conn.commit()
    conn.close()


def test_e2e_gap4_remediation_consolidates_duplicates_and_verifies_5_physical_checks(tmp_path: Path) -> None:
    """Categories: Exact production regression, Rollback & recovery, Boundary payload."""
    db_path = tmp_path / "gap4_remediation_sandbox.sqlite"
    _build_full_remediation_sandbox_db(db_path)

    pkg_path = tmp_path / "hygiene_package.json"
    pkg_path.write_text(
        json.dumps(
            {
                "findings": [
                    {"proposed_fix": "consolidate_duplicate_sources", "raw_data": {}},
                    {"proposed_fix": "unmerge_or_retitle_degenerate_source", "raw_data": {}},
                ]
            }
        ),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "run-10-25-2026" / "wave3_receipt.json"

    receipt = apply_hygiene_remediation(
        graph_path=db_path,
        package_path=pkg_path,
        receipt_path=receipt_path,
    )
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["remediation_stats"]["duplicate_sources_remediated"] >= 4

    # Independent read-only SQLite verification
    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ro_conn.row_factory = sqlite3.Row
    unknown_cnt = ro_conn.execute("SELECT count(*) FROM source WHERE source_name = 'Unknown'").fetchone()[0]
    assert unknown_cnt == 0

    rem_findings = scan_duplicate_and_malformed_sources(ro_conn)
    assert rem_findings == []

    # Verify assertion_evidence lineage preserved
    ev_links = ro_conn.execute("SELECT count(*) FROM assertion_evidence").fetchone()[0]
    assert ev_links == 2
    ro_conn.close()


def test_output_hierarchy_clean_after_hrit_sow_removal() -> None:
    """Verify that output/project_lens/10-08-2026/Hrit_Sow_V3_Final is gone and hierarchy passes."""
    repo_root = Path(__file__).resolve().parents[1]
    misrouted = repo_root / "output" / "project_lens" / "10-08-2026" / "Hrit_Sow_V3_Final"
    assert not misrouted.exists()
    violations = validate_output_hierarchy(repo_root / "output", project="project_lens")
    assert violations == []
