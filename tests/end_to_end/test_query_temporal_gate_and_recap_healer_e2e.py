"""End-to-end verification for BUG-01 through BUG-05:
- BUG-01: Stage 02 Recap Healer Meeting Objective normalization and deterministic injection + X2 gate pass.
- BUG-02: Canonical enterprise directory role resolution for Michael Reiss / Michael Rice.
- BUG-03: Fail-closed temporal source existence check (NOT_IN_KNOWLEDGE_GRAPH vs FOUND_IN_KNOWLEDGE_GRAPH).
- BUG-04: Unpromoted worktree and DLQ blocker diagnostics when requested meeting is absent from graph.sqlite.
- BUG-05: Deliverable alias binding ('outputs 1 and 2' -> 01_* and 02_* synthesis artifacts).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.query_router import (
    extract_temporal_target_date,
    resolve_deliverable_aliases,
    verify_temporal_source_existence,
)
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


def _create_isolated_graph_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT,
            source_date TEXT,
            cold_archive_ref TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source (source_id, source_name, source_date, cold_archive_ref, created_at)
        VALUES (
            'source-42e0afd605216c9c580f32d9',
            '20261001 Phase1 Sow Progress Amit Mandy Jen',
            '2026-10-01',
            'archive/10-01-2026/Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory.md',
            '2026-10-20T02:42:18Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source (source_id, source_name, source_date, cold_archive_ref, created_at)
        VALUES (
            'source-681a857685a8f482dc059699',
            '20261027 Traci Amit Catch Up',
            '2026-10-27',
            'archive/10-27-2026/Traci_Amit_Catch_Up.md',
            '2026-10-27T17:34:36Z'
        )
        """
    )
    conn.commit()
    return conn


def test_e2e_bug01_recap_healer_normalizes_overview_and_passes_x2_gate() -> None:
    raw_recap_with_overview = (
        "# Subject: Discuss PRD | 10/27/2026\n\n"
        "**Date:** 10/27/2026\n"
        "**Time:** 03:33 PM - 03:58 PM ET\n"
        "**Attendees:**\n"
        "- harper Hoffman (Product Manager, Apex Global Analytics)\n"
        "- Amit Ayer (Principal Architect & Technology Strategist, PartnerAdvisory)\n\n"
        "# Overview\n\n"
        "The team aligned on PRD closure requirements and Phase 1 SOW metrics.\n\n"
        "# Key Discussion Points & Findings\n\n"
        "- **harper Hoffman reviewed Phase 1 SOW completion criteria:** Details discussed. [00:05:32]\n\n"
        "# Action & Governance Ledger\n\n"
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Action | Deliver updated metric definitions sheet to harper Hoffman | Amit Ayer | 10/28/2026 | [00:19:05] | Confirmed |\n"
    )
    healed = heal_output_2_recap(
        raw_recap_with_overview,
        metadata={"meeting_name": "Discuss PRD", "date": "10/27/2026"},
    )
    assert "Meeting Objective" in healed
    evaluator = XExitGatesEvaluator()
    evaluator._validate_recap_structure(healed)


def test_e2e_bug01_recap_healer_injects_missing_meeting_objective_and_passes_x2() -> None:
    raw_recap_missing_objective = (
        "# Subject: Discuss PRD | 10/27/2026\n\n"
        "**Date:** 10/27/2026\n"
        "**Time:** 03:33 PM - 03:58 PM ET\n"
        "**Attendees:**\n"
        "- harper Hoffman (Product Manager, Apex Global Analytics)\n"
        "- Amit Ayer (Principal Architect & Technology Strategist, PartnerAdvisory)\n\n"
        "# Key Discussion Points & Findings\n\n"
        "- **harper Hoffman reviewed Phase 1 SOW completion criteria:** Details discussed. [00:05:32]\n\n"
        "# Action & Governance Ledger\n\n"
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Action | Deliver updated metric definitions sheet to harper Hoffman | Amit Ayer | 10/28/2026 | [00:19:05] | Confirmed |\n"
    )
    healed = heal_output_2_recap(
        raw_recap_missing_objective,
        metadata={"meeting_name": "Discuss PRD", "date": "10/27/2026"},
    )
    assert "Meeting Objective" in healed
    evaluator = XExitGatesEvaluator()
    evaluator._validate_recap_structure(healed)


def test_e2e_bug02_canonical_enterprise_directory_resolves_michael_reiss() -> None:
    dir_path = (
        Path(__file__).resolve().parents[2]
        / "dual_pipeline"
        / "transcript_metadata_core"
        / "canonical_enterprise_directory.json"
    )
    directory = json.loads(dir_path.read_text(encoding="utf-8"))
    assert directory.get("michael reiss") == [
        "Michael Reiss",
        "Apex Global Enterprises (NEA)",
        "Data Scientist",
    ]
    assert directory.get("michael rice") == [
        "Michael Reiss",
        "Apex Global Enterprises (NEA)",
        "Data Scientist",
    ]


def test_e2e_bug03_bug04_bug05_temporal_gate_and_dlq_diagnostics(tmp_path: Path) -> None:
    conn = _create_isolated_graph_db(tmp_path / "graph.sqlite")
    repo_root = tmp_path / "repo"
    wt_dir = repo_root / ".worktrees" / "audio_project_lens_10_27_2026_prd_eduardo_partner_advisory"
    wt_dir.mkdir(parents=True)
    rt_root = repo_root / ".local" / "dual_pipeline"
    dlq_dir = rt_root / "dlq"
    dlq_dir.mkdir(parents=True)
    dlq_file = dlq_dir / "dlq_20261027_204113_7cfa0441.json"
    dlq_file.write_text(
        json.dumps({
            "dlq_id": "dlq_20261027_204113_7cfa0441",
            "stage": "X_EXIT_GATES",
            "error_type": "ValueError",
            "error_message": "X2 FAIL: Recap missing 'Meeting Objective' section",
            "stable_op_id": "directive-project_lens-discuss_prd-10-27-2026-v1",
            "metadata": {
                "project": "project_lens",
                "meeting_name": "Discuss PRD",
                "date": "10/27/2026",
                "attendees": "harper Hoffman, Eduardo Juan More, Amit Ayer",
            },
        }),
        encoding="utf-8",
    )

    try:
        query = (
            "-query Summarize outputs 1 and 2 recap for TODAY's completed LENS mandy PRD "
            "meeting 3:30 pm et - audience PartnerAdvisory team to define action items and complete this LENS SOW 1 scope"
        )
        res = verify_temporal_source_existence(
            query,
            conn=conn,
            reference_date="2026-10-27",
            repo_root=repo_root,
            runtime_root=rt_root,
        )
        assert res["status"] == "NOT_IN_KNOWLEDGE_GRAPH"
        assert res["requested_date"] == "2026-10-27"
        assert res["matching_sources"] == []
        assert res["deliverable_aliases_requested"] == [
            "01_hitl_resolved_transcript.md",
            "02_executive_intelligence_report.md",
        ]
        assert len(res["unpromoted_worktrees"]) == 1
        assert res["unpromoted_worktrees"][0]["worktree_slug"] == "audio_project_lens_10_27_2026_prd_eduardo_partner_advisory"
        assert len(res["dlq_blockers"]) == 1
        assert res["dlq_blockers"][0]["stage"] == "X_EXIT_GATES"
        assert "not in the Knowledge Graph" in res["message"]

        # Verify a query for an actually ingested 2026-10-27 meeting succeeds
        res_traci = verify_temporal_source_existence(
            "Summarize today's Traci Amit catch up meeting",
            conn=conn,
            reference_date="2026-10-27",
            repo_root=repo_root,
            runtime_root=rt_root,
        )
        assert res_traci["status"] == "FOUND_IN_KNOWLEDGE_GRAPH"
        assert len(res_traci["matching_sources"]) == 1
        assert res_traci["matching_sources"][0]["source_id"] == "source-681a857685a8f482dc059699"
    finally:
        conn.close()
