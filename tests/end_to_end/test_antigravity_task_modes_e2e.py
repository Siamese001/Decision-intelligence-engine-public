"""tests/end_to_end/test_antigravity_task_modes_e2e.py

End-to-end qualification suite proving both scheduled and interactive launch paths
across all seven Antigravity tasks under Wave 3.
Verifies all 14 combinations, approval, decline, missing approval, changed scope drift,
same-operation resume, read-only observer invariants, and empty-callback defenses.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tools.scheduled_task_core import (
    AntigravityHostLaunchAdapter,
    ApprovalRecord,
    EmptyCallbackDefenseError,
    ExecutionMode,
    ItemSetDriftError,
    PlanDriftError,
    SEVEN_ARCHIVE_TASK_IDS,
    TriggerOrigin,
)
from tools.scheduled_task_core.authority import verify_approval_record
from tools.scheduled_task_core.lifecycle import (
    EmptyAuditCallbackError,
    ScheduledTaskLifecycleRunner,
)


@pytest.fixture
def disposable_task_env(tmp_path: Path) -> dict[str, Any]:
    """Provisions isolated, disposable domain fixtures for all seven tasks."""
    fixture_root = tmp_path / "fixture_env"
    fixture_root.mkdir(parents=True, exist_ok=True)

    # 1. Knowledge graph SQLite database with 14 tables
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
        INSERT INTO node VALUES ('node-01', 'Apex Global Enterprises');
        INSERT INTO node VALUES ('node-02', 'APEX Grid');
        INSERT INTO assertion VALUES ('as-01', 'node-01', 'NEE leads renewable deployment');
        INSERT INTO evidence_item VALUES ('ev-auto-01', 'Verified SEC 10-K report');
    """)
    conn.commit()
    conn.close()

    # 2. Storage generations and audio cache
    gens_dir = fixture_root / "generations"
    (gens_dir / "gen_001").mkdir(parents=True, exist_ok=True)
    (gens_dir / "gen_001" / "stale_graph.db").write_text("old graph data", encoding="utf-8")
    (gens_dir / "gen_002").mkdir(parents=True, exist_ok=True)
    (gens_dir / "gen_002" / "active_graph.db").write_text("active graph data", encoding="utf-8")

    active_pair = fixture_root / "active_pair.json"
    active_pair.write_text(json.dumps({
        "generation_dir": "gen_002",
        "active_graph_sha256": hashlib.sha256(b"active graph data").hexdigest(),
    }), encoding="utf-8")

    audio_cache = fixture_root / "output" / "transcript_cache"
    audio_cache.mkdir(parents=True, exist_ok=True)
    (audio_cache / "meeting_01.raw").write_bytes(b"\x00\x01\x02\x03")

    # 3. Worktrees: one with protected deliverables, one empty stub
    wt_dir = fixture_root / ".worktrees"
    wt_protected = wt_dir / "feat_meeting_deliverable"
    wt_protected.mkdir(parents=True, exist_ok=True)
    (wt_protected / "01_executive_summary.md").write_text("# Meeting Deliverable", encoding="utf-8")
    (wt_protected / "output").mkdir(parents=True, exist_ok=True)
    (wt_protected / "output" / "summary.md").write_text("# Production Deliverable", encoding="utf-8")

    wt_stub = wt_dir / "chore_empty_stub"
    wt_stub.mkdir(parents=True, exist_ok=True)
    (wt_stub / "README.md").write_text("# Stub", encoding="utf-8")

    # 4. Monolithic module for antipattern audit
    anti_dir = fixture_root / "src"
    anti_dir.mkdir(parents=True, exist_ok=True)
    mono_file = anti_dir / "monolithic_service.py"
    mono_file.write_text("def run():\n    if True:\n        pass\n" + "\n".join([f"# Line {i}" for i in range(700)]), encoding="utf-8")

    # 5. SSOT drift config
    cfg_file = fixture_root / "config.py"
    cfg_file.write_text("SSOT_DRIFT_CONSTANT = True\n", encoding="utf-8")

    # 6. PR queue observer baseline snapshot
    obs_dir = fixture_root / "reports" / "pr_queue_observer"
    obs_dir.mkdir(parents=True, exist_ok=True)
    (obs_dir / "last_accepted_snapshot.json").write_text(json.dumps({"digest": "SNAPSHOT_PREV_DIGEST"}), encoding="utf-8")

    return {
        "fixture_root": fixture_root,
        "graph_db": graph_db,
        "active_pair": active_pair,
        "config_file": cfg_file,
    }


def _make_valid_approval(task_id: str, run_id: str, items: list[str], plan_digest: str = "test-plan-digest") -> ApprovalRecord:
    now = dt.datetime.now(dt.timezone.utc)
    exp = (now + dt.timedelta(hours=2)).isoformat()
    return ApprovalRecord(
        approval_id=f"app-{run_id}",
        task_id=task_id,
        run_id=run_id,
        operator="operator_human",
        signature="ed25519:valid-human-signature",
        status="APPROVED",
        approved_items=items,
        plan_digest=plan_digest,
        baseline_sha="f54762198f7183e2ec9e37859feafc8828dd5681",
        issued_at=now.isoformat(),
        expires_at=exp,
        replay_nonce=f"nonce-{task_id}-{run_id}",
        is_authentic_human=True,
    )


# -----------------------------------------------------------------------------
# Test 1: All 14 Combinations Execute Real Domain Workflows with Findings
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("task_id", sorted(list(SEVEN_ARCHIVE_TASK_IDS)))
@pytest.mark.parametrize("mode", ["scheduled", "interactive"])
def test_all_14_task_mode_combinations_e2e(
    disposable_task_env: dict[str, Any],
    task_id: str,
    mode: str,
) -> None:
    """Verifies all 14 task/mode combinations execute real domain workflows with observable findings."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = disposable_task_env["fixture_root"]
    run_id = f"test-run-{task_id}-{mode}"

    approval = None
    confirm_token = None

    if mode == "interactive":
        # Supply valid approvals or tokens for interactive qualification
        if task_id == "knowledge_graph_hygiene":
            approval = _make_valid_approval(task_id, run_id, ["as-01"])
        elif task_id == "storage_repository_hygiene":
            approval = _make_valid_approval(task_id, run_id, ["generations/gen_001/stale_graph.db", "output/transcript_cache/meeting_01.raw"])
        elif task_id == "weekly_market_intelligence":
            approval = _make_valid_approval(task_id, run_id, ["dir-wmi-01"], plan_digest=hashlib.sha256(b"# NEE Weekly Market Intelligence Briefing\n\nSignals and regulatory updates.").hexdigest())
        elif task_id == "worktree_coverage_cleanup":
            confirm_token = "CONFIRM-PRUNE-SAFE-TOKEN"
        elif task_id == "repository_antipattern_audit":
            approval = _make_valid_approval(task_id, run_id, ["wave-1-split-monolith"])
        elif task_id == "ssot_drift_remediation":
            approval = _make_valid_approval(task_id, run_id, ["fix-ssot-constant"])

    result = launcher.launch_task(
        task_id=task_id,
        mode=mode,
        fixture_root=fixture_root,
        run_id=run_id,
        approval_record=approval,
        confirm_token=confirm_token,
    )

    assert result.task_id == task_id
    assert result.mode.value == mode
    assert result.findings_count > 0, f"Task {task_id} in {mode} mode must yield observable findings"

    if mode == "scheduled":
        assert result.is_read_only is True
        assert result.mutations_count == 0
        assert result.terminal_outcome in ("HITL_REQUIRED", "AWAITING_APPROVAL", "NOTIFICATION_EMITTED", "NO_NOTIFICATION")
    else:
        assert result.terminal_outcome in ("COMPLETE", "OBSERVATION_EXPLAINED")


# -----------------------------------------------------------------------------
# Test 2: Interactive Authentic Approval Workflow
# -----------------------------------------------------------------------------

def test_interactive_authentic_approval_workflow_e2e(disposable_task_env: dict[str, Any]) -> None:
    """Verifies that an authentic human approval authorizes mutations and verifies post-integrity."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = disposable_task_env["fixture_root"]
    run_id = "test-graph-approval"

    approval = _make_valid_approval("knowledge_graph_hygiene", run_id, ["as-01"])

    result = launcher.launch_task(
        task_id="knowledge_graph_hygiene",
        mode="interactive",
        fixture_root=fixture_root,
        run_id=run_id,
        approval_record=approval,
    )

    assert result.terminal_outcome == "COMPLETE"
    assert result.mutations_count > 0
    assert result.is_read_only is False

    # Check that backup rollback copy was created
    backups = list((fixture_root / "backups").glob("hygiene_*"))
    assert len(backups) == 1
    assert (backups[0] / "graph_rollback.db").is_file()


# -----------------------------------------------------------------------------
# Test 3: Weekly Intelligence Decline Workflow
# -----------------------------------------------------------------------------

def test_weekly_market_intelligence_decline_workflow_e2e(disposable_task_env: dict[str, Any]) -> None:
    """Verifies operator decline records DECLINED / CANCELLED and is never marked COMPLETE."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = disposable_task_env["fixture_root"]
    run_id = "test-wmi-decline"

    result = launcher.launch_task(
        task_id="weekly_market_intelligence",
        mode="interactive",
        fixture_root=fixture_root,
        run_id=run_id,
        decision="DECLINE",
    )

    assert result.terminal_outcome == "DECLINED"
    assert result.terminal_outcome != "COMPLETE"
    assert result.mutations_count == 0
    assert result.report_path is not None and result.report_path.is_file()
    data = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert data["status"] == "DECLINED"


# -----------------------------------------------------------------------------
# Test 4: Missing Approval Halts Safely
# -----------------------------------------------------------------------------

def test_missing_approval_halts_safely_e2e(disposable_task_env: dict[str, Any]) -> None:
    """Verifies that interactive execution without approval halts in AWAITING_APPROVAL."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = disposable_task_env["fixture_root"]
    run_id = "test-missing-approval"

    result = launcher.launch_task(
        task_id="knowledge_graph_hygiene",
        mode="interactive",
        fixture_root=fixture_root,
        run_id=run_id,
        approval_record=None,
    )

    assert result.terminal_outcome == "AWAITING_APPROVAL"
    assert result.mutations_count == 0
    assert result.is_read_only is True


# -----------------------------------------------------------------------------
# Test 5: Scope Drift & Plan Drift Rejection
# -----------------------------------------------------------------------------

def test_scope_and_plan_drift_rejection_e2e(disposable_task_env: dict[str, Any]) -> None:
    """Verifies that approved item drift or plan digest drift fails closed with typed errors."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = disposable_task_env["fixture_root"]
    run_id = "test-drift-scope"

    # 1. ItemSetDriftError: approved scope differs from candidate scope
    mismatched_approval = _make_valid_approval(
        "storage_repository_hygiene",
        run_id,
        ["unexpected_file.txt"],  # Mismatched scope
    )

    with pytest.raises(ItemSetDriftError):
        launcher.launch_task(
            task_id="storage_repository_hygiene",
            mode="interactive",
            fixture_root=fixture_root,
            run_id=run_id,
            approval_record=mismatched_approval,
        )

    # 2. PlanDriftError: plan digest mismatch
    wmi_drift_approval = _make_valid_approval(
        "weekly_market_intelligence",
        run_id,
        ["dir-wmi-01"],
        plan_digest="tampered_different_digest_value",
    )

    with pytest.raises(PlanDriftError):
        launcher.launch_task(
            task_id="weekly_market_intelligence",
            mode="interactive",
            fixture_root=fixture_root,
            run_id=run_id,
            approval_record=wmi_drift_approval,
        )


# -----------------------------------------------------------------------------
# Test 6: Weekly Intelligence Same-Operation Resume
# -----------------------------------------------------------------------------

def test_weekly_market_intelligence_same_operation_resume_e2e(disposable_task_env: dict[str, Any]) -> None:
    """Verifies scheduled run creates pending request and interactive approval resumes same run."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = disposable_task_env["fixture_root"]
    run_id = "test-wmi-continuation"

    # Step 1: Scheduled run persists approval request
    sched_result = launcher.launch_task(
        task_id="weekly_market_intelligence",
        mode="scheduled",
        fixture_root=fixture_root,
        run_id=run_id,
    )

    assert sched_result.terminal_outcome == "AWAITING_APPROVAL"
    req_file = fixture_root / "approval_requests" / f"{run_id}.json"
    assert req_file.is_file()
    req_data = json.loads(req_file.read_text(encoding="utf-8"))
    assert req_data["directive_id"] == "dir-wmi-01"

    # Step 2: Interactive resumption with authentic approval for same directive
    briefing_digest = req_data["proposed_delta_digest"]
    approval = _make_valid_approval(
        "weekly_market_intelligence",
        run_id,
        ["dir-wmi-01"],
        plan_digest=briefing_digest,
    )

    resume_result = launcher.launch_task(
        task_id="weekly_market_intelligence",
        mode="interactive",
        fixture_root=fixture_root,
        run_id=run_id,
        approval_record=approval,
    )

    assert resume_result.terminal_outcome == "COMPLETE"
    assert resume_result.mutations_count == 1
    assert (fixture_root / "graph_meta.json").is_file()


# -----------------------------------------------------------------------------
# Test 7: PR Queue Observer Strictly Read-Only In Both Modes
# -----------------------------------------------------------------------------

def test_pr_queue_observer_strictly_read_only_in_both_modes_e2e(disposable_task_env: dict[str, Any]) -> None:
    """Verifies PR queue observer guarantees zero mutations across scheduled and interactive modes."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = disposable_task_env["fixture_root"]

    # 1. Scheduled mode
    sched_res = launcher.launch_task(
        task_id="pr_queue_observer",
        mode="scheduled",
        fixture_root=fixture_root,
        run_id="run-obs-sched",
    )
    assert sched_res.is_read_only is True
    assert sched_res.mutations_count == 0
    assert sched_res.terminal_outcome in ("NOTIFICATION_EMITTED", "NO_NOTIFICATION")

    # 2. Interactive mode
    inter_res = launcher.launch_task(
        task_id="pr_queue_observer",
        mode="interactive",
        fixture_root=fixture_root,
        run_id="run-obs-inter",
    )
    assert inter_res.is_read_only is True
    assert inter_res.mutations_count == 0
    assert inter_res.terminal_outcome == "OBSERVATION_EXPLAINED"


# -----------------------------------------------------------------------------
# Test 8: Empty Callback Defense
# -----------------------------------------------------------------------------

def test_empty_callback_defense_e2e(tmp_path: Path) -> None:
    """Verifies that empty callbacks without domain activity are rejected from qualifying execution."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=tmp_path,
        lock_dir=tmp_path / "locks",
    )
    launcher = AntigravityHostLaunchAdapter()
    auth = launcher.build_authority_context(
        task_id="knowledge_graph_hygiene",
        run_id="run-empty-check",
        mode="scheduled",
    )

    # Empty callback rejected by lifecycle runner when qualification is enforced
    with pytest.raises(EmptyAuditCallbackError):
        runner.run_task(
            task_id="knowledge_graph_hygiene",
            run_id="run-empty-check",
            authority=auth,
            output_root=tmp_path / "output",
            findings_ledger=None,  # Handled inside runner if passed
            ledger_save_path=tmp_path / "ledger.json",
            journal=None,
            audit_fn=lambda p: [],  # Empty callback
            reject_empty_callback=True,
        )
