"""End-to-end verification tests for Project NEXUS gap remediation and graph promotion."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.tier2_dedup_core.nexus_gap_remediation import (
    NEXUS_CANONICAL_SOURCES,
    NEXUS_DUPLICATE_SOURCE_PAIRS,
    _consolidate_nexus_duplicate_sources,
    _promote_nexus_candidates,
)


def test_nexus_relocated_and_restored_folders_exist() -> None:
    """Verify relocated non-Nexus folders and restored historical folders exist in canonical projects."""
    wt_root = Path(__file__).resolve().parents[1]
    expected_dirs = [
        wt_root / "output" / "nea" / "09-01-2026" / "NEA_Screener_Sync",
        wt_root / "output" / "nea" / "09-07-2026" / "NEA_Onboarding_Charter_Teams",
        wt_root / "output" / "project_lens" / "09-14-2026" / "SOW1_Observability_Catchup",
        wt_root / "output" / "partner_alliances" / "09-22-2026" / "Google_Internal_Anya_Shreyak",
        wt_root / "output" / "partner_alliances" / "09-25-2026" / "Google_Alex_Shreyak_Amit_Anya",
        wt_root / "output" / "dociq" / "09-30-2026" / "Dociq_Update_Jack_PartnerAdvisory",
        wt_root / "output" / "dociq" / "10-01-2026" / "Dociq_Jack_Amit_Jack_PartnerAdvisory",
        wt_root / "output" / "market_intelligence" / "10-04-2026" / "Energy_Utilities_Market_Antigravity",
        wt_root / "output" / "market_intelligence" / "10-06-2026" / "Energy_Utilities_Market_Antigravity",
        wt_root / "output" / "nea" / "10-09-2026" / "Jeffrey_Amit_Cweos_Jeffrey_PartnerAdvisory",
        wt_root / "output" / "internal_consulting" / "10-12-2026" / "Agile_Delivery_Roles_Product_PartnerAdvisory",
    ]
    for folder in expected_dirs:
        assert folder.is_dir(), f"Missing expected relocated/restored folder: {folder}"
        files_03 = list(folder.glob("03_*_canonical_signal_projection.json"))
        assert len(files_03) == 1, f"Expected exactly 1 canonical 03 projection in {folder}, found {len(files_03)}"
        proj_data = json.loads(files_03[0].read_text(encoding="utf-8"))
        assert isinstance(proj_data, dict) and proj_data


def test_nexus_folder_purity() -> None:
    """Verify only true Project Nexus date folders remain under output/project_nexus/."""
    wt_root = Path(__file__).resolve().parents[1]
    nexus_dir = wt_root / "output" / "project_nexus"
    assert nexus_dir.is_dir()
    remaining_dates = {p.name for p in nexus_dir.iterdir() if p.is_dir() and not p.name.startswith(".")}
    assert remaining_dates == {
        "08-18-2026",
        "08-28-2026",
        "09-10-2026",
        "09-11-2026",
        "09-14-2026",
    }


def test_nexus_duplicate_merge_and_candidate_promotion_isolated() -> None:
    """Verify duplicate source consolidation, 10-04 ingestion, and candidate promotion in an isolated SQLite DB."""
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
    win, lose = NEXUS_DUPLICATE_SOURCE_PAIRS[0]
    conn.execute("INSERT INTO source (source_id) VALUES (?)", (win,))
    conn.execute("INSERT INTO source (source_id) VALUES (?)", (lose,))
    conn.execute("INSERT INTO source_version (source_version_id, source_id, version_number) VALUES ('sv-1', ?, 1)", (win,))
    conn.execute("INSERT INTO source_version (source_version_id, source_id, version_number) VALUES ('sv-2', ?, 1)", (lose,))
    conn.execute(
        "INSERT INTO assertion (assertion_id, primary_source_id, canonical_status, canonical_eligible, retrieval_eligible) VALUES ('a-1', ?, 'candidate', 0, 0)",
        (lose,),
    )

    merged = _consolidate_nexus_duplicate_sources(conn)
    assert merged == 1
    stats = _promote_nexus_candidates(conn, "2026-10-25T20:00:00Z")
    assert stats["ingested_1004_assertions"] == 3
    assert stats["promoted_candidates"] == 4
    rem = conn.execute(
        f"SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({','.join('?' for _ in NEXUS_CANONICAL_SOURCES)}) AND canonical_status='candidate'",
        NEXUS_CANONICAL_SOURCES,
    ).fetchone()[0]
    assert rem == 0
    conn.close()
