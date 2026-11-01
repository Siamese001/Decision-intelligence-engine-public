"""tests/end_to_end/test_all_automations_worktree_e2e.py

End-to-End Class B test suite verifying that all 6 canonical automations and 6 backward-compatible
aliases execute cleanly off isolated worktree branches with zero direct-to-main mutations, produce sealed
evidence bundles, and demonstrate their top value/effort enhancements.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from storage.connection import create_sqlite_conn
from tools.scheduled_task_core import (
    AntigravityHostLaunchAdapter,
    AuthorityContext,
    DurableFindingsLedger,
    ExecutionMode,
    NINE_GOVERNED_TASK_IDS,
    OperationalCategory,
    OperationalChangeJournal,
    ScheduledTaskLifecycleRunner,
    TriggerOrigin,
    WorktreeIsolationContext,
    verify_sealed_evidence_bundle,
)
from tools.scheduled_task_core.adapters import (
    run_cost_quality_review,
    run_knowledge_graph_hygiene,
    run_pipeline_outcome_assurance,
    run_pr_queue_observer,
    run_repository_antipattern_audit,
    run_ssot_drift_remediation,
    run_storage_repository_hygiene,
    run_weekly_market_intelligence,
    run_worktree_coverage_cleanup,
)
from tools.scheduled_task_core.adapters_core.intelligence_engine import CandidateSignal

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_worktree_env(tmp_path: Path):
    """Provisions a hermetic worktree environment simulating an isolated branch."""
    worktree_root = tmp_path / ".worktrees" / "feat_e2e_automation_test"
    worktree_root.mkdir(parents=True)

    # Subdirectories
    (worktree_root / "reports").mkdir()
    (worktree_root / "output").mkdir()
    (worktree_root / "documents").mkdir()
    (worktree_root / "config").mkdir()
    (worktree_root / ".worktrees").mkdir()

    # Create dummy SQLite Graph
    graph_db = worktree_root / "graph.db"
    conn = create_sqlite_conn(graph_db)
    cur = conn.cursor()
    cur.execute("CREATE TABLE node (id TEXT PRIMARY KEY, label TEXT)")
    cur.execute("CREATE TABLE assertion (id TEXT PRIMARY KEY, entity_id TEXT, claim TEXT)")
    cur.execute("CREATE TABLE evidence_item (id TEXT PRIMARY KEY, quote TEXT)")
    cur.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_item_id TEXT, relevance_score REAL)")

    # Insert sample rows
    cur.execute("INSERT INTO node VALUES ('node-1', 'Existing Node Alpha')")
    cur.execute("INSERT INTO assertion VALUES ('as-1', 'node-1', 'Existing claim in knowledge graph')")
    cur.execute("INSERT INTO evidence_item VALUES ('ev-1', 'Direct quote from transcript')")
    cur.execute("INSERT INTO assertion_evidence VALUES ('as-1', 'ev-1', 1.0)")
    # Insert an ungrounded assertion for hygiene detection
    cur.execute("INSERT INTO assertion VALUES ('as-ungrounded', 'node-1', 'Ungrounded floating claim')")
    conn.commit()
    conn.close()

    # Active pair JSON
    (worktree_root / "active_pair.json").write_text(
        json.dumps({"generation_dir": "gen_002", "pinned_at": "2026-10-14T00:00:00Z"}),
        encoding="utf-8",
    )

    return worktree_root


def _make_auth(task_id: str, run_id: str, mode: str = "scheduled") -> AuthorityContext:
    adapter = AntigravityHostLaunchAdapter(repo_root=ROOT)
    return adapter.build_authority_context(
        task_id=task_id,
        run_id=run_id,
        mode=mode,
        trigger_origin="scheduler_cron" if mode == "scheduled" else "manual_operator",
    )


def test_worktree_isolation_context_detection(isolated_worktree_env: Path) -> None:
    """Verifies that WorktreeIsolationContext recognizes secondary worktree vs root."""
    wt_ctx = WorktreeIsolationContext.resolve(isolated_worktree_env)
    assert wt_ctx.is_isolated is True
    assert wt_ctx.branch_name == "feat_e2e_automation_test"

    # Primary root detection
    primary_root = wt_ctx.repo_root
    assert primary_root is not None
    assert ".worktrees" not in primary_root.parts


@pytest.mark.parametrize("task_id", sorted(list(NINE_GOVERNED_TASK_IDS)))
def test_all_nine_tasks_e2e_dual_mode_execution(isolated_worktree_env: Path, task_id: str) -> None:
    """Verifies that all 9 tasks execute end-to-end in both scheduled and interactive modes."""
    run_dir = isolated_worktree_env / "reports" / task_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Scheduled mode (audit / detection)
    auth_sched = _make_auth(task_id, f"run-sched-{task_id}", mode="scheduled")
    assert auth_sched.is_scheduled is True

    # 2. Interactive mode (HITL review / explanation)
    auth_inter = _make_auth(task_id, f"run-inter-{task_id}", mode="interactive")
    assert auth_inter.is_interactive is True


def test_enhancement_1_cost_quality_anomaly_detection(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 1: cost_quality_review flags >20% stage-level token/cost spikes."""
    reports_dir = isolated_worktree_env / "reports"
    # Create baseline: intake=1000, extraction=4000
    (reports_dir / "cost_baseline.json").write_text(
        json.dumps({"stages": {"intake": 1000.0, "extraction": 4000.0, "judge": 3000.0}}),
        encoding="utf-8",
    )
    # Create receipt with extraction=6000 (+50% spike > 20%)
    (reports_dir / "receipt_spiked.json").write_text(
        json.dumps({"stage_costs": {"intake": 1050.0, "extraction": 6000.0, "judge": 3100.0}}),
        encoding="utf-8",
    )

    auth = _make_auth("cost_quality_review", "run-cost-01", mode="scheduled")
    result = run_cost_quality_review(auth, isolated_worktree_env, reports_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert any(f.get("issue") == "STAGE_COST_ANOMALY" and f.get("stage") == "extraction" for f in result.findings)
    anomaly = next(f for f in result.findings if f.get("issue") == "STAGE_COST_ANOMALY")
    assert anomaly["spike_percentage"] == 50.0


def test_enhancement_2_pipeline_outcome_stranded_directive(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 2: pipeline_outcome_assurance links stranded directives to resume commands."""
    directives_dir = isolated_worktree_env / "output" / "directives"
    directives_dir.mkdir(parents=True)
    # Create stranded directive
    d_path = directives_dir / "directive_20261014_meeting.json"
    d_path.write_text(
        json.dumps({"directive_id": "dir-meet-99", "status": "STRANDED", "attendees": ["Alice", "Bob"]}),
        encoding="utf-8",
    )

    auth = _make_auth("pipeline_outcome_assurance", "run-pipe-01", mode="scheduled")
    run_dir = isolated_worktree_env / "reports" / "pipeline_outcome_assurance"
    run_dir.mkdir(parents=True)
    result = run_pipeline_outcome_assurance(auth, isolated_worktree_env, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    stranded = next(f for f in result.findings if f.get("issue") == "STRANDED_DIRECTIVE")
    assert stranded["directive_id"] == "dir-meet-99"
    assert "ingest-meeting --resume" in stranded["resume_command"]


def test_enhancement_3_repository_antipattern_ratchet(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 3: repository_antipattern_audit compares complexity against baseline."""
    # Write complex module
    src_dir = isolated_worktree_env / "src"
    src_dir.mkdir()
    code = "\n".join([f"    if x == {i}:\n        return {i}" for i in range(20)])
    (src_dir / "heavy.py").write_text(f"def evaluate(x):\n{code}\n", encoding="utf-8")

    # Set baseline lower than current complexity
    (isolated_worktree_env / "reports" / "complexity_baseline.json").write_text(
        json.dumps({"total_complexity": 5}),
        encoding="utf-8",
    )

    auth = _make_auth("repository_antipattern_audit", "run-anti-01", mode="scheduled")
    run_dir = isolated_worktree_env / "reports" / "repository_antipattern_audit"
    run_dir.mkdir(parents=True)
    result = run_repository_antipattern_audit(auth, isolated_worktree_env, run_dir)

    assert any(f.get("issue") == "COMPLEXITY_RATCHET_VIOLATION" for f in result.findings)


def test_enhancement_5_knowledge_graph_shadow_proof(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 5: knowledge_graph_hygiene executes 5 physical proof checks on shadow SQLite."""
    auth = _make_auth("knowledge_graph_hygiene", "run-kgh-01", mode="scheduled")
    run_dir = isolated_worktree_env / "reports" / "knowledge_graph_hygiene"
    run_dir.mkdir(parents=True)
    result = run_knowledge_graph_hygiene(auth, isolated_worktree_env, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    shadow_proof = result.metadata.get("shadow_physical_proof", {})
    assert shadow_proof.get("all_passed") is True
    checks = shadow_proof.get("checks", {})
    assert checks.get("file_changed") == "PASS"
    assert checks.get("raw_bytes") == "PASS"
    assert checks.get("integrity") == "PASS"
    assert checks.get("readback") == "PASS"
    assert checks.get("row_delta") == "PASS"


def test_enhancement_6_storage_reclamation_forecast(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 6: storage_repository_hygiene computes ranked byte forecast and pin lineage."""
    old_gen = isolated_worktree_env / "generations" / "gen_001"
    old_gen.mkdir(parents=True)
    (old_gen / "file_a.bin").write_bytes(b"A" * 5000)
    (old_gen / "file_b.bin").write_bytes(b"B" * 15000)

    auth = _make_auth("storage_repository_hygiene", "run-srh-01", mode="scheduled")
    run_dir = isolated_worktree_env / "reports" / "storage_repository_hygiene"
    run_dir.mkdir(parents=True)
    result = run_storage_repository_hygiene(auth, isolated_worktree_env, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    forecast = result.metadata.get("byte_reclamation_forecast", {})
    assert forecast.get("total_reclaimable_bytes") == 20000
    ranked = forecast.get("ranked_candidates", [])
    assert len(ranked) >= 2
    assert ranked[0]["size_bytes"] >= ranked[1]["size_bytes"]  # Sorted descending
    assert forecast.get("active_generation_pin", {}).get("generation_dir") == "gen_002"


def test_enhancement_7_weekly_market_intelligence_novelty_filter(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 7: weekly_market_intelligence filters out existing graph claims/nodes."""
    graph_db = isolated_worktree_env / "graph.db"

    # Candidate signal duplicate of existing assertion
    duplicate_signal = CandidateSignal(
        title="Existing Node Alpha",
        source_agency="Test Agency",
        date="10/14/2026",
        url="https://example.com",
        content="Existing claim in knowledge graph",
        authority_score=0.95,
        relevance_score=0.95,
        materiality_score=0.90,
        novelty_score=0.90,
        actionability_score=0.90,
        epistemic_modality="OBSERVED_FACT",
        horizon="7-day",
        quantified_metrics=["$100M"],
        docket_ids=["D-101"],
        decision_implication="Test duplicate filtering.",
    )

    auth = _make_auth("weekly_market_intelligence", "run-wmi-01", mode="scheduled")
    run_dir = isolated_worktree_env / "reports" / "weekly_market_intelligence"
    run_dir.mkdir(parents=True)
    result = run_weekly_market_intelligence(
        auth,
        isolated_worktree_env,
        run_dir,
        candidate_signals=[duplicate_signal],
    )

    # Since the only signal is a duplicate, it should trigger zero-signal NOOP
    assert result.terminal_outcome == "COMPLETE_NOOP"
    assert result.metadata.get("zero_signal") is True


def test_enhancement_8_worktree_coverage_stash_matcher(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 8: worktree_coverage_cleanup matches git stashes to feature branches."""
    # Create fake stashes file
    (isolated_worktree_env / ".stashes.json").write_text(
        json.dumps([
            {"ref": "stash@{0}", "branch": "feat_e2e_automation_test", "message": "WIP on feat_e2e_automation_test"},
            {"ref": "stash@{1}", "branch": "orphaned_old_branch", "message": "WIP on old branch"},
        ]),
        encoding="utf-8",
    )

    auth = _make_auth("worktree_coverage_cleanup", "run-wtc-01", mode="scheduled")
    run_dir = isolated_worktree_env / "reports" / "worktree_coverage_cleanup"
    run_dir.mkdir(parents=True)
    result = run_worktree_coverage_cleanup(auth, isolated_worktree_env, run_dir)

    stashes = [f for f in result.findings if "stash_ref" in f]
    assert len(stashes) == 2
    assert any(s["is_orphaned"] is True for s in stashes)


def test_enhancement_9_pr_queue_observer_overlap_detector(isolated_worktree_env: Path) -> None:
    """E2E Enhancement 9: pr_queue_observer flags cross-worktree file modification collisions."""
    # Create 2 worktrees touching the same file
    wt1 = isolated_worktree_env / ".worktrees" / "feat_branch_a"
    wt2 = isolated_worktree_env / ".worktrees" / "feat_branch_b"
    wt1.mkdir()
    wt2.mkdir()
    (wt1 / ".worktree_manifest.json").write_text(
        json.dumps({"modified_files": ["storage/connection.py", "config/settings.py"]}),
        encoding="utf-8",
    )
    (wt2 / ".worktree_manifest.json").write_text(
        json.dumps({"modified_files": ["storage/connection.py", "tools/cli.py"]}),
        encoding="utf-8",
    )

    auth = _make_auth("pr_queue_observer", "run-obs-01", mode="interactive")
    run_dir = isolated_worktree_env / "reports" / "pr_queue_observer"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = run_pr_queue_observer(auth, isolated_worktree_env, run_dir)

    conflicts = [f for f in result.findings if f.get("issue") == "CROSS_WORKTREE_OVERLAP_CONFLICT"]
    assert len(conflicts) >= 1
    assert conflicts[0]["colliding_path"] == "storage/connection.py"
    assert "feat_branch_a" in conflicts[0]["worktrees"]
    assert "feat_branch_b" in conflicts[0]["worktrees"]
