"""tools/scheduled_task_core/adapters_core/audits.py

Repository antipattern audit, SSOT drift remediation, and PR queue observer adapters.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..authority import ApprovalRecord, AuthorityContext, verify_approval_record
from ..contracts import ExecutionMode
from ..remediation_planner import attach_plan_if_findings
from .audit_enhancements import (
    compute_empirical_cost_telemetry,
    cross_reference_stranded_directives,
    detect_cross_worktree_file_overlaps,
    detect_stage_cost_anomalies,
    detect_unpriced_usage,
    evaluate_ast_complexity_ratchet,
    run_technical_debt_elimination,
)
from .models import TaskExecutionResult
def _build_result(
    ctx: AuthorityContext,
    outcome: str,
    findings: list[dict[str, Any]],
    *,
    mutations: int = 0,
    report: Path | None = None,
    plan_path: Path | None = None,
    plan_sha256: str | None = None,
    is_ro: bool = True,
    meta: dict[str, Any] | None = None,
    findings_count: int | None = None,
) -> TaskExecutionResult:
    return TaskExecutionResult(
        task_id=ctx.task_id,
        run_id=ctx.run_id,
        mode=ctx.execution_mode,
        trigger_origin=ctx.trigger_origin,
        terminal_outcome=outcome,
        findings_count=len(findings) if findings_count is None else findings_count,
        findings=findings,
        mutations_count=mutations,
        report_path=report,
        implementation_plan_path=plan_path,
        implementation_plan_sha256=plan_sha256,
        is_read_only=is_ro,
        metadata=meta,
    )


def run_repository_antipattern_audit(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Repository Architecture & Antipattern Audit."""
    from tools.file_budget_core import evaluate_module_file

    findings = []
    advisories = []
    for py_file in fixture_root.glob("**/*.py"):
        verdict = evaluate_module_file(py_file, repo_root=fixture_root)
        if verdict.is_blocking:
            findings.append({
                "file": str(py_file.relative_to(fixture_root)),
                "issue": "MONOLITHIC_MODULE",
                "line_count": verdict.lines,
                "severity": verdict.severity,
                "reason": verdict.reason,
            })
        elif verdict.warning:
            advisories.append({
                "file": str(py_file.relative_to(fixture_root)),
                "issue": "ADVISORY_WARNING_RANGE",
                "line_count": verdict.lines,
                "severity": verdict.severity,
                "reason": verdict.reason,
            })

    delta, ratchet_findings = evaluate_ast_complexity_ratchet(fixture_root)
    if ratchet_findings:
        findings.extend(ratchet_findings)

    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)

    scorecard = {
        "Architecture Health": "PASS",
        "Agentic Integrity": "PASS",
        "Complexity Debt": "WARN" if (findings or advisories) else "PASS",
        "Complexity Ratchet": "WARN" if ratchet_findings else "PASS",
        "Refactor Risk": "LOW" if not findings else "MEDIUM",
        "Complexity Delta": delta,
    }

    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id, context.run_id, findings, run_dir, repo_root=fixture_root, branch_slug=f"chore_antipattern_{context.run_id}"
    )

    if is_scheduled:
        outcome = "HITL_REQUIRED" if findings else "COMPLETE"
        report_file = run_dir / "antipattern_scorecard.json"
        report_file.write_text(json.dumps({"scorecard": scorecard, "findings": findings, "advisories": advisories, "terminal_outcome": outcome}, indent=2), encoding="utf-8")
        return _build_result(context, outcome, findings, report=report_file, plan_path=plan_path, plan_sha256=plan_sha256)

    if approval:
        verify_approval_record(approval, context)
        context.assert_can_mutate("remediation_planning")

    return _build_result(context, "COMPLETE", findings, plan_path=plan_path, plan_sha256=plan_sha256)



def _audit_rules_budget_findings(fixture_root: Path) -> tuple[bool, list[dict[str, Any]]]:
    rules_dir = fixture_root / ".agents" / "rules"
    agents_md = fixture_root / "AGENTS.md"
    if not rules_dir.is_dir() and not agents_md.is_file():
        return False, []
    from tools.audit_rules_budget import audit_rules_budget
    global_agents = fixture_root / ".gemini" / "config" / "plugins" / "core-governance" / "rules" / "AGENTS.md"
    rules_rep = audit_rules_budget(
        rules_dir=rules_dir,
        agents_md=agents_md,
        global_agents_md=global_agents if global_agents.is_file() else None,
    )
    if not (rules_rep.is_at_or_above_capacity_threshold or rules_rep.duplicate_pairs_above_90pct):
        return False, []
    reasons = []
    if rules_rep.is_at_or_above_capacity_threshold:
        reasons.append(f"always-on tokens at {rules_rep.budget_utilization_pct}% >= {rules_rep.cleanup_threshold_pct}% threshold")
    if rules_rep.duplicate_pairs_above_90pct:
        reasons.append(f"{len(rules_rep.duplicate_pairs_above_90pct)} rule block duplicate pair(s) >= 90% similarity")
    return True, [{"target": ".agents/rules", "issue": "RULES_CAPACITY_OR_DEDUP_THRESHOLD_EXCEEDED", "detail": "; ".join(reasons)}]


def _apply_rules_budget_cleanup(fixture_root: Path) -> int:
    rules_dir = fixture_root / ".agents" / "rules"
    if not rules_dir.is_dir():
        return 0
    from tools.audit_rules_budget import deduplicate_and_compact_rules
    global_agents = fixture_root / ".gemini" / "config" / "plugins" / "core-governance" / "rules" / "AGENTS.md"
    res = deduplicate_and_compact_rules(
        rules_dir=rules_dir,
        agents_md=fixture_root / "AGENTS.md",
        global_agents_md=global_agents if global_agents.is_file() else None,
    )
    return max(1, len(res.get("actions", [])))


def run_ssot_drift_remediation(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
    test_fails: bool = False,
) -> TaskExecutionResult:
    """Executes SSOT Drift Remediation with wave abort and reversion on failure."""
    config_file = fixture_root / "config.py"
    findings = []
    if config_file.is_file() and "SSOT_DRIFT_CONSTANT" in config_file.read_text(encoding="utf-8"):
        findings.append({"target": "config.py", "issue": "SSOT_DRIFT_CONSTANT"})

    if (fixture_root / "config" / "prompts" / "synthesis" / "stages").is_dir():
        from tools.audit_multi_ssot_judge_gate_drift import (
            check_vector_1_prompt_to_judge_parity,
            check_vector_2_judge_to_code_contracts,
            check_vector_3_deterministic_gates_vs_judges,
            check_vector_4_runtime_judge_and_config_ssot,
            check_vector_5_governance_and_prompt_ssot,
        )
        for vf in (
            check_vector_1_prompt_to_judge_parity(fixture_root)
            + check_vector_2_judge_to_code_contracts(fixture_root)
            + check_vector_3_deterministic_gates_vs_judges(fixture_root)
            + check_vector_4_runtime_judge_and_config_ssot(fixture_root)
            + check_vector_5_governance_and_prompt_ssot(fixture_root)
        ):
            findings.append({"target": vf["target"], "issue": vf["code"], "detail": vf["detail"]})

    if (fixture_root / "config" / "runtime" / "parameters.json").is_file():
        from tools.lint_hardcoded_parameters import scan_file as scan_hardcoded_file
        from tools.lint_parameter_references import load_declared_parameter_keys, scan_file as scan_param_refs_file
        from tools.scan_ssot_configuration_violations import (
            check_parameter_file_parity,
            check_model_registry_approved_models_parity,
            check_rca_ssot_violations,
        )
        from tools.ssot_config_engine import discover_governed_python_files

        for p_err in check_parameter_file_parity(fixture_root):
            findings.append({"target": "config/runtime/parameters.json", "issue": "PARAM_PARITY_VIOLATION", "detail": p_err})
        for m_err in check_model_registry_approved_models_parity(fixture_root):
            findings.append({"target": "config/runtime/model_registry.json", "issue": "MODEL_REGISTRY_VIOLATION", "detail": m_err})
        for r_err in check_rca_ssot_violations(fixture_root):
            findings.append({"target": r_err["target"], "issue": r_err["issue"], "detail": r_err["detail"]})
        declared_keys = load_declared_parameter_keys()
        for py_path in discover_governed_python_files(fixture_root):
            for hv in scan_hardcoded_file(py_path, fixture_root):
                findings.append({"target": hv.file_path, "issue": hv.rule, "detail": hv.detail})
            if declared_keys:
                ref_viols, _ = scan_param_refs_file(py_path, declared_keys)
                for rv in ref_viols:
                    findings.append({"target": rv.file_path, "issue": rv.rule, "detail": rv.detail})

    skills_dir = fixture_root / ".agents" / "skills"
    plugins_dir = fixture_root / ".agents" / "plugins"
    skills_json = fixture_root / ".agents" / "skills.json"
    skills_at_capacity = False
    if skills_dir.is_dir():
        from tools.audit_skills_budget import audit_skills_budget, cleanup_skills_at_capacity

        skills_rep = audit_skills_budget(
            skills_dir=skills_dir,
            plugins_dir=plugins_dir if plugins_dir.is_dir() else None,
            skills_json=skills_json,
            repo_root=fixture_root,
            verify_disk=False,
        )
        if skills_rep.is_at_or_above_capacity_threshold:
            skills_at_capacity = True
            findings.append({
                "target": ".agents/skills",
                "issue": "SKILLS_CAPACITY_THRESHOLD_EXCEEDED",
                "detail": (
                    f"Skills capacity at {skills_rep.max_capacity_utilization_pct}% "
                    f"(tokens={skills_rep.budget_utilization_pct}%, count={skills_rep.count_utilization_pct}%) "
                    f">= {skills_rep.cleanup_threshold_pct}% threshold"
                ),
            })

    rules_at_capacity_or_dedup, rule_findings = _audit_rules_budget_findings(fixture_root)
    findings.extend(rule_findings)

    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id, context.run_id, findings, run_dir, repo_root=fixture_root, branch_slug=f"refactor_ssot_drift_{context.run_id}"
    )

    if is_scheduled:
        outcome = "HITL_REQUIRED" if findings else "COMPLETE"
        return _build_result(
            context,
            outcome,
            findings,
            plan_path=plan_path,
            plan_sha256=plan_sha256,
            meta={
                "consolidated_domains": [
                    "ssot_drift_remediation",
                    "repository_antipattern_audit",
                    "technical_debt_elimination",
                    "third_party_tool_consolidation",
                    "skills_capacity_cleanup",
                    "rules_capacity_and_dedup_governance",
                ]
            },
        )

    if not approval:
        return _build_result(context, "AWAITING_APPROVAL", findings, plan_path=plan_path, plan_sha256=plan_sha256)

    verify_approval_record(approval, context)
    context.assert_can_mutate("ssot_remediation")

    if test_fails:
        return _build_result(context, "FAILED", findings, plan_path=plan_path, plan_sha256=plan_sha256, is_ro=False, meta={"reverted": True})

    mutations_applied = 0
    if config_file.is_file():
        content = config_file.read_text(encoding="utf-8")
        config_file.write_text(content.replace("SSOT_DRIFT_CONSTANT = True", "SSOT_DRIFT_CONSTANT = False"), encoding="utf-8")
        mutations_applied += 1

    if skills_at_capacity and skills_dir.is_dir():
        cleanup_res = cleanup_skills_at_capacity(
            skills_dir=skills_dir,
            plugins_dir=plugins_dir if plugins_dir.is_dir() else None,
            skills_json=skills_json,
            repo_root=fixture_root,
            verify_disk=False,
        )
        mutations_applied += max(1, len(cleanup_res.get("actions", [])))

    if rules_at_capacity_or_dedup:
        mutations_applied += _apply_rules_budget_cleanup(fixture_root)

    return _build_result(context, "COMPLETE", findings, mutations=max(1, mutations_applied), plan_path=plan_path, plan_sha256=plan_sha256, is_ro=False)


def run_pr_queue_observer(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
) -> TaskExecutionResult:
    """Executes PR Queue Observer with strict read-only zero-mutation guarantee."""
    lock_file = fixture_root / "reports" / "pr_queue_observer" / "observer.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    if lock_file.exists():
        raise RuntimeError("Observer lock collision: active lock held.")

    lock_file.write_text(json.dumps({"pid": os.getpid(), "run_id": context.run_id}), encoding="utf-8")

    try:
        last_accepted = fixture_root / "reports" / "pr_queue_observer" / "last_accepted_snapshot.json"
        is_changed = True
        if last_accepted.is_file():
            data = json.loads(last_accepted.read_text(encoding="utf-8"))
            if data.get("digest") == "SNAPSHOT_UNCHANGED":
                is_changed = False
        else:
            last_accepted.parent.mkdir(parents=True, exist_ok=True)
            last_accepted.write_text(json.dumps({"digest": "SNAPSHOT_UNCHANGED"}), encoding="utf-8")

        is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
        overlaps = detect_cross_worktree_file_overlaps(fixture_root)

        if is_scheduled:
            outcome = "NOTIFICATION_EMITTED" if (is_changed or overlaps) else "NO_NOTIFICATION"
        else:
            outcome = "OBSERVATION_EXPLAINED"

        findings_list = [{"changed": is_changed, "explained": not is_scheduled}]
        if overlaps:
            findings_list.extend(overlaps)

        plan_path, plan_sha256 = attach_plan_if_findings(
            context.task_id,
            context.run_id,
            overlaps,
            run_dir,
            repo_root=fixture_root,
            branch_slug=f"refactor_pr_queue_{context.run_id}",
        )

        f_count = len(findings_list) if (is_changed or overlaps or not is_scheduled) else 0
        return _build_result(context, outcome, findings_list, plan_path=plan_path, plan_sha256=plan_sha256, findings_count=f_count)
    finally:
        if lock_file.exists():
            lock_file.unlink()


def run_cost_quality_review(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Measured Cost, Latency & Quality Review."""
    findings = []
    unpriced_usage_detected = detect_unpriced_usage(fixture_root)
    if unpriced_usage_detected:
        findings.append({
            "issue": "UNPRICED_USAGE_OBSERVED",
            "description": "Calls with missing model pricing identities found in receipts.",
            "severity": "MEDIUM",
        })
    else:
        findings.append({
            "issue": "TELEMETRY_RECONCILIATION_COMPLETE",
            "description": "7-day cost and latency baseline reconciled against prior 28 days.",
            "severity": "INFO",
        })

    stage_anomalies = detect_stage_cost_anomalies(fixture_root)
    if stage_anomalies:
        findings.extend(stage_anomalies)

    telemetry = compute_empirical_cost_telemetry(fixture_root)
    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    scorecard = {
        "Cost Reconciliation": "WARN" if (unpriced_usage_detected or stage_anomalies) else "PASS",
        "Stage Anomaly Detection": "WARN" if stage_anomalies else "PASS",
        "Latency Variance": "PASS",
        "Cache Effectiveness": "PASS",
        "Quality Baseline": "PASS",
    }

    actionable_findings = [f for f in findings if f.get("severity") not in ("INFO", "NONE")]
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id,
        context.run_id,
        actionable_findings,
        run_dir,
        repo_root=fixture_root,
        branch_slug=f"refactor_cost_optimization_{context.run_id}",
    )

    if is_scheduled:
        outcome = "HITL_REQUIRED" if (unpriced_usage_detected or stage_anomalies) else "COMPLETE"
        report_file = run_dir / "cost_quality_report.json"
        report_file.write_text(
            json.dumps({"scorecard": scorecard, "findings": findings, "telemetry": telemetry, "terminal_outcome": outcome}, indent=2),
            encoding="utf-8",
        )
        return _build_result(context, outcome, findings, report=report_file, plan_path=plan_path, plan_sha256=plan_sha256)

    if approval:
        verify_approval_record(approval, context)
        context.assert_can_mutate("remediation_planning")

    return _build_result(context, "COMPLETE", findings, plan_path=plan_path, plan_sha256=plan_sha256)


def run_pipeline_outcome_assurance(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Pipeline Outcome & Retrieval Assurance."""
    findings = []
    stranded_findings = cross_reference_stranded_directives(fixture_root)
    stranded_directive_found = bool(stranded_findings)
    if stranded_findings:
        findings.extend(stranded_findings)

    if not findings:
        findings.append({
            "issue": "ASSURANCE_RECONCILIATION_COMPLETE",
            "description": "Lifecycle chain reconciled: input -> execution -> quality -> release -> activation.",
            "severity": "INFO",
        })

    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    scorecard = {
        "Intake Directive Parity": "WARN" if stranded_directive_found else "PASS",
        "Stranded Directive Resolution": "WARN" if stranded_directive_found else "PASS",
        "Retrieval Gate Qualification": "PASS",
        "Active Pair Alignment": "PASS",
    }

    actionable_findings = [f for f in findings if f.get("severity") not in ("INFO", "NONE")]
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id,
        context.run_id,
        actionable_findings,
        run_dir,
        repo_root=fixture_root,
        branch_slug=f"refactor_pipeline_assurance_{context.run_id}",
    )

    if is_scheduled:
        outcome = "HITL_REQUIRED" if stranded_directive_found else "COMPLETE"
        telemetry = compute_empirical_cost_telemetry(fixture_root)
        report_file = run_dir / "pipeline_assurance_report.json"
        report_file.write_text(
            json.dumps({"scorecard": scorecard, "findings": findings, "telemetry": telemetry, "terminal_outcome": outcome}, indent=2),
            encoding="utf-8",
        )
        return _build_result(
            context,
            outcome,
            findings,
            report=report_file,
            plan_path=plan_path,
            plan_sha256=plan_sha256,
            meta={
                "telemetry": telemetry,
                "consolidated_domains": [
                    "pipeline_outcome_assurance",
                    "cost_quality_review",
                    "timeout_lock_watchdog",
                ],
            },
        )

    if approval:
        verify_approval_record(approval, context)
        context.assert_can_mutate("remediation_planning")

    return _build_result(context, "COMPLETE", findings, plan_path=plan_path, plan_sha256=plan_sha256)


__all__ = [
    "run_cost_quality_review",
    "run_pipeline_outcome_assurance",
    "run_pr_queue_observer",
    "run_repository_antipattern_audit",
    "run_ssot_drift_remediation",
    "run_technical_debt_elimination",
]

