"""End-to-end verification tests for 09-25-2026 Google Partner Alliances and Project LENS 1:1 split."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.transcript_qa import evaluate_all_qa_gates
from tools.tier2_dedup_core.nexus_gap_remediation import (
    _GOOGLE_0925_SOURCE_ID,
    _GOOGLE_LENS_ENTITY_ID,
    _LENS_0925_SOURCE_ID,
    _TRACI_0925_SOURCE_ID,
    _apply_0925_split_sql,
)


def test_split_0925_deliverable_containers_and_qa_gates() -> None:
    """Verify both Partner Alliances and Project LENS 09-25-2026 containers exist and pass all QA gates."""
    wt_root = Path(__file__).resolve().parents[1]
    ga_dir = wt_root / "output" / "partner_alliances" / "09-25-2026" / "Google_Alex_Shreyak_Amit_Anya"
    lens_dir = wt_root / "output" / "project_lens" / "09-25-2026" / "Lens_Escalation_Jen_Amit"

    assert ga_dir.is_dir()
    assert lens_dir.is_dir()

    f1_ga = (ga_dir / "01_Google_Alex_Shreyak_Amit_Anya_09-25-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md").read_text(encoding="utf-8")
    f2_ga = (ga_dir / "02_Google_Alex_Shreyak_Amit_Anya_09-25-2026_0900AM_1000AM_ET_executive_recap.md").read_text(encoding="utf-8")
    f3_ga = json.loads((ga_dir / "03_Google_Alex_Shreyak_Amit_Anya_09-25-2026_0900AM_1000AM_ET_canonical_signal_projection.json").read_text(encoding="utf-8"))
    meta_ga = json.loads((ga_dir / "metadata.json").read_text(encoding="utf-8"))

    assert "Jen Pan" not in f1_ga
    assert "Taylor Mercer" not in f1_ga
    res_ga = evaluate_all_qa_gates(f1_ga, f2_ga, f3_ga, f1_ga, meta_ga)
    assert res_ga["passed"] is True

    f1_lens = (lens_dir / "01_Lens_Escalation_Jen_Amit_09-25-2026_0923AM_0948AM_ET_hitl_resolved_transcript.md").read_text(encoding="utf-8")
    f2_lens = (lens_dir / "02_Lens_Escalation_Jen_Amit_09-25-2026_0923AM_0948AM_ET_executive_recap.md").read_text(encoding="utf-8")
    f3_lens = json.loads((lens_dir / "03_Lens_Escalation_Jen_Amit_09-25-2026_0923AM_0948AM_ET_canonical_signal_projection.json").read_text(encoding="utf-8"))
    meta_lens = json.loads((lens_dir / "metadata.json").read_text(encoding="utf-8"))

    assert "Jen Pan" in f1_lens
    assert "Taylor Mercer" not in f1_lens
    res_lens = evaluate_all_qa_gates(f1_lens, f2_lens, f3_lens, f1_lens, meta_lens)
    assert res_lens["passed"] is True


def test_split_0925_sql_remediation_isolated() -> None:
    """Verify _apply_0925_split_sql cleanly separates Google, LENS, and Traci assertions in an isolated SQLite DB."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    conn.executescript(
        """
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
            evidence_id TEXT PRIMARY KEY, source_id TEXT, source_version_id TEXT,
            locator_kind TEXT, locator_value TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
            object_node_id TEXT, primary_source_id TEXT, extraction_run_id TEXT,
            canonical_status TEXT, updated_at TEXT
        );
        CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT);
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, assertion_id TEXT, source_id TEXT, updated_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO source (source_id) VALUES (?)", (_GOOGLE_0925_SOURCE_ID,))
    conn.execute("INSERT INTO source (source_id) VALUES (?)", (_TRACI_0925_SOURCE_ID,))
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-lens-1', ?, 'sv-old', 'timestamp', '00:36:18')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-traci-1', ?, 'sv-old', 'timestamp', '00:55:13')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('assertion-21b32e04a1145fe6f939f3c65dfb', 'workstream_lens', 'HAS_CLAIM_TEXT', NULL, ?, 'er-old', 'canonical', '2026-10-01')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-lens-sow', 'person_jen_pan', 'HAS_CLAIM_TEXT', NULL, ?, 'er-old', 'canonical', '2026-10-01')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-traci-jarvis', 'entity-c7808e04f0add3cf3751911034a3', 'HAS_CLAIM_TEXT', NULL, ?, 'er-old', 'canonical', '2026-10-01')",
        (_GOOGLE_0925_SOURCE_ID,),
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-lens-sow', 'ev-lens-1')")
    conn.execute("INSERT INTO search_document VALUES ('sd-lens', 'asrt-lens-sow', ?, '2026-10-01')", (_GOOGLE_0925_SOURCE_ID,))

    stats = _apply_0925_split_sql(conn, "2026-10-29T10:55:00Z")
    assert stats["repointed_google_assertions"] == 1
    assert stats["rebound_lens_evidence_items"] == 1
    assert stats["rebound_traci_evidence_items"] == 1
    assert stats["rebound_lens_assertions"] == 1
    assert stats["rebound_traci_assertions"] == 1

    subj_google = conn.execute("SELECT subject_node_id FROM assertion WHERE assertion_id='assertion-21b32e04a1145fe6f939f3c65dfb'").fetchone()[0]
    assert subj_google == _GOOGLE_LENS_ENTITY_ID
    src_lens = conn.execute("SELECT primary_source_id FROM assertion WHERE assertion_id='asrt-lens-sow'").fetchone()[0]
    assert src_lens == _LENS_0925_SOURCE_ID
    src_traci = conn.execute("SELECT primary_source_id FROM assertion WHERE assertion_id='asrt-traci-jarvis'").fetchone()[0]
    assert src_traci == _TRACI_0925_SOURCE_ID
    sd_src = conn.execute("SELECT source_id FROM search_document WHERE search_document_id='sd-lens'").fetchone()[0]
    assert sd_src == _LENS_0925_SOURCE_ID
    conn.close()
