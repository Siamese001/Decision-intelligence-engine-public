"""Wave 1 E2E & Adversarial Tests: Deterministic Source Identity & Ingestion Fallout Guard."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.graph_writer_core.sources import SourceOpsMixin, normalize_source_archive_stem
from dual_pipeline.signal_extraction import SignalExtractor
from tools.prepare_evidence_envelope_intake import (
    _adapt_v53_projection_to_envelope,
    _validate_non_degenerate_source_identity,
)


def _create_sandbox_graph(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
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
            evidence_fingerprint TEXT,
            source_id TEXT,
            source_version_id TEXT,
            locator_kind TEXT,
            locator_value TEXT,
            page_number TEXT,
            section_name TEXT,
            paragraph_number TEXT,
            row_reference TEXT,
            slide_number TEXT,
            transcript_timestamp TEXT,
            direct_quote TEXT,
            evidence_directness TEXT,
            source_authority_rank INTEGER,
            extraction_confidence REAL,
            corroboration_group TEXT,
            location_status TEXT,
            created_at TEXT
        );
        """
    )
    return conn


def test_e2e_multi_path_reingestion_converges_to_single_source_id(tmp_path: Path) -> None:
    """Category: Exact production regression & Rollback/recovery (10-22 4-source split)."""
    db_path = tmp_path / "sandbox_graph.sqlite"
    conn = _create_sandbox_graph(db_path)
    writer = SourceOpsMixin()

    variants = [
        {
            "source_id": "source-d335b3123d936158964e8f55",
            "source_version_id": "source-version-01",
            "name": "LENS Staffing Transitions and Delivery Updates",
            "date": "2026-10-22",
            "source_location": "local://.local/input/lens_resourcing_transition_10_22_2026.md",
            "package_digest": "a" * 64,
            "evidence": [],
        },
        {
            "source_id": "source-761215e9914c6518cd6fcc35",
            "source_version_id": "source-version-02",
            "name": "Document 20261022 Lens Resourcing Transition",
            "date": "2026-10-20",
            "source_location": "repo_root/.local/input/lens_resourcing_transition_10_22_2026.md",
            "package_digest": "b" * 64,
            "evidence": [],
        },
        {
            "source_id": "source-234fb5b63270645fe22c1344",
            "source_version_id": "source-version-03",
            "name": "Document 20261022 Lens Staffing Transitions Delivery Updates",
            "date": "2026-10-20",
            "source_location": "repo_root/.local/input/lens_resourcing_transition_10_22_2026.md",
            "package_digest": "c" * 64,
            "evidence": [],
        },
        {
            "source_id": "source-9619ee4078949a31625f7473",
            "source_version_id": "source-version-04",
            "name": "Document 20261022 Lens Staffing Transitions Delivery Amit",
            "date": "2026-10-22",
            "source_location": "repo_root/.worktrees/ingest_doc_batch_reingest_last_20_documents/output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Amit/source_document.txt",
            "package_digest": "d" * 64,
            "evidence": [],
        },
    ]

    for idx, var in enumerate(variants):
        writer._ensure_source(conn, var, f"2026-10-25T12:0{idx}:00Z")
    conn.commit()
    conn.close()

    # Independent read-only verification
    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    source_count = ro_conn.execute("SELECT count(*) FROM source").fetchone()[0]
    unknown_count = ro_conn.execute("SELECT count(*) FROM source WHERE source_name = 'Unknown'").fetchone()[0]
    version_count = ro_conn.execute("SELECT count(*) FROM source_version WHERE source_id = 'source-d335b3123d936158964e8f55'").fetchone()[0]
    ro_conn.close()

    assert source_count == 1
    assert unknown_count == 0
    assert version_count == 4


def test_adversarial_boundary_and_malformed_degenerate_identities(tmp_path: Path) -> None:
    """Categories: Boundary payload, Malformed input, Invariant bypass attempt."""
    extractor = SignalExtractor()

    # 1. Boundary payload: empty meeting_id and title must fail closed instead of minting 'meeting-unknown'
    with pytest.raises(ValueError, match="meeting-unknown"):
        extractor.extract_and_gate(
            {"schema_version": "5.7", "evidence_index": {}, "graph_mutations": {}},
            metadata={},
        )

    # 2. Invariant bypass attempt: explicit 'meeting-unknown' and 'Unknown' rejected
    with pytest.raises(ValueError, match="Degenerate source identity rejected"):
        _validate_non_degenerate_source_identity("meeting-unknown", "Unknown")

    # 3. Malformed input: raw 12-hex hash title ('Document 5B6473Ba8471') rejected without path anchor
    with pytest.raises(ValueError, match="Degenerate source identity rejected"):
        _validate_non_degenerate_source_identity("document-5b6473ba8471", "Document 5B6473Ba8471")

    # 4. Path-anchored recovery when draft has degenerate 'meeting-unknown' but valid file path exists
    src_file = tmp_path / "output" / "project_lens" / "10-01-2026" / "Rules_Creation_Jen_Amit" / "01_Rules_Creation_Jen_Amit_10-01-2026_hitl_resolved_transcript.md"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("# Transcript\n[00:01] **Amit**: Welcome everyone to the rules creation session.", encoding="utf-8")

    env = _adapt_v53_projection_to_envelope(
        {"meeting_id": "meeting-unknown", "evidence_index": {}, "graph_mutations": {}},
        source_path=src_file,
    )
    assert env["meeting"]["meeting_id"] == "meeting-rules-creation-jen-amit"
    assert env["meeting"]["title"] == "Rules Creation Jen Amit"


def test_adversarial_writer_rejects_unknown_source_bucket(tmp_path: Path) -> None:
    """Category: Invariant bypass attempt against SourceOpsMixin."""
    db_path = tmp_path / "reject_unknown.sqlite"
    conn = _create_sandbox_graph(db_path)
    writer = SourceOpsMixin()

    with pytest.raises(RuntimeError, match="Degenerate source metadata rejected"):
        writer._ensure_source(
            conn,
            {
                "source_id": "source-cb6118d820a3ebd9b8e6c576",
                "source_version_id": "sv-1",
                "name": "Unknown",
                "date": "2026-10-01",
                "source_location": "some_path.md",
            },
            "2026-10-25T12:00:00Z",
        )
    conn.close()


def test_normalize_source_archive_stem_strips_staging_and_worktree() -> None:
    """Verify cross-worktree and staging path normalization."""
    s1 = normalize_source_archive_stem(
        "repo_root/output/project_lens/10-14-2026/.staging_Jarvis_Daniel_PartnerAdvisory_1791994417126/01_Jarvis_Daniel_PartnerAdvisory_10-14-2026_0258PM_ET_hitl_resolved_transcript.md"
    )
    s2 = normalize_source_archive_stem(
        "Jarvis_Daniel_PartnerAdvisory/01_Jarvis_Daniel_PartnerAdvisory_10-14-2026_0258PM_ET_hitl_resolved_transcript.md"
    )
    assert s1 == "jarvis_daniel_partner_advisory"
    assert s2 == "jarvis_daniel_partner_advisory"
