"""End-to-end verification tests for APEX_TRANS gap remediation, source unmerging, and graph promotion."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.tier2_dedup_core.neet_gap_remediation import (
    NEET_CANONICAL_SOURCES,
    NEET_SESSION1_SOURCE_ID,
    NEET_SESSION2_SOURCE_ID,
    _consolidate_neet_duplicate_sources,
    _promote_neet_candidates,
    _unmerge_neet_collided_sources,
)


def test_neet_normalized_and_hrit_relocated_folders_exist() -> None:
    """Verify all 15 APEX_TRANS packages exist under output/apex_trans/ and 4 HRIT packages reside under output/hrit/."""
    wt_root = Path(__file__).resolve().parents[1]
    dup_0907 = wt_root / "output" / "apex_trans" / "09-07-2026" / "Portfolio_Overview_Research_Corporate"
    assert not dup_0907.exists(), "Duplicate 09-07 Portfolio_Overview_Research_Corporate folder should be removed"

    neet_doc_dirs = [
        wt_root / "output" / "apex_trans" / "07-21-2026" / "Trident_See_Sense_Act_Corporate",
        wt_root / "output" / "apex_trans" / "09-07-2026" / "Neet_Technology_Evaluations_Corporate",
    ]
    for folder in neet_doc_dirs:
        assert folder.is_dir(), f"Missing APEX_TRANS document folder: {folder}"
        assert list(folder.glob("03_*_canonical_signal_projection.json"))

    neet_meeting_dirs = [
        wt_root / "output" / "apex_trans" / "09-10-2026",
        wt_root / "output" / "apex_trans" / "09-11-2026",
        wt_root / "output" / "apex_trans" / "09-16-2026" / "Neet_Insights_Hub_Input_Session_4",
        wt_root / "output" / "apex_trans" / "09-17-2026" / "Neet_Insights_Hub_Input_Krystal_PartnerAdvisory",
        wt_root / "output" / "apex_trans" / "09-17-2026" / "Neet_Insights_Hub_Christina_PartnerAdvisory",
        wt_root / "output" / "apex_trans" / "09-18-2026" / "Insights_Hub_Input_Mitchell_PartnerAdvisory",
        wt_root / "output" / "apex_trans" / "09-18-2026" / "Insights_Hub_Cieran-patrick_Dominic",
        wt_root / "output" / "apex_trans" / "09-24-2026" / "Internal_Transmission_Aaron_Anece",
        wt_root / "output" / "apex_trans" / "09-24-2026" / "Insights_Hub_Data_Systems_Aaron_Anece",
        wt_root / "output" / "apex_trans" / "09-28-2026" / "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
        wt_root / "output" / "apex_trans" / "09-29-2026" / "Insights_Hub_Progress_Mitchell_PartnerAdvisory",
        wt_root / "output" / "apex_trans" / "09-30-2026" / "Insights_Hub_Follow_Mitchell_Tenika",
        wt_root / "output" / "apex_trans" / "10-02-2026" / "Western_Weather_Mitchell_Aaron",
    ]
    for folder in neet_meeting_dirs:
        assert folder.is_dir(), f"Missing APEX_TRANS meeting folder: {folder}"
        assert list(folder.glob("01_*_hitl_resolved_transcript.md"))
        assert list(folder.glob("02_*_executive_recap.md"))
        assert list(folder.glob("03_*_canonical_signal_projection.json"))

    hrit_dirs = [
        wt_root / "output" / "hrit" / "09-25-2026" / "Hr_It_Minute_Notes_09_16_09_25_apex_global",
        wt_root / "output" / "hrit" / "10-02-2026" / "PartnerAdvisory_Align_Phase_Sow_Prf_Clement",
        wt_root / "output" / "hrit" / "10-07-2026" / "Prf_Integration_Architecture_Clement",
        wt_root / "output" / "hrit" / "10-12-2026" / "Nee4u_Employee_Experience_Agent_PartnerAdvisory",
    ]
    for folder in hrit_dirs:
        assert folder.is_dir(), f"Missing relocated HRIT folder: {folder}"
        files_03 = list(folder.glob("03_*_canonical_signal_projection.json"))
        assert files_03
        proj_data = json.loads(files_03[0].read_text(encoding="utf-8"))
        assert proj_data.get("meeting", {}).get("project_id") == "hrit" or proj_data.get("project") == "hrit"


def test_neet_unmerge_consolidate_and_promote_lifecycle_isolated() -> None:
    """Verify 09-17 and 09-18 source unmerging, 09-24 orphan merge, and candidate promotion in an isolated DB."""
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
            source_version_id TEXT PRIMARY KEY, source_id TEXT, version_number INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY, source_id TEXT, source_version_id TEXT
        );
        CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT);
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, primary_source_id TEXT,
            canonical_status TEXT, canonical_eligible INTEGER,
            validation_state TEXT, currentness_state TEXT,
            superseded_by_assertion_id TEXT, retrieval_eligible INTEGER,
            approval_reference TEXT, updated_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, source_id TEXT, assertion_id TEXT,
            canonical_status TEXT, currentness_state TEXT, retrieval_eligible INTEGER, updated_at TEXT
        );
        """
    )
    for sid in NEET_CANONICAL_SOURCES:
        if sid not in (NEET_SESSION2_SOURCE_ID, NEET_SESSION1_SOURCE_ID):
            conn.execute(
                "INSERT INTO source VALUES (?, 'dual_pipeline_intake', 'Name', '2026-09-17', 5, 'derivative_internal', '.staging_old', '', '2026-10-01')",
                (sid,),
            )
    conn.execute(
        "INSERT INTO source VALUES ('source-f39c2ff07811104aa3e05244', 'dual_pipeline_intake', 'Orphan 09-24', '2026-09-24', 5, 'derivative_internal', 'Neet_Internal', '', '2026-09-24')"
    )
    # 09-17 versions
    conn.execute("INSERT INTO source_version VALUES ('source-version-0d637a39e959f4d8109b307a', 'source-f4ad7c688918deba983e24b8', 1)")
    conn.execute("INSERT INTO source_version VALUES ('source-version-52b97de91ce96658b032b495', 'source-f4ad7c688918deba983e24b8', 3)")
    conn.execute("INSERT INTO evidence_item VALUES ('ev-s3', 'source-f4ad7c688918deba983e24b8', 'source-version-0d637a39e959f4d8109b307a')")
    conn.execute("INSERT INTO evidence_item VALUES ('ev-s2', 'source-f4ad7c688918deba983e24b8', 'source-version-52b97de91ce96658b032b495')")
    conn.execute("INSERT INTO assertion VALUES ('a-s3', 'source-f4ad7c688918deba983e24b8', 'candidate', 0, 'needs_review', 'current', NULL, 0, NULL, '2026-10-01')")
    conn.execute("INSERT INTO assertion VALUES ('a-s2', 'source-f4ad7c688918deba983e24b8', 'candidate', 0, 'needs_review', 'current', NULL, 0, NULL, '2026-10-01')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-s3', 'ev-s3')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-s2', 'ev-s2')")
    # 09-18 versions
    conn.execute("INSERT INTO source_version VALUES ('source-version-735c92f25ba58642a7dc2218', 'source-d00208b01736a1a7a5c7119a', 1)")
    conn.execute("INSERT INTO source_version VALUES ('source-version-cdb9485d09f6ca0cfe60e68a', 'source-d00208b01736a1a7a5c7119a', 2)")
    conn.execute("INSERT INTO evidence_item VALUES ('ev-s1', 'source-d00208b01736a1a7a5c7119a', 'source-version-cdb9485d09f6ca0cfe60e68a')")
    conn.execute("INSERT INTO assertion VALUES ('a-s1', 'source-d00208b01736a1a7a5c7119a', 'candidate', 0, 'needs_review', 'current', NULL, 0, NULL, '2026-10-01')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-s1', 'ev-s1')")
    # 09-24 orphan assertion
    conn.execute("INSERT INTO assertion VALUES ('a-0924-orphan', 'source-f39c2ff07811104aa3e05244', 'candidate', 0, 'needs_review', 'current', NULL, 0, NULL, '2026-09-24')")

    splits = _unmerge_neet_collided_sources(conn, "2026-10-25T21:00:00Z")
    assert splits == 2
    merges = _consolidate_neet_duplicate_sources(conn)
    assert merges == 1
    stats = _promote_neet_candidates(conn, "2026-10-25T21:00:00Z")
    assert stats["promoted_active_candidates"] == 4

    s2_src = conn.execute("SELECT primary_source_id, canonical_status FROM assertion WHERE assertion_id = 'a-s2'").fetchone()
    assert s2_src == (NEET_SESSION2_SOURCE_ID, "canonical")
    s1_src = conn.execute("SELECT primary_source_id, canonical_status FROM assertion WHERE assertion_id = 'a-s1'").fetchone()
    assert s1_src == (NEET_SESSION1_SOURCE_ID, "canonical")
    orphan_src = conn.execute("SELECT primary_source_id, canonical_status FROM assertion WHERE assertion_id = 'a-0924-orphan'").fetchone()
    assert orphan_src == ("source-ec1254230eb418a8f639d023", "canonical")
    conn.close()
