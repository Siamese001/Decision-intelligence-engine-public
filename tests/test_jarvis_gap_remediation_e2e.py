"""End-to-end verification tests for Project JARVIS gap remediation and graph promotion."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.core import ROOT
from tools.tier2_dedup_core.jarvis_gap_remediation import (
    JARVIS_CANONICAL_SOURCES,
    JARVIS_DUPLICATE_SOURCE_PAIRS,
    _consolidate_jarvis_duplicate_sources,
    _promote_jarvis_candidates,
)


def test_jarvis_relocated_and_restored_folders_exist() -> None:
    """Verify relocated and restored Project JARVIS folders exist under output/project_jarvis/."""
    wt_root = Path(__file__).resolve().parents[1]
    expected_dirs = [
        wt_root / "output" / "project_jarvis" / "09-25-2026" / "Scope_Traci_Metzger",
        wt_root / "output" / "project_jarvis" / "09-28-2026" / "Debrief_Traci_Amit",
        wt_root / "output" / "project_jarvis" / "09-28-2026" / "Followup_Business_Case_Jill_PartnerAdvisory",
        wt_root / "output" / "project_jarvis" / "09-29-2026" / "Jarvis_Reid_Amit",
    ]
    for folder in expected_dirs:
        assert folder.is_dir(), f"Missing expected Jarvis folder: {folder}"
        files_01 = list(folder.glob("01_*_hitl_resolved_transcript.md"))
        files_02 = list(folder.glob("02_*_executive_recap.md"))
        files_03 = list(folder.glob("03_*_canonical_signal_projection.json"))
        assert files_01, f"Missing 01 transcript in {folder}"
        assert files_02, f"Missing 02 recap in {folder}"
        assert files_03, f"Missing 03 signal projection in {folder}"
        proj_data = json.loads(files_03[0].read_text(encoding="utf-8"))
        assert isinstance(proj_data, dict) and proj_data


def test_jarvis_charter_reference_hygiene() -> None:
    """Verify unrelated cross-project reference files were removed from Jarvis_Q1_Charter_Sharon_PartnerAdvisory."""
    wt_root = Path(__file__).resolve().parents[1]
    ref_dir = (
        wt_root
        / "output"
        / "project_jarvis"
        / "09-28-2026"
        / "Jarvis_Q1_Charter_Sharon_PartnerAdvisory"
        / "referenced_artifacts"
    )
    assert ref_dir.is_dir()
    remaining = {p.name for p in ref_dir.iterdir()}
    assert "dociq_q4_scope_09-23-2026.txt" not in remaining
    assert "nea_screener_sync_9_1_2026.txt" not in remaining
    pngs = [name for name in remaining if name.endswith(".png")]
    assert len(pngs) == 7


def test_jarvis_duplicate_merge_and_candidate_promotion_isolated() -> None:
    """Verify duplicate source consolidation and candidate promotion in an isolated SQLite database."""
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
    win, lose = JARVIS_DUPLICATE_SOURCE_PAIRS[0]
    conn.execute("INSERT INTO source VALUES (?, NULL)", (win,))
    conn.execute("INSERT INTO source VALUES (?, NULL)", (lose,))
    conn.execute("INSERT INTO source VALUES ('source-7b9e83133ff41f2cc54a8d24', 'old_ref')")
    conn.execute("INSERT INTO source_version VALUES ('sv-1', ?, 1)", (win,))
    conn.execute("INSERT INTO source_version VALUES ('sv-2', ?, 1)", (lose,))
    conn.execute(
        "INSERT INTO assertion VALUES ('a-1', ?, 'candidate', 0, 'unreviewed', 'current', NULL, NULL, 0, NULL, 'n-1', '2026-10-01')",
        (lose,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('a-2', 'source-7b9e83133ff41f2cc54a8d24', 'candidate', 0, 'unreviewed', 'superseded', 'a-1', '2026-10-01', 0, NULL, NULL, '2026-10-01')"
    )

    merged = _consolidate_jarvis_duplicate_sources(conn)
    assert merged == 1
    stats = _promote_jarvis_candidates(conn, "2026-10-25T18:00:00Z")
    assert stats["total_promoted"] == 2
    rem = conn.execute("SELECT COUNT(*) FROM assertion WHERE canonical_status = 'candidate'").fetchone()[0]
    assert rem == 0
    assert len(JARVIS_CANONICAL_SOURCES) == 16
    conn.close()
