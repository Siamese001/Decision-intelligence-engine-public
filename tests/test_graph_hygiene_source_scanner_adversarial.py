"""Wave 2 E2E & Adversarial Tests: First-Class Source Table & Cross-Project Hygiene Scanners."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.hygiene_agent import ProvenanceInspectorAgent
from dual_pipeline.graph_scout_core.scans import scan_duplicate_and_malformed_sources
from tools.validate_output_hierarchy import validate_meeting_directory
from tools.validate_output_hierarchy_core.helpers import _validate_project_affinity


def _seed_gap4_source_db(db_path: Path) -> sqlite3.Connection:
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
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            validation_state TEXT,
            confidence_score REAL,
            retrieval_eligible INTEGER
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            assertion_id TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER
        );
        """
    )
    # 1. Seed the 4 duplicate 10-22 LENS Staffing Transition sources
    conn.executemany(
        "INSERT INTO source (source_id, source_type, source_name, source_date, cold_archive_ref) VALUES (?, 'dual_pipeline_intake', ?, ?, ?)",
        [
            ("source-d335b3123d936158964e8f55", "LENS Staffing Transitions and Delivery Updates", "2026-10-22", "local://.local/input/lens_resourcing_transition_10_22_2026.md"),
            ("source-761215e9914c6518cd6fcc35", "Document 20261022 Lens Resourcing Transition", "2026-10-20", "repo_root/.local/input/lens_resourcing_transition_10_22_2026.md"),
            ("source-234fb5b63270645fe22c1344", "Document 20261022 Lens Staffing Transitions Delivery Updates", "2026-10-20", "repo_root/.local/input/lens_resourcing_transition_10_22_2026.md"),
            ("source-9619ee4078949a31625f7473", "Document 20261022 Lens Staffing Transitions Delivery Amit", "2026-10-22", "repo_root/.worktrees/ingest_doc_batch_reingest_last_20_documents/output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Amit/source_document.txt"),
            # 2. Seed 'Unknown' bucket + legitimate counterpart + hex-hash title
            ("source-cb6118d820a3ebd9b8e6c576", "Unknown", "2026-10-01", "Rules_Creation_Jen_Amit_Alex_Jen/01_Rules_Creation_Jen_Amit_Alex_Jen_10-01-2026_0444PM0455PMET_hitl_resolved_transcript.md"),
            ("source-2af61f674935afe8672a6881", "20261001 Rules Creation Jen Amit", "2026-10-01", "Rules_Creation_Jen_Amit_Alex_Jen/01_Rules_Creation_Jen_Amit_Alex_Jen_10-01-2026_0444PM0455PMET_hitl_resolved_transcript.md"),
            ("source-e09492e1b058c21ead1e0626", "Document 5B6473Ba8471", "2026-10-12", "some_doc.md"),
        ],
    )
    # Seed 3 multi-date source_versions under 'source-cb6118d820a3ebd9b8e6c576'
    conn.executemany(
        "INSERT INTO source_version (source_version_id, source_id, version_number, source_date_normalized) VALUES (?, ?, ?, ?)",
        [
            ("sv-1", "source-cb6118d820a3ebd9b8e6c576", 1, "2026-10-01"),
            ("sv-2", "source-cb6118d820a3ebd9b8e6c576", 2, "2026-10-06"),
            ("sv-3", "source-cb6118d820a3ebd9b8e6c576", 3, "2025-11-19"),
        ],
    )
    conn.commit()
    return conn


def test_provenance_inspector_detects_duplicate_sources_and_unknown_bucket(tmp_path: Path) -> None:
    """Category: Exact production regression (Gap 4 10-22 4-source split & Unknown bucket)."""
    db_path = tmp_path / "gap4_graph.sqlite"
    conn = _seed_gap4_source_db(db_path)

    raw = scan_duplicate_and_malformed_sources(conn)
    dup_clusters = [f for f in raw if f.finding_type == "duplicate_source_cluster"]
    degen = [f for f in raw if f.finding_type == "degenerate_source_metadata"]
    multi_date = [f for f in raw if f.finding_type == "multi_date_source_collision"]

    assert len(dup_clusters) == 2
    cluster_sizes = sorted(len(c.details["member_source_ids"]) for c in dup_clusters)
    assert cluster_sizes == [2, 4]
    assert {f.target_id for f in degen} == {"source-cb6118d820a3ebd9b8e6c576", "source-e09492e1b058c21ead1e0626"}
    assert len(multi_date) == 1
    assert multi_date[0].target_id == "source-cb6118d820a3ebd9b8e6c576"

    agent = ProvenanceInspectorAgent()
    findings = agent.audit(db_path, conn)
    conn.close()

    fixes = {f.proposed_fix for f in findings}
    assert "consolidate_duplicate_sources" in fixes
    assert "unmerge_or_retitle_degenerate_source" in fixes


def test_validate_output_hierarchy_flags_misrouted_hrit_sow_in_project_lens(tmp_path: Path) -> None:
    """Categories: Exact production regression & Invariant bypass attempt (Hrit_Sow_V3_Final in project_lens)."""
    # 1. Direct helper check
    err = _validate_project_affinity(
        "Hrit_Sow_V3_Final_10-08-2026_0229PM_ET",
        "project_lens",
        "project_lens/10-08-2026/Hrit_Sow_V3_Final",
    )
    assert err is not None
    assert "[PROJECT_AFFINITY_MISMATCH]" in err
    assert "hrit" in err

    # 2. Full directory container check in isolated sandbox
    date_dir = tmp_path / "output" / "project_lens" / "10-08-2026"
    hrit_sub = date_dir / "Hrit_Sow_V3_Final"
    hrit_sub.mkdir(parents=True, exist_ok=True)
    (hrit_sub / "metadata.json").write_text(json.dumps({"is_document": True}), encoding="utf-8")
    (hrit_sub / "source_document.txt").write_text("HRIT SOW V3 Final document text.", encoding="utf-8")
    (hrit_sub / "03_Hrit_Sow_V3_Final_10-08-2026_0229PM_ET_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
    for r in ("qa_gates_report.json", "llm_judge_evaluation.json", "final_release_decision.json", "intake_package.json"):
        (hrit_sub / r).write_text(json.dumps({"status": "PASS", "authoritative_write_authorized": True}), encoding="utf-8")

    violations = validate_meeting_directory(date_dir, project_name="project_lens")
    assert any("[PROJECT_AFFINITY_MISMATCH]" in v and "Hrit_Sow_V3_Final" in v for v in violations)


def test_adversarial_empty_and_retired_source_tables(tmp_path: Path) -> None:
    """Categories: Boundary payload & Malformed input."""
    db_path = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(str(db_path))
    assert scan_duplicate_and_malformed_sources(conn) == []

    conn.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT, source_date TEXT, cold_archive_ref TEXT)")
    conn.execute(
        "INSERT INTO source VALUES ('source-111', 'Retired - Duplicate Source', '2026-10-08', 'Hrit_Sow_V3_Final/source_document.txt')"
    )
    conn.execute(
        "INSERT INTO source VALUES ('source-222', 'Retired - Duplicate Source 2', '2026-10-08', 'Hrit_Sow_V3_Final/source_document.txt')"
    )
    conn.commit()
    # Retired tombstones must not trigger active duplicate cluster alarms
    assert scan_duplicate_and_malformed_sources(conn) == []

    # Distinct same-day stakeholder interview sessions (Session 1, Session 2, Session 3, Session Wrapup) must NOT merge
    conn.executemany(
        "INSERT INTO source VALUES (?, ?, ?, ?)",
        [
            ("source-s2", "20260917 apex_trans Insights Hub Input Session 2", "2026-09-17", "output/apex_trans/09-17-2026/Neet_Insights_Hub_Christina_PartnerAdvisory/01_transcript.md"),
            ("source-s3", "20260917 apex_trans Insights Hub Input Session 3", "2026-09-17", "output/apex_trans/09-17-2026/Neet_Insights_Hub_Input_Krystal_PartnerAdvisory/01_transcript.md"),
            ("source-s1", "20260918 apex_trans Insights Hub Input Session 1", "2026-09-18", "output/apex_trans/09-18-2026/Insights_Hub_Input_Mitchell_PartnerAdvisory/01_transcript.md"),
            ("source-sw", "20260918 apex_trans Insights Hub Input Session Wrapup", "2026-09-18", "output/apex_trans/09-18-2026/Insights_Hub_Cieran-patrick_Dominic/01_transcript.md"),
        ],
    )
    conn.commit()
    assert scan_duplicate_and_malformed_sources(conn) == []
    conn.close()
