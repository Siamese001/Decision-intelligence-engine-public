"""End-to-end verification tests for Project DocIQ gap remediation and graph promotion."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.tier2_dedup_core.dociq_gap_remediation import (
    DOCIQ_CANONICAL_SOURCES,
    DOCIQ_DUPLICATE_SOURCE_PAIRS,
    DOCIQ_TRANSITION_ASSERTION_ID,
    _consolidate_dociq_duplicate_sources,
    _promote_dociq_candidates,
)


def test_dociq_relocated_and_restored_folders_exist() -> None:
    """Verify all 5 core Project DocIQ folders exist under output/dociq/ with project='dociq'."""
    wt_root = Path(__file__).resolve().parents[1]
    meeting_dirs = [
        wt_root / "output" / "dociq" / "09-23-2026" / "Dociq_Q4_Scope",
        wt_root / "output" / "dociq" / "09-30-2026" / "Dociq_Q4_Scope_Bailey_Amit",
        wt_root / "output" / "dociq" / "10-01-2026" / "Dociq_Jack_Amit_Jack_PartnerAdvisory",
    ]
    for folder in meeting_dirs:
        assert folder.is_dir(), f"Missing expected DocIQ meeting folder: {folder}"
        assert list(folder.glob("01_*_hitl_resolved_transcript.md")), f"Missing 01 transcript in {folder}"
        assert list(folder.glob("02_*_executive_recap.md")), f"Missing 02 recap in {folder}"
        files_03 = list(folder.glob("03_*_canonical_signal_projection.json"))
        assert files_03, f"Missing 03 signal projection in {folder}"
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        assert meta.get("project") == "dociq"

    doc_dirs = [
        wt_root / "output" / "dociq" / "09-30-2026" / "Dociq_Update_Jack_PartnerAdvisory",
        wt_root / "output" / "dociq" / "10-01-2026" / "Dociq_Update_Q3_Accomplishments_Backfill",
    ]
    for folder in doc_dirs:
        assert folder.is_dir(), f"Missing expected DocIQ document folder: {folder}"
        files_03 = list(folder.glob("03_*_canonical_signal_projection.json"))
        assert files_03, f"Missing 03 signal projection in {folder}"
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        assert meta.get("project") == "dociq"

    jack_ref = (
        wt_root
        / "output"
        / "dociq"
        / "10-01-2026"
        / "Dociq_Jack_Amit_Jack_PartnerAdvisory"
        / "referenced_artifacts"
    )
    assert (jack_ref / "dociq_q3_progress_key_accomplishments.md").is_file()
    assert (jack_ref / "dociq_q3_progress_key_accomplishments.png").is_file()


def test_dociq_leaked_reference_hygiene() -> None:
    """Verify leaked dociq_q4_scope_09-23-2026 files were removed from non-DocIQ project folders."""
    wt_root = Path(__file__).resolve().parents[1]
    cleaned_dirs = [
        wt_root / "output" / "conduit" / "08-31-2026" / "Conduit_Lessons_Learned_Michael_PartnerAdvisory" / "referenced_artifacts",
        wt_root / "output" / "enterprise_account_planning" / "10-01-2026" / "Florida_Account_Planning_Beau_Maud" / "referenced_artifacts",
        wt_root / "output" / "project_lens" / "09-15-2026" / "Mandy_Amit_Ayer_PartnerAdvisory" / "referenced_artifacts",
        wt_root / "output" / "project_lens" / "09-30-2026" / "Pipeline_Sign_Off_Amit_Jake" / "referenced_artifacts",
        wt_root / "output" / "project_lens" / "10-09-2026" / "Working_Session_Testing_Eduardo_PartnerAdvisory" / "referenced_artifacts",
    ]
    for ref_dir in cleaned_dirs:
        if ref_dir.is_dir():
            names = {p.name for p in ref_dir.iterdir()}
            assert "dociq_q4_scope_09-23-2026.txt" not in names
            assert "dociq_q4_scope_09-23-2026_metadata.json" not in names


def test_dociq_duplicate_merge_and_candidate_promotion_isolated() -> None:
    """Verify duplicate source consolidation, idempotency, and candidate promotion in an isolated SQLite DB."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    conn.executescript(
        """
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY, approval_reference TEXT, approval_type TEXT,
            scope_json TEXT, proposal_sha256 TEXT, approval_statement_sha256 TEXT,
            approved_at TEXT, expires_at TEXT, status TEXT, approved_by TEXT,
            release_version TEXT, active INTEGER, created_at TEXT
        );
        CREATE TABLE source (source_id TEXT PRIMARY KEY, cold_archive_ref TEXT);
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY, source_id TEXT, version_number INTEGER
        );
        CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_id TEXT);
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, primary_source_id TEXT,
            canonical_status TEXT, canonical_eligible INTEGER,
            validation_state TEXT, currentness_state TEXT,
            superseded_by_assertion_id TEXT, superseded_at TEXT,
            retrieval_eligible INTEGER, approval_reference TEXT,
            object_node_id TEXT, updated_at TEXT
        );
        CREATE TABLE search_document (doc_id TEXT PRIMARY KEY, source_id TEXT);
        CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, source_id TEXT);
        """
    )
    win, lose = DOCIQ_DUPLICATE_SOURCE_PAIRS[0]
    for sid in DOCIQ_CANONICAL_SOURCES:
        conn.execute("INSERT INTO source VALUES (?, 'old_ref')", (sid,))
    conn.execute("INSERT INTO source VALUES (?, NULL)", (lose,))
    conn.execute("INSERT INTO source_version VALUES ('sv-1', ?, 1)", (win,))
    conn.execute("INSERT INTO source_version VALUES ('sv-2', ?, 1)", (lose,))
    conn.execute(
        "INSERT INTO assertion VALUES ('a-1', ?, 'candidate', 0, 'needs_review', 'current', NULL, NULL, 0, NULL, 'n-1', '2026-10-01')",
        (lose,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES (?, 'source-619a0273fe4357416470e53d', 'candidate', 0, 'superseded', 'historical', 'a-1', '2026-10-01', 0, NULL, NULL, '2026-10-01')",
        (DOCIQ_TRANSITION_ASSERTION_ID,),
    )

    merged = _consolidate_dociq_duplicate_sources(conn)
    assert merged == 1
    assert _consolidate_dociq_duplicate_sources(conn) == 0
    stats = _promote_dociq_candidates(conn, "2026-10-25T19:50:00Z")
    assert stats["total_promoted"] == 2
    rem = conn.execute("SELECT COUNT(*) FROM assertion WHERE canonical_status = 'candidate'").fetchone()[0]
    assert rem == 0
    assert len(DOCIQ_CANONICAL_SOURCES) == 5
    conn.close()
