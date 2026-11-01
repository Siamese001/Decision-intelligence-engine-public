"""Adversarial and edge-case tests for BUG-01 through BUG-05:
Covers mandatory categories:
1. Boundary payload (empty strings, None inputs, missing metadata, zero-source DB).
2. Malformed input (corrupted JSON in DLQ directory, malformed dates, non-standard markdown headers).
3. Invariant bypass attempt (attempting silent historical fallback when a future or unpromoted date is requested).
4. Exact production regression (exact 2026-10-27 3:30 PM ET Mandy PRD meeting failure scenario).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.query_router import (
    extract_meeting_topic_tokens,
    extract_temporal_target_date,
    resolve_deliverable_aliases,
    scan_unpromoted_dlq_and_worktrees,
    verify_temporal_source_existence,
)
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


def test_adversarial_boundary_payloads(tmp_path: Path) -> None:
    """Category 1: Boundary payloads (empty strings, None values, empty DB)."""
    assert resolve_deliverable_aliases("") == []
    assert resolve_deliverable_aliases("   ") == []
    assert extract_temporal_target_date("", reference_date="2026-10-27") is None
    assert extract_meeting_topic_tokens("") == []
    assert heal_output_2_recap("", metadata=None) == ""

    no_date_res = verify_temporal_source_existence(
        "What is the overall status of Project LENS?",
        conn=None,
        reference_date="2026-10-27",
        repo_root=tmp_path,
        runtime_root=tmp_path,
    )
    assert no_date_res["status"] == "NO_TEMPORAL_CONSTRAINT"
    assert no_date_res["requested_date"] is None


def test_adversarial_malformed_dlq_and_dates(tmp_path: Path) -> None:
    """Category 2: Malformed input (corrupted DLQ JSON, invalid date strings, numbered headers)."""
    rt_root = tmp_path / ".local" / "dual_pipeline"
    dlq_dir = rt_root / "dlq"
    dlq_dir.mkdir(parents=True)
    (dlq_dir / "dlq_20261027_corrupt.json").write_text("{corrupted_json: true", encoding="utf-8")

    diag = scan_unpromoted_dlq_and_worktrees(
        "2026-10-27",
        ["mandy", "prd"],
        repo_root=tmp_path,
        runtime_root=rt_root,
    )
    assert diag == {"dlq_blockers": [], "unpromoted_worktrees": []}

    # Invalid reference date gracefully falls back without raising
    target = extract_temporal_target_date("today's meeting", reference_date="not-a-valid-date")
    assert target is not None and len(target) == 10

    # Numbered Executive Summary header normalizes to Meeting Objective
    numbered_recap = (
        "# Subject: LENS Sync | 10/27/2026\n\n"
        "**Date:** 10/27/2026\n"
        "**Attendees:**\n- Amit Ayer (Principal Architect, PartnerAdvisory)\n\n"
        "## 1. Executive Summary\n\n"
        "Reviewed LENS Phase 1 deliverables.\n\n"
        "## 2. Action & Governance Ledger\n\n"
        "No formal actions were established in this session; open initiatives remain unresolved questions.\n"
    )
    healed = heal_output_2_recap(numbered_recap, metadata={"date": "10/27/2026"})
    XExitGatesEvaluator()._validate_recap_structure(healed)


def test_adversarial_invariant_bypass_prevents_historical_substitution(tmp_path: Path) -> None:
    """Category 3: Invariant bypass attempt — older sources for the same entity must NEVER satisfy a today query."""
    conn = sqlite3.connect(str(tmp_path / "graph.sqlite"))
    conn.execute(
        "CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT, source_date TEXT, cold_archive_ref TEXT)"
    )
    for dt in ("2026-09-15", "2026-10-01", "2026-10-02", "2026-10-21"):
        conn.execute(
            "INSERT INTO source VALUES (?, ?, ?, ?)",
            (f"src-{dt}", f"Project LENS Mandy PRD SOW {dt}", dt, f"archive/{dt}/lens_mandy_prd.md"),
        )
    conn.commit()
    try:
        res = verify_temporal_source_existence(
            "Summarize Talking recap points for the mandy PRD meeting 3:30 pm et today",
            conn=conn,
            reference_date="2026-10-27",
            repo_root=tmp_path,
            runtime_root=tmp_path,
        )
        assert res["status"] == "NOT_IN_KNOWLEDGE_GRAPH"
        assert res["matching_sources"] == []
    finally:
        conn.close()


def test_adversarial_exact_production_regression_outputs_1_and_2_aliases() -> None:
    """Category 4: Exact production regression for 'outputs 1 and 2' and multi-format dates."""
    aliases = resolve_deliverable_aliases(
        "-query Summarize outputs 1 and 2 recap for TODAY's completed LENS mandy PRD meeting 3:30 pm et"
    )
    assert aliases == [
        "01_hitl_resolved_transcript.md",
        "02_executive_intelligence_report.md",
    ]
    assert resolve_deliverable_aliases("Review Deliverable 01, Deliverable 02, and Output 3") == [
        "01_hitl_resolved_transcript.md",
        "02_executive_intelligence_report.md",
        "03_canonical_signal_projection.json",
    ]
    assert extract_temporal_target_date("Meeting on 10/27/2026 at 3:30 PM ET") == "2026-10-27"
    assert extract_temporal_target_date("Meeting on 10-27-2026 at 3:30 PM ET") == "2026-10-27"
    assert extract_temporal_target_date("Source 20261027 Mandy PRD") == "2026-10-27"
