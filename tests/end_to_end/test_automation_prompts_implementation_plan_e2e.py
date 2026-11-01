"""tests/end_to_end/test_automation_prompts_implementation_plan_e2e.py

End-to-end lifecycle verification test for the 6 canonical automation task prompts and 12 governed adapters.
Validates:
1. Full end-to-end adapter execution across all 12 tasks in SCHEDULED mode with findings.
2. Mandatory generation and disk persistence of wave-based Implementation Plans.
3. Strict plan validation via validate_plan_content(..., strict=True).
4. Durable handoff package plan sealing and halting at the Plan Approval Gate.
5. Interactive execution mode plan binding and zero-mutation enforcement.
6. Clean execution (zero findings) produces no-plan clean outcomes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from storage.connection import create_sqlite_conn
from tools.scheduled_task_core.adapters import (
    TaskExecutionResult,
    run_agentic_repo_best_practices_scout,
    run_corner_foresight_blindspot_scout,
    run_cost_quality_review,
    run_e2e_critical_functionality_assurance,
    run_evals_surface_assurance,
    run_frontier_llm_research_scout,
    run_hotspot_value_enhancement,
    run_knowledge_graph_hygiene,
    run_pipeline_outcome_assurance,
    run_pr_queue_observer,
    run_repository_antipattern_audit,
    run_retrieval_surface_assurance,
    run_ssot_drift_remediation,
    run_storage_repository_hygiene,
    run_system_learning_assurance,
    run_technical_debt_elimination,
    run_testing_gaps_assurance,
    run_third_party_tool_consolidation,
    run_timeout_lock_watchdog,
    run_tokenomics_cost_optimization,
    run_weekly_market_intelligence,
    run_worktree_coverage_cleanup,
)
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import TEN_GOVERNED_TASK_IDS, ExecutionMode, TriggerOrigin
from tools.scheduled_task_core.findings import DurableFindingsLedger
from tools.scheduled_task_core.handoff import DurableHandoffPackage, create_durable_handoff
from tools.validate_implementation_plan import validate_plan_content


def _setup_gap_fixture(task_id: str, repo_root: Path) -> None:
    """Populates repository fixture with seeded defects for the given task."""
    if task_id == "cost_quality_review":
        reports_dir = repo_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "stage_receipt.json").write_text(
            json.dumps({"pricing_status": "UNPRICED_USAGE"}), encoding="utf-8"
        )
    elif task_id == "pipeline_outcome_assurance":
        directives_dir = repo_root / "directives"
        directives_dir.mkdir(parents=True, exist_ok=True)
        (directives_dir / "directive_01.json").write_text(
            json.dumps({"directive_id": "dir-e2e-01", "status": "FAILED"}), encoding="utf-8"
        )
    elif task_id == "repository_antipattern_audit":
        (repo_root / "monolithic_module.py").write_text(
            "\n".join([f"line_{i} = {i}" for i in range(850)]), encoding="utf-8"
        )
    elif task_id == "worktree_coverage_cleanup":
        (repo_root / ".worktrees" / "stub_coverage_wt").mkdir(parents=True, exist_ok=True)
    elif task_id == "storage_repository_hygiene":
        candidate = repo_root / "generations" / "gen_e2e" / "stale_output.bin"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"stale_bytes")
    elif task_id == "knowledge_graph_hygiene":
        db_path = repo_root / "graph.db"
        conn = create_sqlite_conn(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE assertion (id TEXT PRIMARY KEY, entity_id TEXT, claim TEXT, currentness_state TEXT, subject_node_id TEXT, object_node_id TEXT, primary_source_id TEXT, predicate TEXT, scope_text TEXT, object_literal TEXT)")
        cur.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_item_id TEXT, relevance_score REAL)")
        cur.execute("CREATE TABLE node (id TEXT, node_id TEXT PRIMARY KEY, canonical_name TEXT, label TEXT, node_type TEXT, active INTEGER)")
        cur.execute("INSERT INTO node VALUES ('orphan_node_1', 'orphan_node_1', 'Orphan', 'Orphan', 'entity', 1)")
        conn.commit()
        conn.close()
    elif task_id == "ssot_drift_remediation":
        (repo_root / "config.py").write_text("SSOT_DRIFT_CONSTANT = True\n", encoding="utf-8")
    elif task_id == "technical_debt_elimination":
        (repo_root / "legacy_debt_module.py").write_text(
            "\n".join([f"debt_line_{i} = {i}" for i in range(850)]), encoding="utf-8"
        )
    elif task_id == "timeout_lock_watchdog":
        (repo_root / "stale.lock").write_text("pid=999999\n", encoding="utf-8")
    elif task_id == "third_party_tool_consolidation":
        (repo_root / "redundant_wrapper.py").write_text("# wrapper\n", encoding="utf-8")
    elif task_id == "weekly_market_intelligence":
        # Defaults to finding signals if not provided
        pass
    elif task_id == "pr_queue_observer":
        wt_a = repo_root / ".worktrees" / "branch_alpha"
        wt_b = repo_root / ".worktrees" / "branch_beta"
        wt_a.mkdir(parents=True, exist_ok=True)
        wt_b.mkdir(parents=True, exist_ok=True)
        (wt_a / "conflict.py").write_text("# alpha\n", encoding="utf-8")
        (wt_b / "conflict.py").write_text("# beta\n", encoding="utf-8")
    elif task_id == "frontier_llm_research_scout":
        (repo_root / "candidate_innovation.json").write_text("{}", encoding="utf-8")
    elif task_id == "testing_gaps_assurance":
        (repo_root / "untested_hotspot.py").write_text(
            "\n".join([f"gap_line_{i} = {i}" for i in range(850)]), encoding="utf-8"
        )
    elif task_id == "e2e_critical_functionality_assurance":
        (repo_root / "broken_e2e_handoff.py").write_text("# broken\n", encoding="utf-8")
    elif task_id == "corner_foresight_blindspot_scout":
        (repo_root / "expiring_commitment.json").write_text("{}", encoding="utf-8")
    elif task_id == "hotspot_value_enhancement":
        (repo_root / "fragile_hotspot.py").write_text("# fragile hotspot\n", encoding="utf-8")
    elif task_id == "tokenomics_cost_optimization":
        (repo_root / "unoptimized_token_hotspots.json").write_text("{}", encoding="utf-8")
    elif task_id == "agentic_repo_best_practices_scout":
        (repo_root / "candidate_agentic_repo_pattern.json").write_text("{}", encoding="utf-8")
    elif task_id == "retrieval_surface_assurance":
        (repo_root / "retrieval_surface_gap.json").write_text("{}", encoding="utf-8")
    elif task_id == "system_learning_assurance":
        (repo_root / "system_learning_gap.json").write_text("{}", encoding="utf-8")
    elif task_id == "evals_surface_assurance":
        (repo_root / "evals_surface_gap.json").write_text("{}", encoding="utf-8")


ADAPTER_RUNNERS: dict[str, Callable[[AuthorityContext, Path, Path], TaskExecutionResult]] = {
    "cost_quality_review": run_cost_quality_review,
    "pipeline_outcome_assurance": run_pipeline_outcome_assurance,
    "repository_antipattern_audit": run_repository_antipattern_audit,
    "worktree_coverage_cleanup": run_worktree_coverage_cleanup,
    "storage_repository_hygiene": run_storage_repository_hygiene,
    "knowledge_graph_hygiene": run_knowledge_graph_hygiene,
    "ssot_drift_remediation": run_ssot_drift_remediation,
    "technical_debt_elimination": run_technical_debt_elimination,
    "timeout_lock_watchdog": run_timeout_lock_watchdog,
    "third_party_tool_consolidation": run_third_party_tool_consolidation,
    "weekly_market_intelligence": lambda ctx, root, rdir: run_weekly_market_intelligence(ctx, root, rdir, candidate_signals=None),
    "pr_queue_observer": run_pr_queue_observer,
    "frontier_llm_research_scout": run_frontier_llm_research_scout,
    "testing_gaps_assurance": run_testing_gaps_assurance,
    "e2e_critical_functionality_assurance": run_e2e_critical_functionality_assurance,
    "corner_foresight_blindspot_scout": run_corner_foresight_blindspot_scout,
    "hotspot_value_enhancement": run_hotspot_value_enhancement,
    "tokenomics_cost_optimization": run_tokenomics_cost_optimization,
    "agentic_repo_best_practices_scout": run_agentic_repo_best_practices_scout,
    "retrieval_surface_assurance": run_retrieval_surface_assurance,
    "system_learning_assurance": run_system_learning_assurance,
    "evals_surface_assurance": run_evals_surface_assurance,
}


@pytest.mark.parametrize("task_id", TEN_GOVERNED_TASK_IDS)
def test_all_ten_adapters_e2e_scheduled_mode_with_gaps(tmp_path: Path, task_id: str) -> None:
    """Verifies that all 10 adapters in scheduled mode generate a strictly valid plan and halt."""
    repo_root = tmp_path / f"repo_{task_id}"
    repo_root.mkdir(parents=True)
    run_dir = tmp_path / f"run_{task_id}"
    run_dir.mkdir(parents=True)

    _setup_gap_fixture(task_id, repo_root)

    ctx = AuthorityContext(
        task_id=task_id,
        run_id=f"run-e2e-{task_id}",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )

    runner = ADAPTER_RUNNERS[task_id]
    result = runner(ctx, repo_root, run_dir)

    # 1. Terminal outcome halts safely without unapproved mutations
    assert result.terminal_outcome in (
        "HITL_REQUIRED",
        "AWAITING_APPROVAL",
        "OPERATOR_INPUT_REQUIRED",
        "NOTIFICATION_EMITTED",
    )
    assert result.mutations_count == 0

    # 2. Plan path and digest are populated
    assert result.implementation_plan_path is not None, f"Task {task_id} did not attach plan path"
    assert result.implementation_plan_sha256 is not None, f"Task {task_id} did not attach plan sha256"
    assert result.implementation_plan_path.is_file(), f"Plan file does not exist: {result.implementation_plan_path}"

    # 3. Plan content passes strict governance validation
    plan_text = result.implementation_plan_path.read_text(encoding="utf-8")
    is_valid, errors = validate_plan_content(plan_text, filename=result.implementation_plan_path.name, strict=True)
    assert is_valid, f"Task {task_id} generated plan failed strict validation: {errors}"

    # 4. Computed SHA-256 matches
    expected_sha = hashlib.sha256(result.implementation_plan_path.read_bytes()).hexdigest()
    assert result.implementation_plan_sha256 == expected_sha

    # 5. Dual persistence: plan exists in run_dir and docs/plans
    assert (run_dir / "implementation_plan.md").is_file()
    plans_dir = repo_root / "docs" / "plans"
    assert plans_dir.is_dir()
    plan_files = list(plans_dir.glob("*.md"))
    assert len(plan_files) >= 1

    # 6. Durable handoff package integration
    ledger = DurableFindingsLedger(task_id=task_id)
    finding = ledger.record_finding(signature=f"sig:{task_id}", run_id=ctx.run_id)
    handoff = create_durable_handoff(
        task_id=task_id,
        run_id=ctx.run_id,
        findings=[finding],
        authority=ctx,
        implementation_plan_path=result.implementation_plan_path,
        implementation_plan_sha256=result.implementation_plan_sha256,
        require_plan=True,
    )
    assert handoff.verify_plan_sealed() is True
    assert handoff.implementation_plan_path == str(result.implementation_plan_path)
    assert handoff.implementation_plan_sha256 == result.implementation_plan_sha256


@pytest.mark.parametrize("task_id", TEN_GOVERNED_TASK_IDS)
def test_all_ten_adapters_e2e_interactive_mode_with_gaps(tmp_path: Path, task_id: str) -> None:
    """Verifies that all 10 adapters in interactive mode bind plans and prevent unapproved mutations."""
    repo_root = tmp_path / f"repo_interactive_{task_id}"
    repo_root.mkdir(parents=True)
    run_dir = tmp_path / f"run_interactive_{task_id}"
    run_dir.mkdir(parents=True)

    _setup_gap_fixture(task_id, repo_root)

    ctx = AuthorityContext(
        task_id=task_id,
        run_id=f"run-e2e-int-{task_id}",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="operator",
        is_scheduled=False,
    )

    runner = ADAPTER_RUNNERS[task_id]
    result = runner(ctx, repo_root, run_dir)

    # In interactive mode without approval, mutations are zero and plans are attached
    assert result.mutations_count == 0
    assert result.terminal_outcome in (
        "AWAITING_APPROVAL",
        "COMPLETE",
        "OBSERVATION_RECORDED",
        "OBSERVATION_EXPLAINED",
    )
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert result.implementation_plan_path.is_file()

    plan_text = result.implementation_plan_path.read_text(encoding="utf-8")
    is_valid, errors = validate_plan_content(plan_text, filename=result.implementation_plan_path.name, strict=True)
    assert is_valid, f"Task {task_id} generated plan failed strict validation: {errors}"


def test_clean_execution_mode_zero_gaps(tmp_path: Path) -> None:
    """Verifies that clean repositories unconditionally emit and validate a 2-wave zero-delta verification plan."""
    repo_root = tmp_path / "repo_clean"
    repo_root.mkdir(parents=True)
    run_dir = tmp_path / "run_clean"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="storage_repository_hygiene",
        run_id="run-clean-001",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )

    result = run_storage_repository_hygiene(ctx, repo_root, run_dir)
    assert result.terminal_outcome in ("PASS", "CLEAN", "COMPLETE")
    assert result.mutations_count == 0
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_sha256 is not None
    assert (run_dir / "implementation_plan.md").is_file()
    plan_text = result.implementation_plan_path.read_text(encoding="utf-8")
    is_valid, errors = validate_plan_content(plan_text, filename="implementation_plan.md", strict=True)
    assert is_valid, f"Clean execution zero-gap plan failed strict validation: {errors}"


def test_ssot_agent_skills_capacity_cleanup_e2e(tmp_path: Path) -> None:
    """Verifies that run_ssot_drift_remediation detects >= 90% skills capacity and cleans up skills on approval."""
    from tools.scheduled_task_core.authority import ApprovalRecord

    repo_root = tmp_path / "repo_skills_cap"
    skills_dir = repo_root / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    run_dir = tmp_path / "run_skills_cap"
    run_dir.mkdir(parents=True)

    # Seed 24 registered skills + 1 unregistered skill = 25/26 skills (96.15% >= 90% count capacity)
    reg_names = [f"skill-cap-{i:02d}" for i in range(24)]
    for name in reg_names + ["unregistered-overflow-skill"]:
        sdir = skills_dir / name
        sdir.mkdir()
        (sdir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use this skill when testing {name} capacity management.\n---\n# {name}\n",
            encoding="utf-8",
        )
    (repo_root / ".agents" / "skills.json").write_text(
        json.dumps({"skills": [{"name": n} for n in reg_names], "plugin_skills": []}),
        encoding="utf-8",
    )

    # 1. Scheduled run detects >= 90% skills capacity and emits HITL_REQUIRED + plan
    sched_ctx = AuthorityContext(
        task_id="ssot_drift_remediation",
        run_id="run-ssot-skills-sched",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )
    sched_res = run_ssot_drift_remediation(sched_ctx, repo_root, run_dir)
    assert sched_res.terminal_outcome == "HITL_REQUIRED"
    assert any(f.get("issue") == "SKILLS_CAPACITY_THRESHOLD_EXCEEDED" for f in sched_res.findings)
    assert (skills_dir / "unregistered-overflow-skill").exists()

    # 2. Interactive approved run cleans up unregistered skill and compacts descriptions
    int_ctx = AuthorityContext(
        task_id="ssot_drift_remediation",
        run_id="run-ssot-skills-int",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="operator",
        is_scheduled=False,
    )
    approval = {
        "approval_id": "app-ssot-skills-001",
        "task_id": "ssot_drift_remediation",
        "run_id": "run-ssot-skills-int",
        "operator": "human_operator",
        "signature": "sig-ed25519-valid",
        "status": "APPROVED",
        "approved_items": [],
        "plan_digest": "digest-123",
        "baseline_sha": "sha-123",
        "issued_at": "2026-10-26T12:00:00Z",
        "expires_at": "2099-12-31T23:59:59Z",
        "replay_nonce": "nonce-skills-cap-001",
    }
    int_res = run_ssot_drift_remediation(int_ctx, repo_root, run_dir, approval=approval)
    assert int_res.terminal_outcome == "COMPLETE"
    assert int_res.mutations_count >= 1
    assert not (skills_dir / "unregistered-overflow-skill").exists()


def test_ssot_drift_remediation_rules_capacity_and_dedup_lifecycle(tmp_path: Path) -> None:
    """Verifies that run_ssot_drift_remediation detects >90% duplicate rules and cleans them up on approval."""
    repo_root = tmp_path / "repo_rules_cap"
    rules_dir = repo_root / ".agents" / "rules"
    rules_dir.mkdir(parents=True)
    run_dir = tmp_path / "run_rules_cap"
    run_dir.mkdir(parents=True)

    agents_md = repo_root / "AGENTS.md"
    agents_md.write_text("# Agents Index\n", encoding="utf-8")

    block = "This is a governed rule block that must strictly enforce data integrity and prevent any uncoordinated mutations across storage layers."
    rule_a = rules_dir / "rule_a.md"
    rule_a.write_text(f"---\ntrigger: always_on\n---\n# Rule A\n\n{block}\n", encoding="utf-8")
    rule_b = rules_dir / "rule_b.md"
    rule_b.write_text(f"---\ntrigger: always_on\n---\n# Rule B\n\n{block}\n", encoding="utf-8")

    # 1. Scheduled run detects duplicate rules and emits HITL_REQUIRED + plan
    sched_ctx = AuthorityContext(
        task_id="ssot_drift_remediation",
        run_id="run-ssot-rules-sched",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        is_scheduled=True,
    )
    sched_res = run_ssot_drift_remediation(sched_ctx, repo_root, run_dir)
    assert sched_res.terminal_outcome == "HITL_REQUIRED"
    assert any(f.get("issue") == "RULES_CAPACITY_OR_DEDUP_THRESHOLD_EXCEEDED" for f in sched_res.findings)
    assert block in rule_b.read_text(encoding="utf-8")

    # 2. Interactive approved run cleans up duplicate block into SSOT link
    int_ctx = AuthorityContext(
        task_id="ssot_drift_remediation",
        run_id="run-ssot-rules-int",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="operator",
        is_scheduled=False,
    )
    approval = {
        "approval_id": "app-ssot-rules-001",
        "task_id": "ssot_drift_remediation",
        "run_id": "run-ssot-rules-int",
        "operator": "human_operator",
        "signature": "sig-ed25519-valid",
        "status": "APPROVED",
        "approved_items": [],
        "plan_digest": "digest-123",
        "baseline_sha": "sha-123",
        "issued_at": "2026-10-26T12:00:00Z",
        "expires_at": "2099-12-31T23:59:59Z",
        "replay_nonce": "nonce-rules-cap-001",
    }
    int_res = run_ssot_drift_remediation(int_ctx, repo_root, run_dir, approval=approval)
    assert int_res.terminal_outcome == "COMPLETE"
    assert int_res.mutations_count >= 1
    content_b = rule_b.read_text(encoding="utf-8")
    assert "See [rule_a.md]" in content_b
    assert block not in content_b

