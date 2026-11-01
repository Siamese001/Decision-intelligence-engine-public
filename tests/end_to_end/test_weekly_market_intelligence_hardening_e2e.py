"""tests/end_to_end/test_weekly_market_intelligence_hardening_e2e.py

End-to-end and adversarial verification for Weekly Market Intelligence hardening.
Tests 5 distinct execution paths:
1. Zero-Signal Path -> COMPLETE_NOOP, 0 graph mutations.
2. Duplicate-Signal Path -> COMPLETE_NOOP, 0 graph mutations against existing claims.
3. Contradictory-Source Path -> Preserves both claims, 0 invented consensus.
4. Approval-Pending Path -> AWAITING_APPROVAL, worktree retained, 0 mutations.
5. Successful-Publication Path -> COMPLETE, same-directive resumed without re-extraction, 5 physical checks passed.
Includes Before / After signal quality comparison proving high-signal filtering.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tools.scheduled_task_core import (
    AntigravityHostLaunchAdapter,
    ApprovalRecord,
    ExecutionMode,
    TriggerOrigin,
)
from tools.scheduled_task_core.adapters_core.intelligence import (
    run_weekly_market_intelligence,
)
from tools.scheduled_task_core.adapters_core.intelligence_engine import (
    CandidateSignal,
    evaluate_candidate_signals,
    verify_physical_mutation_proof,
)


@pytest.fixture
def mock_sandbox_env(tmp_path: Path) -> dict[str, Any]:
    """Sets up an isolated disposable sandbox with 14-table SQLite knowledge graph."""
    fixture_root = tmp_path / "sandbox_env"
    fixture_root.mkdir(parents=True, exist_ok=True)

    graph_db = fixture_root / "graph.db"
    conn = sqlite3.connect(graph_db, timeout=30.0)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE node (id TEXT PRIMARY KEY, label TEXT);
        CREATE TABLE source (id TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE source_version (id TEXT PRIMARY KEY, source_id TEXT);
        CREATE TABLE extraction_run (id TEXT PRIMARY KEY);
        CREATE TABLE evidence_item (id TEXT PRIMARY KEY, quote TEXT);
        CREATE TABLE assertion (id TEXT PRIMARY KEY, entity_id TEXT, claim TEXT);
        CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_item_id TEXT, relevance_score REAL);
        CREATE TABLE search_document (id TEXT PRIMARY KEY, content TEXT);
        CREATE TABLE search_fts (id TEXT PRIMARY KEY, terms TEXT);
        CREATE TABLE audit_event (id TEXT PRIMARY KEY, event_type TEXT);
        CREATE TABLE dense_vector (id TEXT PRIMARY KEY, vector BLOB);
        CREATE TABLE sparse_weight (id TEXT PRIMARY KEY, weights BLOB);
        CREATE TABLE colbert_token (id TEXT PRIMARY KEY, token TEXT);
        CREATE TABLE retrieval_record (id TEXT PRIMARY KEY, query TEXT);
        INSERT INTO node VALUES ('node-baseline-01', 'Apex Global Enterprises Baseline');
        INSERT INTO assertion VALUES ('as-baseline-01', 'node-baseline-01', 'Existing market trend claim already present in graph');
        INSERT INTO evidence_item VALUES ('ev-baseline-01', 'Baseline filing 2026');
        INSERT INTO assertion_evidence VALUES ('as-baseline-01', 'ev-baseline-01', 1.0);
    """)
    conn.commit()
    conn.close()

    active_pair = fixture_root / "active_pair.json"
    active_pair.write_text(json.dumps({
        "generation_dir": "gen_001",
        "active_graph_sha256": hashlib.sha256(graph_db.read_bytes()).hexdigest(),
    }), encoding="utf-8")

    return {"fixture_root": fixture_root, "graph_db": graph_db}


def _make_approval(run_id: str, plan_digest: str) -> ApprovalRecord:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    return ApprovalRecord(
        approval_id=f"app-{run_id}",
        task_id="weekly_market_intelligence",
        run_id=run_id,
        operator="operator_human",
        signature="ed25519:valid-human-signature",
        status="APPROVED",
        approved_items=["dir-wmi-01"],
        plan_digest=plan_digest,
        baseline_sha="f54762198f7183e2ec9e37859feafc8828dd5681",
        issued_at=now.isoformat(),
        expires_at=(now + dt.timedelta(hours=2)).isoformat(),
        replay_nonce=f"nonce-wmi-{run_id}",
        is_authentic_human=True,
    )


# --- 1. Zero-Signal Path --------------------------------------------------------

def test_zero_signal_path_produces_complete_noop(mock_sandbox_env: dict[str, Any]) -> None:
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = mock_sandbox_env["fixture_root"]
    run_id = "test-zero-signal"

    context = launcher.build_authority_context(
        task_id="weekly_market_intelligence",
        run_id=run_id,
        mode="scheduled",
    )
    run_dir = fixture_root / "reports" / "weekly_market_intelligence" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Empty candidate signals
    result = run_weekly_market_intelligence(
        context=context,
        fixture_root=fixture_root,
        run_dir=run_dir,
        candidate_signals=[],
    )

    assert result.terminal_outcome == "COMPLETE_NOOP"
    assert result.mutations_count == 0
    assert result.is_read_only is True
    assert result.metadata.get("zero_signal") is True
    assert result.report_path is not None and result.report_path.is_file()

    # Verify 0 graph mutations occurred
    conn = sqlite3.connect(mock_sandbox_env["graph_db"])
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM assertion")
    assert cur.fetchone()[0] == 1  # only baseline remains
    conn.close()


# --- 2. Duplicate-Signal Path ----------------------------------------------------

def test_duplicate_signal_path_rejects_and_avoids_mutation(mock_sandbox_env: dict[str, Any]) -> None:
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = mock_sandbox_env["fixture_root"]
    run_id = "test-dup-signal"

    context = launcher.build_authority_context(
        task_id="weekly_market_intelligence",
        run_id=run_id,
        mode="scheduled",
    )
    run_dir = fixture_root / "reports" / "weekly_market_intelligence" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Signal whose content exactly matches existing claim in SQLite GraphDB
    dup_signals = [
        CandidateSignal(
            title="Duplicate Market Trend Report",
            source_agency="FPSC Docket",
            date="10/14/2026",
            url="https://example.com/dup",
            content="Existing market trend claim already present in graph",
            authority_score=0.90,
            relevance_score=0.95,
            materiality_score=0.90,
            novelty_score=0.10,  # Stale/duplicate
            actionability_score=0.90,
            epistemic_modality="OBSERVED_FACT",
            horizon="7-day",
            quantified_metrics=["500 MW"],
            decision_implication="Duplicate decision context",
        )
    ]

    result = run_weekly_market_intelligence(
        context=context,
        fixture_root=fixture_root,
        run_dir=run_dir,
        candidate_signals=dup_signals,
    )

    assert result.terminal_outcome == "COMPLETE_NOOP"
    assert result.mutations_count == 0


# --- 3. Contradictory-Source Path ------------------------------------------------

def test_contradictory_source_path_preserves_competing_claims(mock_sandbox_env: dict[str, Any]) -> None:
    fixture_root = mock_sandbox_env["fixture_root"]
    contradictory_signals = [
        CandidateSignal(
            title="EIA BESS Cost Decline Forecast",
            source_agency="Energy Information Administration",
            date="10/10/2026",
            url="https://eia.gov/bess",
            content="EIA projects 4-hour utility BESS CAPEX to drop 18% by Q4 2027.",
            authority_score=0.95,
            relevance_score=0.90,
            materiality_score=0.88,
            novelty_score=0.85,
            actionability_score=0.90,
            epistemic_modality="FORECAST",
            horizon="90-day",
            quantified_metrics=["-18% CAPEX", "4-hour duration"],
            decision_implication="Accelerate BESS procurement contracts.",
        ),
        CandidateSignal(
            title="Supply Chain Transformer Shortage Risk",
            source_agency="EPRI Reliability Bulletin",
            date="10/12/2026",
            url="https://epri.com/transformers",
            content="EPRI warns HV transformer lead times extended to 140 weeks, offsetting battery savings.",
            authority_score=0.95,
            relevance_score=0.92,
            materiality_score=0.90,
            novelty_score=0.85,
            actionability_score=0.92,
            epistemic_modality="OBSERVED_FACT",
            horizon="7-day",
            quantified_metrics=["140 weeks lead time"],
            decision_implication="Hedge balance sheet and lengthen procurement horizons.",
            contradicts_signal_id="EIA BESS Cost Decline Forecast",
        ),
    ]

    passed, audit_log = evaluate_candidate_signals(contradictory_signals, graph_db_path=mock_sandbox_env["graph_db"])
    assert len(passed) == 2
    # Verify both competing perspectives are preserved without forced consensus
    assert any(s.epistemic_modality == "FORECAST" for s in passed)
    assert any(s.epistemic_modality == "OBSERVED_FACT" for s in passed)


# --- 4. Approval-Pending Path ----------------------------------------------------

def test_approval_pending_path_retains_worktree_and_directive(mock_sandbox_env: dict[str, Any]) -> None:
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = mock_sandbox_env["fixture_root"]
    run_id = "test-awaiting-approval"

    context = launcher.build_authority_context(
        task_id="weekly_market_intelligence",
        run_id=run_id,
        mode="scheduled",
    )
    run_dir = fixture_root / "reports" / "weekly_market_intelligence" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    result = run_weekly_market_intelligence(
        context=context,
        fixture_root=fixture_root,
        run_dir=run_dir,
    )

    assert result.terminal_outcome == "AWAITING_APPROVAL"
    assert result.mutations_count == 0
    assert result.is_read_only is True
    assert result.metadata.get("worktree_retained") is True
    assert result.approval_request_id == f"app-req-{run_id}"

    # Verify request payload
    req_file = fixture_root / "approval_requests" / f"{run_id}.json"
    assert req_file.is_file()
    req_data = json.loads(req_file.read_text(encoding="utf-8"))
    assert req_data["directive_id"] == "dir-wmi-01"
    assert req_data["worktree_retained"] is True
    assert "layman_summary" in req_data
    assert isinstance(req_data["layman_summary"], list)
    assert "hitl_clusters" in req_data
    assert len(req_data["hitl_clusters"]) == 2
    cluster_ids = {c["cluster_id"] for c in req_data["hitl_clusters"]}
    assert cluster_ids == {"cluster-broader-market-trends", "cluster-apex_global-apex-implications"}
    for cluster in req_data["hitl_clusters"]:
        assert "ready_to_ask_question" in cluster
        assert cluster["ready_to_ask_question"]["options"][0].startswith("(Recommended)")
        for summary_line in cluster["plain_english_summary"]:
            for banned_acronym in ("FPSC", "FERC", "APEX", "BESS"):
                assert banned_acronym not in summary_line


# --- 5. Successful-Publication Path & Physical Proofs ----------------------------

def test_successful_publication_path_and_physical_proof(mock_sandbox_env: dict[str, Any]) -> None:
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = mock_sandbox_env["fixture_root"]
    graph_db = mock_sandbox_env["graph_db"]
    run_id = "test-successful-publication"

    # Step 1: Run scheduled intake to establish sealed directive and digest
    context_sched = launcher.build_authority_context(
        task_id="weekly_market_intelligence",
        run_id=run_id,
        mode="scheduled",
    )
    run_dir_sched = fixture_root / "reports" / "weekly_market_intelligence" / run_id
    run_dir_sched.mkdir(parents=True, exist_ok=True)

    sched_res = run_weekly_market_intelligence(
        context=context_sched,
        fixture_root=fixture_root,
        run_dir=run_dir_sched,
    )
    assert sched_res.terminal_outcome == "AWAITING_APPROVAL"

    req_file = fixture_root / "approval_requests" / f"{run_id}.json"
    req_data = json.loads(req_file.read_text(encoding="utf-8"))
    briefing_digest = req_data["proposed_delta_digest"]
    assert "layman_summary" in req_data
    assert isinstance(req_data["layman_summary"], list)
    assert "hitl_clusters" in req_data
    assert len(req_data["hitl_clusters"]) == 2
    from tools.scheduled_task_core.adapters_core.intelligence_engine import (
        evaluate_candidate_signals,
        filter_signals_by_approved_clusters,
    )
    all_passed, _ = evaluate_candidate_signals(None)
    broader_only = filter_signals_by_approved_clusters(all_passed, ["cluster-broader-market-trends"])
    assert len(broader_only) == 1
    assert "FERC" in broader_only[0].title

    # Step 2: Interactive operator approval resumes same directive without re-extraction
    context_interactive = launcher.build_authority_context(
        task_id="weekly_market_intelligence",
        run_id=run_id,
        mode="interactive",
    )
    approval = _make_approval(run_id, plan_digest=briefing_digest)

    res_interactive = run_weekly_market_intelligence(
        context=context_interactive,
        fixture_root=fixture_root,
        run_dir=run_dir_sched,
        approval=approval,
    )

    assert res_interactive.terminal_outcome == "COMPLETE"
    assert res_interactive.mutations_count >= 1
    assert res_interactive.is_read_only is False
    assert res_interactive.metadata.get("re_extracted") is False
    assert res_interactive.metadata.get("worktree_retained") is False

    # Verify physical mutation proof
    proof = res_interactive.metadata.get("physical_proof", {})
    assert proof.get("file_changed") is True
    assert proof.get("raw_bytes_verified") is True
    assert proof.get("integrity_ok") is True
    assert proof.get("readback_assertion_count") > 1


# --- 6. Before / After Signal-Quality Comparison --------------------------------

def test_before_after_signal_quality_comparison(mock_sandbox_env: dict[str, Any]) -> None:
    """Verifies that 5-dimension scoring filters noisy ungrounded summaries (before)

    while retaining quantified high-signal filings (after).
    """
    raw_mixed_candidates = [
        # Noise / low-signal (rejected)
        CandidateSignal(
            title="General Renewable Energy Blog Post",
            source_agency="Industry Blog",
            date="10/12/2026",
            url="https://blog.example.com",
            content="Clean energy is very important and growing rapidly across the nation.",
            authority_score=0.40,
            relevance_score=0.50,
            materiality_score=0.30,
            novelty_score=0.40,
            actionability_score=0.30,
            epistemic_modality="ANALYST_HYPOTHESIS",
            horizon="90-day",
            quantified_metrics=[],  # missing
            docket_ids=[],
            decision_implication="",  # missing
        ),
        # High-signal (accepted)
        CandidateSignal(
            title="FPSC Docket 20260001 APEX Clean Energy Filing",
            source_agency="FPSC",
            date="10/14/2026",
            url="https://floridapsc.com/20260001",
            content="APEX filed 2026 solar deployment schedule totaling 1,200 MW across 16 sites.",
            authority_score=0.98,
            relevance_score=0.98,
            materiality_score=0.95,
            novelty_score=0.90,
            actionability_score=0.95,
            epistemic_modality="OBSERVED_FACT",
            horizon="7-day",
            quantified_metrics=["1,200 MW", "16 sites"],
            docket_ids=["Docket 20260001"],
            decision_implication="Direct capital expenditure and rate base growth.",
        ),
    ]

    passed, audit_log = evaluate_candidate_signals(raw_mixed_candidates, graph_db_path=mock_sandbox_env["graph_db"])

    assert len(passed) == 1
    assert passed[0].title.startswith("FPSC Docket 20260001")
    assert passed[0].composite_score >= 0.90

    # Rejected candidate audit
    rejected = [item for item in audit_log if item["disposition"] == "REJECTED"]
    assert len(rejected) == 1
    assert rejected[0]["title"] == "General Renewable Energy Blog Post"
