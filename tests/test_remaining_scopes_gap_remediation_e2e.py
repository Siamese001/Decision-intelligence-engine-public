"""End-to-end verification tests for remaining scopes gap remediation and graph promotion."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.tier2_dedup_core.remaining_scopes_gap_remediation import (
    REMAINING_SCOPES_ARCHIVE_REFS,
    _promote_remaining_scopes_candidates,
    _rebind_remaining_scopes_sources,
)


def test_remaining_scopes_filesystem_normalization_and_restoration_e2e() -> None:
    """Verify nested meeting slugs, restored packages (EDO, MI, LENS), and pruned duplicates."""
    wt_root = Path(__file__).resolve().parents[1]

    nested_dirs = [
        wt_root / "output" / "ec_scheduling" / "04-09-2026" / "NEE_EC_Scheduling_PO2000622222",
        wt_root / "output" / "project_spring" / "06-29-2026" / "Project_Spring_SOW",
        wt_root / "output" / "enterprise_architecture" / "07-15-2026" / "EA_AI_Policy_Strategy",
        wt_root / "output" / "enterprise_architecture" / "08-11-2026" / "AI_Enterprise_Systems_of_Record_HR_PoC",
        wt_root / "output" / "enterprise_architecture" / "09-12-2026" / "Architecture_Sync_Decoupled_WAL",
        wt_root / "output" / "regai" / "09-02-2026" / "RegAI_Delivery_Issues",
        wt_root / "output" / "regai" / "09-08-2026" / "RegAI_Delivery_Risk",
        wt_root / "output" / "regai" / "09-10-2026" / "RegAI_FlexAI_Proposal",
        wt_root / "output" / "edo" / "10-19-2026" / "Work_Venkat_Lan",
        wt_root / "output" / "market_intelligence" / "10-14-2026" / "Energy_Utilities_Market_Antigravity",
        wt_root / "output" / "project_lens" / "09-09-2026" / "Jen_Pan_LENS_Business_Check_In",
    ]
    for folder in nested_dirs:
        assert folder.is_dir(), f"Missing normalized or restored directory: {folder}"
        files_03 = list(folder.glob("03_*_canonical_signal_projection.json"))
        assert len(files_03) == 1, f"Expected exactly 1 Deliverable 03 in {folder}, found {len(files_03)}"

    edo_dir = wt_root / "output" / "edo" / "10-19-2026" / "Work_Venkat_Lan"
    assert list(edo_dir.glob("01_*_hitl_resolved_transcript.md"))
    assert list(edo_dir.glob("02_*_executive_recap.md"))

    lens_0909_proj = json.loads(
        next((wt_root / "output" / "project_lens" / "09-09-2026" / "Jen_Pan_LENS_Business_Check_In").glob("03_*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert (
        lens_0909_proj.get("project") == "project_lens"
        or lens_0909_proj.get("meeting_metadata", {}).get("project_id") == "project_lens"
        or "La Casa" in json.dumps(lens_0909_proj)
    )

    removed_dirs = [
        wt_root / "output" / "enterprise_architecture" / "09-16-2026" / "Coming_together_as_one_technology_organi",
        wt_root / "output" / "project_lens" / "10-22-2026" / "Resourcing_Transition_Tyler_PartnerAdvisory",
        wt_root / "output" / "project_lens" / "10-22-2026" / "Staffing_Transitions_Delivery_Amit",
    ]
    for rem in removed_dirs:
        assert not rem.exists(), f"Duplicate folder should be removed: {rem}"

    for _, rel_ref in REMAINING_SCOPES_ARCHIVE_REFS:
        assert (wt_root / rel_ref).is_file(), f"Archive ref target missing on disk: {rel_ref}"


def test_remaining_scopes_graph_ingest_rebind_and_promotion_e2e() -> None:
    """Verify 8-package ingestion, 99 archive ref rebinds, 4 undated source backfills, and promotion."""
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
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, object_type TEXT, object_id TEXT,
            title TEXT, body TEXT, canonical_status TEXT, currentness_state TEXT,
            retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT, updated_at TEXT
        );
        """
    )
    for sid, _ in REMAINING_SCOPES_ARCHIVE_REFS:
        sdate = None if sid in ("source-5603cf8f5154b25dfbf5f33c", "source-5e00f65a5d153b3c14e13d62", "source-7d181ab3563433b658b3f6a0", "source-dce33d47b09ee075af5dfbe8") else "2026-10-12"
        conn.execute(
            "INSERT INTO source VALUES (?, 'dual_pipeline_intake', 'Name', ?, 5, 'derivative_internal', '.staging_old', '', '2026-10-12')",
            (sid, sdate),
        )

    dates_backfilled, rebound = _rebind_remaining_scopes_sources(conn)
    assert dates_backfilled == 4
    assert rebound == len(REMAINING_SCOPES_ARCHIVE_REFS)

    stats = _promote_remaining_scopes_candidates(conn, "2026-10-26T17:00:00Z")
    assert stats["sources_ingested"] == 8
    assert stats["assertions_ingested"] == 24
    assert stats["promoted_active_assertions"] == 24
    assert stats["normalized_tuple_collisions"] == 0
    conn.close()
