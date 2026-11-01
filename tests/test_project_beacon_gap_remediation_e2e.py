"""End-to-end and edge-case verification tests for Project Beacon gap remediation and graph promotion."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.tier2_dedup_core.sherpa_gap_remediation import (
    SHERPA_ARCHIVE_REFS,
    SHERPA_CANONICAL_SOURCES,
    _ingest_missing_sherpa_meetings,
    _promote_sherpa_candidates,
    _rebind_sherpa_archive_refs,
)


def test_project_beacon_normalized_and_restored_folders_exist() -> None:
    """Verify normalized <Meeting_Slug> folders and restored 09-04 FDX rebuild document under output/project_beacon/."""
    wt_root = Path(__file__).resolve().parents[1]
    meeting_dirs = [
        wt_root / "output" / "project_beacon" / "07-27-2026" / "A2a_Architecture_Egle_Andy",
        wt_root / "output" / "project_beacon" / "08-13-2026" / "Taxonomy_Mcp_Vs_A2a_Speaker_Alpha_PartnerAdvisory",
        wt_root / "output" / "project_beacon" / "08-20-2026" / "Technical_Interlock_Speaker_Alpha_Amit",
        wt_root / "output" / "project_beacon" / "09-04-2026" / "project_beacon_Path_Forward",
        wt_root / "output" / "project_beacon" / "09-09-2026" / "Sherpa_Demo_Josh",
        wt_root / "output" / "project_beacon" / "09-11-2026" / "Sherpa_Next_Steps",
        wt_root / "output" / "project_beacon" / "09-17-2026" / "Next_Steps_On_Sherpa_Marcus_PartnerAdvisory",
        wt_root / "output" / "project_beacon" / "09-18-2026" / "Sherpa_sprint_demo_Marcus_PartnerAdvisory",
        wt_root / "output" / "project_beacon" / "10-06-2026" / "project_beacon_Sprint_Speaker_Alpha_PartnerAdvisory",
        wt_root / "output" / "project_beacon" / "10-13-2026" / "project_beacon_Metrics_Statistics_master",
        wt_root / "output" / "project_beacon" / "10-14-2026" / "Sherpa_Connect_Cansu_Dan",
        wt_root / "output" / "project_beacon" / "10-16-2026" / "Sprint_Demo_Marcus_PartnerAdvisory",
    ]
    for folder in meeting_dirs:
        assert folder.is_dir(), f"Missing expected Project Beacon meeting folder: {folder}"
        assert list(folder.glob("01_*_hitl_resolved_transcript.md")), f"Missing 01 transcript in {folder}"
        assert list(folder.glob("02_*_executive_recap.md")), f"Missing 02 recap in {folder}"
        assert list(folder.glob("03_*_canonical_signal_projection.json")), f"Missing 03 projection in {folder}"
        assert (folder / "intake_package.json").is_file(), f"Missing intake_package.json in {folder}"

    fdx_dir = wt_root / "output" / "project_beacon" / "09-04-2026" / "Sherpa_Fdx_Rebuild_Aws_Decision"
    assert fdx_dir.is_dir(), "Missing restored 09-04 Sherpa_Fdx_Rebuild_Aws_Decision folder"
    assert (fdx_dir / "03_Sherpa_Fdx_Rebuild_Aws_Decision_09-04-2026_canonical_signal_projection.json").is_file()
    assert (fdx_dir / "Sherpa_Fdx_Rebuild_Aws_Decision_source.md").is_file()
    meta = json.loads((fdx_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta.get("is_document") is True
    assert meta.get("project") == "project_beacon"


def test_project_beacon_no_flat_files_in_date_directories() -> None:
    """Boundary/edge-case check: verify no loose deliverable or receipt files remain directly inside date folders."""
    wt_root = Path(__file__).resolve().parents[1]
    for date_str in ("09-04-2026", "09-09-2026", "09-11-2026"):
        date_dir = wt_root / "output" / "project_beacon" / date_str
        loose_files = [p.name for p in date_dir.iterdir() if p.is_file() and p.name != ".DS_Store"]
        assert loose_files == [], f"Unexpected loose files in {date_dir}: {loose_files}"


def test_project_beacon_ingestion_idempotency_and_promotion_isolated() -> None:
    """Verify missing meeting ingestion, idempotent re-ingestion, archive rebinding, and draft promotion in an isolated DB."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    conn.executescript(
        """
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY, approval_reference TEXT, approval_type TEXT,
            scope_json TEXT, proposal_sha256 TEXT, approval_statement_sha256 TEXT,
            approved_at TEXT, expires_at TEXT, status TEXT, approved_by TEXT,
            release_version TEXT, active INTEGER, created_at TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT, source_date TEXT,
            authority_rank INTEGER, source_posture TEXT, cold_archive_ref TEXT, notes TEXT, created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY, source_id TEXT, version_number INTEGER,
            source_fingerprint TEXT, content_hash TEXT, source_date_normalized TEXT,
            source_date_precision TEXT, source_date_status TEXT, authority_rank INTEGER,
            scope_text TEXT, posture_text TEXT, created_at TEXT
        );
        CREATE TABLE extraction_run (
            extraction_run_id TEXT PRIMARY KEY, extractor_version TEXT, graph_sha256 TEXT,
            source_fingerprint TEXT, started_at TEXT, completed_at TEXT, status TEXT, candidate_count INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY, evidence_fingerprint TEXT, source_id TEXT, source_version_id TEXT,
            locator_kind TEXT, locator_value TEXT, section_name TEXT, evidence_directness TEXT,
            source_authority_rank INTEGER, extraction_confidence REAL, corroboration_group TEXT,
            location_status TEXT, created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT, subject_node_id TEXT,
            predicate TEXT, object_node_id TEXT, object_literal TEXT, object_literal_type TEXT,
            assertion_type TEXT, atomicity_state TEXT, scope_text TEXT, speaker_or_author TEXT,
            primary_source_id TEXT, extraction_run_id TEXT, extraction_method TEXT, origin_type TEXT,
            confidence_label TEXT, confidence_score REAL, validation_state TEXT, currentness_state TEXT,
            valid_from TEXT, date_precision TEXT, date_status TEXT, recorded_at TEXT, resolution_state TEXT,
            canonical_status TEXT, canonical_eligible INTEGER, retrieval_eligible INTEGER, materiality TEXT,
            classification TEXT, allowed_audiences_json TEXT, hitl_required INTEGER, validation_priority TEXT,
            qualifiers_json TEXT, legacy_record_type TEXT, legacy_record_ids_json TEXT,
            created_at TEXT, updated_at TEXT, approval_reference TEXT,
            superseded_by_assertion_id TEXT, superseded_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT,
            independent_corroboration INTEGER, created_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, object_type TEXT, object_id TEXT, title TEXT, body TEXT,
            canonical_status TEXT, currentness_state TEXT, retrieval_eligible INTEGER,
            source_id TEXT, assertion_id TEXT, updated_at TEXT
        );
        """
    )
    for sid, _ in SHERPA_ARCHIVE_REFS:
        conn.execute(
            "INSERT INTO source VALUES (?, 'dual_pipeline_intake', 'Test', '2026-09-04', 5, 'derivative_internal', 'old_staging_ref', '', '2026-09-04T00:00:00Z')",
            (sid,),
        )
    rebound = _rebind_sherpa_archive_refs(conn)
    assert rebound == len(SHERPA_ARCHIVE_REFS)
    ref_0904 = conn.execute("SELECT cold_archive_ref FROM source WHERE source_id = 'source-cffb30b6e16ac23d5f1b6e4d'").fetchone()[0]
    assert ref_0904 == "output/project_beacon/09-04-2026/project_beacon_Path_Forward/01_project_beacon_Path_Forward_09-04-2026_0200PM_0300PM_ET_hitl_resolved_transcript.md"

    stats = _promote_sherpa_candidates(conn, "2026-10-25T21:00:00Z")
    assert stats["sources_ingested"] == 3
    assert stats["assertions_ingested"] == 9
    assert stats["promoted_non_canonical_assertions"] == 9

    # Idempotent re-ingestion must add 0 duplicate sources or assertions
    repeat = _ingest_missing_sherpa_meetings(conn, "2026-10-25T21:05:00Z")
    assert repeat == {"sources_ingested": 0, "assertions_ingested": 0}

    ph = ",".join("?" for _ in SHERPA_CANONICAL_SOURCES)
    rem_drafts = conn.execute(
        f"SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND canonical_status != 'canonical'",
        SHERPA_CANONICAL_SOURCES,
    ).fetchone()[0]
    assert rem_drafts == 0
