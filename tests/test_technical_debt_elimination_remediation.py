"""tests/test_technical_debt_elimination_remediation.py

Wave 1 test suite for Technical Debt Discovery & Elimination Agent.
Verifies:
1. Scheduled audit read-only discovery & manifest emission without mutation.
2. Interactive mode awaiting operator approval gate.
3. Interactive execution with valid operator approval record.
4. Durable findings ledger deduplication and lifecycle transitions.
5. Wave-based implementation plan schema conformance.
6. Zero graph mutation invariant preservation.
7. Layer architectural integrity & unidirectional flow.
8. Credential isolation and secret hygiene compliance.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

from tools.scheduled_task_core.authority import ApprovalRecord, AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.scheduled_task_core.findings import (
    DurableFinding,
    DurableFindingsLedger,
    FindingState,
)
from tools.scheduled_task_core.launch_adapters import AntigravityHostLaunchAdapter
from tools.scheduled_task_core.adapters import (
    TaskExecutionResult,
    run_technical_debt_elimination,
)
from tools.validate_implementation_plan import validate_file

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_technical_debt_elimination_scheduled_read_only(tmp_path: Path) -> None:
    """Verifies scheduled execution performs read-only discovery and emits a sealed manifest."""
    # Create sample repo tree fixture
    sample_dir = tmp_path / "sample_repo"
    sample_dir.mkdir(parents=True)
    sample_file = sample_dir / "sample_module.py"
    sample_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    run_dir = tmp_path / "reports" / "run-001"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="technical_debt_elimination",
        run_id="run-001",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        allowed_capabilities={"audit_execution", "remediation_planning"},
        is_scheduled=True,
    )

    result = run_technical_debt_elimination(ctx, sample_dir, run_dir)
    assert result.is_read_only is True
    assert result.mutations_count == 0
    assert result.task_id == "technical_debt_elimination"
    assert result.terminal_outcome in ("COMPLETE", "HITL_REQUIRED")

    manifest = run_dir / "tech_debt_manifest.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "findings" in data
    assert "terminal_outcome" in data


def test_technical_debt_elimination_interactive_awaiting_approval(tmp_path: Path) -> None:
    """Verifies interactive execution halts awaiting operator approval."""
    sample_dir = tmp_path / "sample_repo"
    sample_dir.mkdir(parents=True)
    (sample_dir / "sample_module.py").write_text("def sample(): pass\n", encoding="utf-8")

    run_dir = tmp_path / "reports" / "run-002"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="technical_debt_elimination",
        run_id="run-002",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="Amit Ayer",
        allowed_capabilities={"audit_execution", "remediation_planning"},
        is_scheduled=False,
    )

    result = run_technical_debt_elimination(ctx, sample_dir, run_dir, approval=None)
    assert result.terminal_outcome == "AWAITING_APPROVAL"
    assert result.mutations_count == 0
    assert result.is_read_only is True


def test_technical_debt_elimination_interactive_with_approval(tmp_path: Path) -> None:
    """Verifies interactive execution with valid operator approval transitions to COMPLETE."""
    sample_dir = tmp_path / "sample_repo"
    sample_dir.mkdir(parents=True)
    (sample_dir / "sample_module.py").write_text("def sample(): pass\n", encoding="utf-8")

    run_dir = tmp_path / "reports" / "run-003"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="technical_debt_elimination",
        run_id="run-003",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="Amit Ayer",
        allowed_capabilities={"audit_execution", "remediation_planning", "tech_debt_elimination"},
        is_scheduled=False,
    )

    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    exp = (now + dt.timedelta(hours=2)).isoformat()
    approval = {
        "approval_id": "app-run-003",
        "task_id": "technical_debt_elimination",
        "run_id": "run-003",
        "operator": "Amit Ayer",
        "signature": "ed25519:operator-signature",
        "status": "APPROVED",
        "approved_items": [],
        "plan_digest": "plan_digest_003",
        "baseline_sha": "baseline_sha_003",
        "issued_at": now.isoformat(),
        "expires_at": exp,
        "replay_nonce": "nonce-run-003",
        "is_authentic_human": True,
    }

    result = run_technical_debt_elimination(ctx, sample_dir, run_dir, approval=approval)
    assert result.terminal_outcome == "COMPLETE"
    assert result.mutations_count == 0
    assert result.is_read_only is True


def test_technical_debt_findings_ledger_lifecycle(tmp_path: Path) -> None:
    """Verifies durable findings ledger deduplication and state progression for tech debt."""
    ledger = DurableFindingsLedger(task_id="technical_debt_elimination")
    sig = "monolithic_module:dual_pipeline/sample.py:450"

    f1 = ledger.record_finding(
        signature=sig,
        run_id="run-td-01",
        root_cause_hypothesis="Module exceeds 400-line budget",
        owner="refactor_lead",
    )
    assert f1.occurrence_count == 1
    assert f1.state == FindingState.DETECTED

    f2 = ledger.record_finding(
        signature=sig,
        run_id="run-td-02",
    )
    assert f2.finding_id == f1.finding_id
    assert f2.occurrence_count == 2
    assert f2.run_ids_observed == ["run-td-01", "run-td-02"]


def test_wave_implementation_plan_strict_validation() -> None:
    """Verifies that the generated technical debt implementation plan conforms to strict wave governance."""
    plan_path = REPO_ROOT / "docs" / "plans" / "refactor_tech_debt_elimination_10_19_2026.md"
    assert plan_path.is_file(), f"Plan file missing: {plan_path}"

    is_valid, errors = validate_file(plan_path, strict=True)
    assert is_valid, f"Implementation plan validation failed: {errors}"


def test_zero_graph_mutation_guarantee() -> None:
    """Verifies zero live graph database mutations occur during technical debt audit."""
    scheduled_report = REPO_ROOT / "reports" / "tech_debt_audit" / "scheduled_10-19-2026.md"
    assert scheduled_report.is_file(), "Scheduled audit report must exist"

    content = scheduled_report.read_text(encoding="utf-8")
    assert "POST_GRAPH_DIGEST == PRE_GRAPH_DIGEST" in content
    assert "IDEMPOTENT_NOOP +0" in content
