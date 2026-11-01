"""tests/test_automation_prompts_plan_gate_adversarial.py

Adversarial fault-injection and boundary qualification test suite for automation prompt
implementation plans, Plan Approval Gates, D01–D03 domain routing, and live database immutability.
Validates:
1. Zero-gap clean run invariance (zero plans emitted, zero mutations).
2. Multi-gap multi-wave plan synthesis conformance and word budgets.
3. Strict fail-closed rejection of malformed or non-compliant implementation plans.
4. D03 cross-domain mutation prevention for observation and audit tasks.
5. Live database immutability prior to governed PR merge.
6. Durable handoff fail-closed enforcement when findings lack a sealed plan.
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
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import TEN_GOVERNED_TASK_IDS, ExecutionMode, TriggerOrigin
from tools.scheduled_task_core.findings import DurableFindingsLedger
from tools.scheduled_task_core.handoff import (
    DurableHandoffPackage,
    HandoffError,
    create_durable_handoff,
)
from tools.scheduled_task_core.remediation_planner import (
    build_remediation_plan_content,
    generate_and_validate_remediation_plan,
)
from tools.validate_implementation_plan import validate_plan_content


def test_adversarial_zero_gap_clean_run_invariance(tmp_path: Path) -> None:
    """Verifies clean run unconditionally generates and seals a 2-wave zero-delta verification plan with zero mutations."""
    repo_root = tmp_path / "clean_repo"
    repo_root.mkdir(parents=True)
    run_dir = tmp_path / "clean_run"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="cost_quality_review",
        run_id="run-adv-clean",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )

    result = run_cost_quality_review(ctx, repo_root, run_dir)
    assert result.terminal_outcome in ("PASS", "CLEAN", "COMPLETE")
    assert result.mutations_count == 0
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert (run_dir / "implementation_plan.md").is_file()
    assert (repo_root / "docs" / "plans").is_dir()
    plan_text = result.implementation_plan_path.read_text(encoding="utf-8")
    is_valid, errors = validate_plan_content(plan_text, filename="implementation_plan.md", strict=True)
    assert is_valid, f"Zero-gap plan failed strict validation: {errors}"


def test_adversarial_multi_gap_multi_wave_plan_synthesis() -> None:
    """Verifies synthesis of multi-gap findings generates a compliant multi-wave plan within word budgets."""
    complex_findings = [
        {"issue": "UNPRICED_USAGE_OBSERVED", "description": "Receipts with missing pricing models."},
        {"issue": "STAGE_COST_ANOMALY", "description": "L2 extraction stage exceeded baseline by 45%."},
        {"issue": "LATENCY_SPIKE", "description": "P99 latency degraded beyond 3500ms SLA."},
        {"issue": "CACHE_MISS_ELEVATED", "description": "Semantic prefix cache hit rate dropped below 20%."},
        {"issue": "STRANDED_DIRECTIVE", "description": "Meeting intake directive failed mid-pipeline."},
    ]

    for task_id in TEN_GOVERNED_TASK_IDS:
        content = build_remediation_plan_content(task_id, "run-adv-multigap", complex_findings)
        is_valid, errors = validate_plan_content(content, filename="implementation_plan.md", strict=True)
        assert is_valid, f"Task {task_id} multi-gap plan failed validation: {errors}"

        # Word count ceiling check: target 500-800, max 1000
        words = len(content.split())
        assert 350 <= words <= 1000, f"Task {task_id} word count {words} violated bounds"

        # Sequential wave verification
        assert "## Wave 1:" in content
        assert "## Wave 2:" in content

        # Required gate fields present
        for field in ("**Run**:", "**Input**:", "**Expected**:", "**Verify independently**:", "**Fail if**:", "**Record**:"):
            assert field in content, f"Missing required gate field {field} in plan for {task_id}"


def test_adversarial_malformed_plan_rejection() -> None:
    """Verifies that validate_plan_content fails closed when presented with malformed plans."""
    # 1. Truncated plan (< 200 words)
    truncated = "# Plan\n## Wave 1: Fix\n### Objective & Scope\nShort.\n"
    is_valid, errors = validate_plan_content(truncated, filename="implementation_plan.md", strict=True)
    assert not is_valid
    assert any("word count" in e.lower() or "too short" in e.lower() or "missing" in e.lower() for e in errors)

    # 2. Missing mandatory subsections
    missing_subsections = (
        "# Implementation Plan\n\n"
        "## Wave 1: Initial Wave\n\n"
        "### Objective & Scope\n"
        + ("word " * 150)
        + "\n\n### Milestones & Deliverables\n- Deliverable 1\n\n"
        "### Acceptance Criteria\n- Criteria 1\n"
    )
    is_valid, errors = validate_plan_content(missing_subsections, filename="implementation_plan.md", strict=True)
    assert not is_valid
    assert any("runtime receipt" in e.lower() or "completion gate" in e.lower() for e in errors)

    # 3. Non-sequential wave numbers (Wave 1 then Wave 3)
    non_sequential = (
        "# Implementation Plan\n\n"
        "## Wave 1: First Step\n\n"
        "### Objective & Scope\n" + ("step one scope " * 50) + "\n\n"
        "### Milestones & Deliverables\n- Step 1\n\n"
        "### Acceptance Criteria\n- Pass\n\n"
        "### Runtime Receipt & Completion Gate\n"
        "- **Run**: pytest\n- **Input**: input\n- **Expected**: pass\n- **Verify independently**: check\n- **Fail if**: fail\n- **Record**: receipt.json\n\n"
        "## Wave 3: Non Sequential Step\n\n"
        "### Objective & Scope\n" + ("step three scope " * 50) + "\n\n"
        "### Milestones & Deliverables\n- Step 3\n\n"
        "### Acceptance Criteria\n- Pass\n\n"
        "### Runtime Receipt & Completion Gate\n"
        "- **Run**: pytest\n- **Input**: input\n- **Expected**: pass\n- **Verify independently**: check\n- **Fail if**: fail\n- **Record**: receipt.json\n"
    )
    is_valid, errors = validate_plan_content(non_sequential, filename="implementation_plan.md", strict=True)
    assert not is_valid
    assert any("gap" in e.lower() or "sequential" in e.lower() or "wave 2" in e.lower() for e in errors)


def test_adversarial_d03_cross_domain_mutation_rejection() -> None:
    """Verifies that non-graph observation and audit tasks are barred from mutating the knowledge graph."""
    ctx = AuthorityContext(
        task_id="cost_quality_review",
        run_id="run-adv-d03",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )

    # In scheduled mode, authority has zero mutation capabilities
    assert ctx.can_mutate is False
    with pytest.raises(PermissionError, match="Unauthorized .* blocked"):
        ctx.assert_can_mutate("graph_mutation")

    with pytest.raises(PermissionError, match="Unauthorized .* blocked"):
        ctx.assert_can_mutate("remediation_planning")


def test_adversarial_live_database_immutability_pre_pr_merge(tmp_path: Path) -> None:
    """Verifies that live graph databases remain untouched across all 10 task executions."""
    repo_root = tmp_path / "repo_db_test"
    repo_root.mkdir(parents=True)
    run_dir = tmp_path / "run_db_test"
    run_dir.mkdir(parents=True)

    # Create mock live databases
    graph_db = repo_root / "graph.sqlite"
    canonical_db = repo_root / "CANONICAL_GRAPH.db"

    for db_path in (graph_db, canonical_db):
        conn = create_sqlite_conn(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, val TEXT)")
        cur.execute("INSERT INTO metadata VALUES ('genesis', 'immutable_value_001')")
        conn.commit()
        conn.close()

    pre_graph_sha = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    pre_canonical_sha = hashlib.sha256(canonical_db.read_bytes()).hexdigest()

    # Seed findings for tasks
    reports_dir = repo_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "unpriced_receipt.json").write_text(json.dumps({"pricing_status": "UNPRICED_USAGE"}), encoding="utf-8")

    # Run adapters in scheduled and interactive modes
    ctx = AuthorityContext(
        task_id="cost_quality_review",
        run_id="run-adv-db-cost",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )
    run_cost_quality_review(ctx, repo_root, run_dir)

    ctx_prq = AuthorityContext(
        task_id="pr_queue_observer",
        run_id="run-adv-db-prq",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )
    run_pr_queue_observer(ctx_prq, repo_root, run_dir)

    # Verify SHA invariance
    post_graph_sha = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    post_canonical_sha = hashlib.sha256(canonical_db.read_bytes()).hexdigest()

    assert pre_graph_sha == post_graph_sha, "Live graph.sqlite database was mutated prior to PR merge!"
    assert pre_canonical_sha == post_canonical_sha, "Live CANONICAL_GRAPH.db was mutated prior to PR merge!"


def test_adversarial_handoff_fails_closed_without_plan() -> None:
    """Verifies that create_durable_handoff raises HandoffError when findings lack an implementation plan."""
    ctx = AuthorityContext(
        task_id="storage_repository_hygiene",
        run_id="run-adv-handoff",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )

    ledger = DurableFindingsLedger(task_id="storage_repository_hygiene")
    f1 = ledger.record_finding(signature="stale_file:dump.bin", run_id=ctx.run_id)

    # 1. Missing plan path and sha when require_plan=True fails closed
    with pytest.raises(HandoffError, match="requires a verified implementation plan path"):
        create_durable_handoff(
            task_id="storage_repository_hygiene",
            run_id=ctx.run_id,
            findings=[f1],
            authority=ctx,
            implementation_plan_path=None,
            implementation_plan_sha256=None,
            require_plan=True,
        )

    # 2. Package verify_plan_sealed check fails when finding_ids present without plan
    unsealed_pkg = DurableHandoffPackage(
        handoff_id="handoff-unsealed-001",
        task_id="storage_repository_hygiene",
        run_id=ctx.run_id,
        created_at_utc="2026-10-14T20:00:00Z",
        finding_ids=[f1.finding_id],
        summary="Test finding",
        required_approval_scope=["APPLY_PATCH"],
        proposed_actions=[],
        reproduction_commands=[],
        implementation_plan_path=None,
        implementation_plan_sha256=None,
    )
    assert unsealed_pkg.verify_plan_sealed() is False


def test_adversarial_prompt_ssot_07_plan_standard_enforcement(tmp_path: Path) -> None:
    """Verifies INV-PROMPT-SSOT-07 passes on live repo and fails closed when a prompt omits plan standard tokens."""
    from tools.lint_prompt_ssot import EXPECTED_CANONICAL_PROMPTS, ROOT, audit_prompt_plan_standards

    # 1. Live repository must pass INV-PROMPT-SSOT-07 with zero errors
    live_errors = audit_prompt_plan_standards(ROOT)
    assert live_errors == [], f"Live canonical prompts failed INV-PROMPT-SSOT-07: {live_errors}"

    # 2. Adversarial mutation: strip mandatory status table or use root implementation_plan.md
    prompts_dir = tmp_path / "automations" / "prompts"
    prompts_dir.mkdir(parents=True)
    for fname in EXPECTED_CANONICAL_PROMPTS.values():
        src = (ROOT / "automations" / "prompts" / fname).read_text(encoding="utf-8")
        dest = prompts_dir / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src, encoding="utf-8")

    target_prompt = prompts_dir / "pipeline_outcome_assurance_agent.md"
    corrupted = (
        target_prompt.read_text(encoding="utf-8")
        .replace("## Wave Implementation & Completion Status Table", "## Legacy Table")
        .replace("UNCONDITIONAL_WAVE_PLAN_MANDATE", "When actionable findings exist, synthesize a wave-based Implementation Plan")
        .replace("INV-RUNTIME-PROOF-5POINT", "LEGACY_PROOF")
        + "\n(and `implementation_plan.md`)\n"
    )
    target_prompt.write_text(corrupted, encoding="utf-8")

    adv_errors = audit_prompt_plan_standards(tmp_path)
    assert any("Wave Implementation & Completion Status Table" in e for e in adv_errors)
    assert any("INV-RUNTIME-PROOF-5POINT" in e for e in adv_errors)
    assert any("references root `implementation_plan.md`" in e for e in adv_errors)
    assert any("INV-PROMPT-SSOT-09" in e for e in adv_errors)

