"""End-to-end and edge-case verification tests for Five-Project gap remediation and graph promotion."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.tier2_dedup_core.five_projects_gap_remediation import (
    FIVE_PROJECTS_ARCHIVE_REFS,
    FIVE_PROJECTS_CANONICAL_SOURCES,
    _consolidate_and_rebind_five_projects_sources,
    _promote_five_projects_candidates,
)
from tools.validate_output_hierarchy_core.constants import (
    LEGACY_UNSCOPED_MULTI_MEETING_DATES,
)


def test_five_projects_filesystem_normalization_and_relocation_e2e() -> None:
    """Verify normalized date subfolders, relocated HRIT 10-06 package, removed duplicates, and empty legacy exemption."""
    wt_root = Path(__file__).resolve().parents[1]
    assert not LEGACY_UNSCOPED_MULTI_MEETING_DATES

    normalized_dirs = [
        wt_root / "output" / "project_atlas" / "12-08-2025" / "NEE_Rewire_Investor_Conference",
        wt_root / "output" / "project_atlas" / "08-28-2026" / "APEX_Development_Rewire",
        wt_root / "output" / "conduit" / "08-18-2026" / "PC_Conduit_Update",
        wt_root / "output" / "conduit" / "08-27-2026" / "PD_Conduit_Catch_Up",
        wt_root / "output" / "enterprise_account_planning" / "09-11-2026" / "Florida_Account_Planning_Kickoff",
        wt_root / "output" / "nea" / "09-14-2026" / "NEA_CI_Stage_Calibration",
        wt_root / "output" / "hrit" / "10-06-2026" / "Prf_Scope_Sow_Clement_PartnerAdvisory",
    ]
    for folder in normalized_dirs:
        assert folder.is_dir(), f"Missing normalized meeting directory: {folder}"
        assert list(folder.glob("01_*_hitl_resolved_transcript.md"))
        assert list(folder.glob("02_*_executive_recap.md"))
        files_03 = list(folder.glob("03_*_canonical_signal_projection.json"))
        assert len(files_03) == 1

    hrit_1006_proj = json.loads(
        next((wt_root / "output" / "hrit" / "10-06-2026" / "Prf_Scope_Sow_Clement_PartnerAdvisory").glob("03_*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert hrit_1006_proj.get("meeting", {}).get("project_id") == "hrit" or hrit_1006_proj.get("project") == "hrit"
    assert not (wt_root / "output" / "nea" / "10-06-2026").exists()

    removed_dirs = [
        wt_root / "output" / "enterprise_account_planning" / "10-14-2026" / "PartnerAdvisory_Solutions_Catalog_2026_Doug",
        wt_root / "output" / "nea" / "readouts",
        wt_root / "output" / "nea" / "Client Interview Prep",
        wt_root / "output" / "nea" / "09-08-2026" / "00_Staffing_Rianne_Mandy_PartnerAdvisory",
        wt_root / "output" / "nea" / "09-28-2026" / "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
        wt_root / "output" / "nea" / "10-12-2026" / "Org_Chart_Christina_Harris",
        wt_root / "output" / "nea" / "10-12-2026" / "Nea_Leadership_Org_Chart",
    ]
    for rem in removed_dirs:
        assert not rem.exists(), f"Redundant folder should be removed: {rem}"

    for _, rel_ref in FIVE_PROJECTS_ARCHIVE_REFS:
        assert (wt_root / rel_ref).is_file(), f"Archive ref target missing on disk: {rel_ref}"


def test_five_projects_graph_ingest_rebind_and_collision_edge_cases_isolated() -> None:
    """Verify 8-package ingestion, duplicate source merge, idempotent re-run, and unique index collision fallback."""
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
    for sid, _ in FIVE_PROJECTS_ARCHIVE_REFS:
        conn.execute(
            "INSERT INTO source VALUES (?, 'dual_pipeline_intake', 'Name', '2026-10-12', 5, 'derivative_internal', '.staging_old', '', '2026-10-12')",
            (sid,),
        )
    conn.execute(
        "INSERT INTO source VALUES ('source-8e104e89d6042c135074186a', 'dual_pipeline_intake', 'Dup Visual Org', '2026-10-12', 5, 'derivative_internal', '.staging_dup', '', '2026-10-12')"
    )
    conn.execute(
        "INSERT INTO source_version VALUES ('sv-dup-1', 'source-8e104e89d6042c135074186a', 1, 'fp', 'ch', '2026-10-12', 'day', 'exact', 5, 's', 'p', '2026-10-12')"
    )
    # Insert two candidate assertions with the same active (subject, predicate, object_node_id) to trigger savepoint collision fallback
    for idx, aid in enumerate(("a-cand-1", "a-cand-2")):
        conn.execute(
            "INSERT INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, predicate, object_node_id, "
            "primary_source_id, validation_state, currentness_state, canonical_status, canonical_eligible, retrieval_eligible) "
            "VALUES (?, ?, 'org_nea', 'PARTNER_OF', 'org_partner_advisory', 'source-8e104e89d6042c135074186a', "
            "'needs_review', 'current', 'candidate', 0, 0)",
            (aid, f"fp-{idx}"),
        )

    merged, rebound = _consolidate_and_rebind_five_projects_sources(conn)
    assert merged == 1
    assert rebound == len(FIVE_PROJECTS_ARCHIVE_REFS)

    stats = _promote_five_projects_candidates(conn, "2026-10-26T12:55:00Z")
    assert stats["sources_ingested"] == 8
    assert stats["assertions_ingested"] == 24
    assert stats["promoted_active_assertions"] + stats["normalized_tuple_collisions"] == 26
    assert stats["normalized_tuple_collisions"] >= 1

    # Idempotent second run must add 0 sources and 0 promotions
    stats_rerun = _promote_five_projects_candidates(conn, "2026-10-26T12:56:00Z")
    assert stats_rerun["sources_ingested"] == 0
    assert stats_rerun["promoted_active_assertions"] == 0
    assert stats_rerun["normalized_tuple_collisions"] == 0

    ph = ",".join("?" for _ in FIVE_PROJECTS_CANONICAL_SOURCES)
    non_canon = conn.execute(
        f"SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND (canonical_status != 'canonical' OR canonical_eligible = 0)",
        list(FIVE_PROJECTS_CANONICAL_SOURCES),
    ).fetchone()[0]
    assert non_canon == 0
    conn.close()
