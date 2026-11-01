"""tests/test_scheduled_task_remediation_plan_generation.py

Unit tests for scheduled task remediation plan generation, strict governance validation,
and adapter plan attachment across all 10 governed tasks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

import pytest

from storage.connection import create_sqlite_conn
from tools.scheduled_task_core.adapters import (
    TaskExecutionResult,
    run_cost_quality_review,
    run_knowledge_graph_hygiene,
    run_pipeline_outcome_assurance,
    run_pr_queue_observer,
    run_repository_antipattern_audit,
    run_ssot_drift_remediation,
    run_storage_repository_hygiene,
    run_technical_debt_elimination,
    run_weekly_market_intelligence,
    run_worktree_coverage_cleanup,
)
from tools.scheduled_task_core.adapters_core.audit_enhancements import compute_empirical_cost_telemetry
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import TEN_GOVERNED_TASK_IDS, ExecutionMode, TriggerOrigin
from tools.scheduled_task_core.handoff import DurableHandoffPackage, create_durable_handoff
from tools.scheduled_task_core.remediation_planner import (
    attach_plan_if_findings,
    build_remediation_plan_content,
    generate_and_validate_remediation_plan,
)
from tools.validate_implementation_plan import validate_plan_content
from tools.validate_implementation_summary import validate_summary_content


@pytest.fixture
def temp_env(tmp_path: Path):
    fixture_root = tmp_path / "repo"
    fixture_root.mkdir(parents=True)
    run_dir = tmp_path / "run_001"
    run_dir.mkdir(parents=True)
    return fixture_root, run_dir


def test_remediation_plan_strict_validation_all_domains(tmp_path: Path):
    """Verifies that generated remediation plans for every governed task pass strict validation and summary validation."""
    sample_findings = [
        {
            "issue": "UNPRICED_USAGE_OBSERVED",
            "file": "dual_pipeline/telemetry.py",
            "description": "Receipts with missing pricing models.",
        },
        {
            "issue": "MONOLITHIC_MODULE",
            "target": "storage/graph_store.py",
            "description": "Module exceeds 400 lines.",
        },
    ]

    for task_id in TEN_GOVERNED_TASK_IDS:
        branch_slug = f"feat/{task_id}_remediation"
        content = build_remediation_plan_content(task_id, "run-test-01", sample_findings, branch_slug=branch_slug)
        is_valid, errors = validate_plan_content(content, filename="implementation_plan.md", strict=True)
        assert is_valid, f"Plan for task '{task_id}' failed strict validation: {errors}"

        # Verify summary status table compliance (Gate 27)
        summary_ok, summary_errors, wave_rows = validate_summary_content(content, filename="implementation_plan.md")
        assert summary_ok, f"Plan for task '{task_id}' failed summary status table validation: {summary_errors}"
        assert len(wave_rows) == 2

        # Check word count adheres to canonical target (500-800 words)
        word_count = len(content.split())
        assert 500 <= word_count <= 800, f"Task '{task_id}' word count {word_count} out of canonical 500-800 bounds"

        # Check backticked deliverables for PlanFidelityAuditor and branch_slug binding
        assert "`[MODIFY]` `dual_pipeline/telemetry.py`" in content
        assert "`[MODIFY]` `storage/graph_store.py`" in content
        assert "## Wave Implementation & Completion Status Table" in content
        assert f"tools/pr_governance.py review {branch_slug}" in content

        # Check mandatory gate fields
        for field in ("**Run**:", "**Input**:", "**Expected**:", "**Verify independently**:", "**Fail if**:", "**Record**:"):
            assert field in content, f"Task '{task_id}' plan missing gate field {field}"


def test_attach_plan_if_findings_behavior(temp_env):
    """Verifies unconditional generation: returns (path, sha) both when findings are empty and when present."""
    fixture_root, run_dir = temp_env

    # 1. Empty findings -> Unconditional zero-delta verification plan generated
    path, sha = attach_plan_if_findings("cost_quality_review", "run-101", [], run_dir, repo_root=fixture_root)
    assert path is not None
    assert sha is not None
    assert path.is_file()
    zero_text = path.read_text(encoding="utf-8")
    is_valid, errors = validate_plan_content(zero_text, filename="implementation_plan.md", strict=True)
    assert is_valid, f"Zero-finding plan failed strict validation: {errors}"
    assert "ZERO_DEFECT_BASELINE_VERIFIED" in zero_text

    # 2. Present findings
    findings = [{"issue": "DRIFT_DETECTED", "description": "Configuration drifted"}]
    path, sha = attach_plan_if_findings(
        "cost_quality_review", "run-101", findings, run_dir, repo_root=fixture_root, branch_slug="feat-cost-opt"
    )
    assert path is not None
    assert sha is not None
    assert path.is_file()
    assert (fixture_root / "docs" / "plans" / "feat-cost-opt.md").is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha


def test_durable_handoff_package_plan_roundtrip(tmp_path: Path):
    """Verifies implementation plan fields survive DurableHandoffPackage serialization."""
    plan_path = tmp_path / "implementation_plan.md"
    plan_path.write_text("# Test Plan", encoding="utf-8")
    plan_sha = hashlib.sha256(b"# Test Plan").hexdigest()

    ctx = AuthorityContext("cost_quality_review", "run-777", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "cron-scheduler")
    handoff = create_durable_handoff(
        "cost_quality_review",
        "run-777",
        [],
        ctx,
        implementation_plan_path=plan_path,
        implementation_plan_sha256=plan_sha,
    )
    assert handoff.implementation_plan_path == str(plan_path)
    assert handoff.implementation_plan_sha256 == plan_sha

    d = handoff.to_dict()
    assert d["implementation_plan_path"] == str(plan_path)
    assert d["implementation_plan_sha256"] == plan_sha

    reconstructed = DurableHandoffPackage.from_dict(d)
    assert reconstructed.implementation_plan_path == str(plan_path)
    assert reconstructed.implementation_plan_sha256 == plan_sha


def test_empirical_cost_telemetry_computation(tmp_path: Path):
    """Verifies compute_empirical_cost_telemetry accurately aggregates tokens and latency."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    sample_receipt = {
        "pricing_status": "PRICED",
        "telemetry": {
            "input_tokens": 1500,
            "cached_tokens": 500,
            "output_tokens": 300,
            "reasoning_tokens": 100,
            "duration_ms": 250.0,
            "stage": "L2_EXTRACTION",
            "cost_usd": 0.005,
        }
    }
    (reports_dir / "stage_receipt.json").write_text(json.dumps(sample_receipt), encoding="utf-8")

    telemetry = compute_empirical_cost_telemetry(tmp_path)
    assert telemetry["total_calls"] == 1
    assert telemetry["input_tokens"] == 1500
    assert telemetry["cached_tokens"] == 500
    assert telemetry["output_tokens"] == 300
    assert telemetry["reasoning_tokens"] == 100
    assert telemetry["p50_latency_ms"] == 250.0
    assert "L2_EXTRACTION" in telemetry["stage_aggregates"]


def test_ssot_drift_remediation_plan_attachment(temp_env):
    """Verifies SSOT drift remediation generates and attaches plan upon finding."""
    fixture_root, run_dir = temp_env
    (fixture_root / "config.py").write_text("SSOT_DRIFT_CONSTANT = True\n", encoding="utf-8")

    ctx = AuthorityContext("ssot_drift_remediation", "run-ssot-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_ssot_drift_remediation(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_repository_antipattern_audit_plan_attachment(temp_env):
    """Verifies repository antipattern audit attaches plan when monolithic files are discovered."""
    fixture_root, run_dir = temp_env
    # Create an unexempted oversized file exceeding 800-line ceiling
    oversized = fixture_root / "large_module.py"
    oversized.write_text("\n".join([f"x_{i} = {i}" for i in range(850)]), encoding="utf-8")

    ctx = AuthorityContext("repository_antipattern_audit", "run-anti-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_repository_antipattern_audit(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_cost_quality_review_plan_attachment(temp_env):
    """Verifies cost quality review attaches plan when unpriced usage is detected."""
    fixture_root, run_dir = temp_env
    reports_dir = fixture_root / "reports"
    reports_dir.mkdir()
    (reports_dir / "unpriced_receipt.json").write_text(json.dumps({"pricing_status": "UNPRICED_USAGE"}), encoding="utf-8")

    ctx = AuthorityContext("cost_quality_review", "run-cost-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_cost_quality_review(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_knowledge_graph_hygiene_plan_attachment(temp_env):
    """Verifies knowledge graph hygiene attaches plan when ungrounded assertions exist."""
    fixture_root, run_dir = temp_env
    graph_db = fixture_root / "graph.db"
    conn = create_sqlite_conn(graph_db)
    cur = conn.cursor()
    cur.execute("CREATE TABLE assertion (id TEXT PRIMARY KEY, entity_id TEXT, claim TEXT)")
    cur.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_item_id TEXT, relevance_score REAL)")
    cur.execute("CREATE TABLE node (id TEXT PRIMARY KEY, label TEXT)")
    cur.execute("INSERT INTO assertion VALUES ('as-1', 'node-1', 'claim 1')")
    cur.execute("INSERT INTO node VALUES ('node-1', 'label 1')")
    cur.execute("INSERT INTO node VALUES ('node-orphan', 'orphan')")
    conn.commit()
    conn.close()

    ctx = AuthorityContext("knowledge_graph_hygiene", "run-kgh-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_knowledge_graph_hygiene(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_storage_repository_hygiene_plan_attachment(temp_env):
    """Verifies storage repository hygiene attaches plan when candidate files are discovered."""
    fixture_root, run_dir = temp_env
    candidate = fixture_root / "generations" / "gen_001" / "stale.bin"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"stale data")

    ctx = AuthorityContext("storage_repository_hygiene", "run-srh-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_storage_repository_hygiene(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_worktree_coverage_cleanup_plan_attachment(temp_env):
    """Verifies worktree coverage cleanup attaches plan when worktree stubs are discovered."""
    fixture_root, run_dir = temp_env
    wt = fixture_root / ".worktrees" / "stub-wt"
    wt.mkdir(parents=True)

    ctx = AuthorityContext("worktree_coverage_cleanup", "run-wt-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_worktree_coverage_cleanup(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_technical_debt_elimination_plan_attachment(temp_env):
    """Verifies technical debt elimination adapter attaches plan when debt is discovered."""
    fixture_root, run_dir = temp_env
    # Create an unexempted oversized file exceeding 800-line ceiling
    large = fixture_root / "heavy_module.py"
    large.write_text("\n".join([f"v_{i} = {i}" for i in range(850)]), encoding="utf-8")

    ctx = AuthorityContext("technical_debt_elimination", "run-tde-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_technical_debt_elimination(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_pipeline_outcome_assurance_plan_attachment(temp_env):
    """Verifies pipeline outcome assurance attaches plan when stranded directives exist."""
    fixture_root, run_dir = temp_env
    directives_dir = fixture_root / "directives"
    directives_dir.mkdir(parents=True)
    (directives_dir / "directive_01.json").write_text(
        json.dumps({"directive_id": "dir-test-01", "status": "FAILED"}), encoding="utf-8"
    )

    ctx = AuthorityContext("pipeline_outcome_assurance", "run-poa-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_pipeline_outcome_assurance(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_pr_queue_observer_plan_attachment(temp_env):
    """Verifies PR queue observer attaches plan when cross-worktree overlaps occur."""
    fixture_root, run_dir = temp_env
    wt1 = fixture_root / ".worktrees" / "feat_branch_a"
    wt2 = fixture_root / ".worktrees" / "feat_branch_b"
    wt1.mkdir(parents=True)
    wt2.mkdir(parents=True)
    (wt1 / "shared_module.py").write_text("# branch a\n", encoding="utf-8")
    (wt2 / "shared_module.py").write_text("# branch b\n", encoding="utf-8")

    ctx = AuthorityContext("pr_queue_observer", "run-prq-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_pr_queue_observer(ctx, fixture_root, run_dir)

    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()


def test_weekly_market_intelligence_plan_attachment(temp_env):
    """Verifies weekly market intelligence attaches plan when passed signals are present."""
    fixture_root, run_dir = temp_env

    ctx = AuthorityContext("weekly_market_intelligence", "run-wmi-01", TriggerOrigin.SCHEDULER_CRON, ExecutionMode.SCHEDULED, "scheduler")
    result = run_weekly_market_intelligence(ctx, fixture_root, run_dir, candidate_signals=None)

    assert result.terminal_outcome == "AWAITING_APPROVAL"
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()

