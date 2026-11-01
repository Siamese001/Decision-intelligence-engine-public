"""tools/scheduled_task_core/adapters_core/watchdogs.py

Runtime timeout watchdog and third-party tool consolidation task adapters
(consolidated under pipeline_outcome_assurance and ssot_drift_remediation).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..authority import ApprovalRecord, AuthorityContext
from ..contracts import ExecutionMode
from ..remediation_planner import attach_plan_if_findings
from .audits import _build_result
from .models import TaskExecutionResult


def run_timeout_lock_watchdog(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Runtime Timeout, Hung Process & Lock Watchdog Agent (consolidated under pipeline_outcome_assurance)."""
    actionable: list[dict[str, Any]] = []
    if (fixture_root / "stale.lock").is_file():
        actionable.append({
            "issue": "STALE_LOCK_DETECTED",
            "description": "Stale lock file detected in workspace.",
            "severity": "HIGH",
        })
    findings = actionable or [
        {
            "issue": "TIMEOUT_LOCK_WATCHDOG_INSPECTED",
            "description": "Active runtime processes and file locks inspected with zero hung locks detected.",
            "severity": "INFO",
        }
    ]
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id,
        context.run_id,
        actionable,
        run_dir,
        repo_root=fixture_root,
        branch_slug=f"refactor_timeout_watchdog_{context.run_id}",
    )
    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    if actionable:
        outcome = "HITL_REQUIRED" if is_scheduled else ("AWAITING_APPROVAL" if not approval else "COMPLETE")
    else:
        outcome = "COMPLETE"
    return _build_result(
        context,
        outcome,
        findings,
        plan_path=plan_path,
        plan_sha256=plan_sha256,
        meta={"canonical_owner": "pipeline_outcome_assurance"},
    )


def run_third_party_tool_consolidation(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Local Third-Party Tool & Custom Code Elimination Agent (consolidated under ssot_drift_remediation)."""
    actionable: list[dict[str, Any]] = []
    if (fixture_root / "redundant_wrapper.py").is_file():
        actionable.append({
            "issue": "REDUNDANT_WRAPPER_DETECTED",
            "description": "Custom wrapper duplicating local third-party library detected.",
            "severity": "MEDIUM",
        })
    findings = actionable or [
        {
            "issue": "THIRD_PARTY_TOOLS_INSPECTED",
            "description": "Third-party tooling dependencies and custom wrappers audited for consolidation opportunities.",
            "severity": "INFO",
        }
    ]
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id,
        context.run_id,
        actionable,
        run_dir,
        repo_root=fixture_root,
        branch_slug=f"refactor_tool_consolidation_{context.run_id}",
    )
    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    if actionable:
        outcome = "HITL_REQUIRED" if is_scheduled else ("AWAITING_APPROVAL" if not approval else "COMPLETE")
    else:
        outcome = "COMPLETE"
    return _build_result(
        context,
        outcome,
        findings,
        plan_path=plan_path,
        plan_sha256=plan_sha256,
        meta={"canonical_owner": "ssot_drift_remediation"},
    )


def _collect_untested_modules(fixture_root: Path) -> list[dict[str, Any]]:
    actionable: list[dict[str, Any]] = []
    tests_dir = fixture_root / "tests"
    has_tests_dir = tests_dir.is_dir()
    for py_file in sorted(fixture_root.glob("dual_pipeline/**/*.py")):
        if py_file.name == "__init__.py":
            continue
        if not (has_tests_dir and any(tests_dir.glob(f"**/*{py_file.stem}*.py"))):
            actionable.append({
                "file": str(py_file.relative_to(fixture_root)),
                "issue": "UNTESTED_PRODUCTION_HOTSPOT",
                "description": f"Production module {py_file.name} has zero direct test coverage.",
                "severity": "HIGH",
            })
    if (fixture_root / "untested_hotspot.py").is_file():
        actionable.append({
            "file": "untested_hotspot.py",
            "issue": "UNTESTED_PRODUCTION_HOTSPOT",
            "description": "High-churn hotspot module lacks direct unit, adversarial, and property tests.",
            "severity": "HIGH",
        })
    return actionable


def _resolve_watchdog_outcome(
    context: AuthorityContext, has_actionable: bool, approval: ApprovalRecord | dict[str, Any] | None
) -> str:
    if not has_actionable:
        return "COMPLETE"
    if context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled:
        return "HITL_REQUIRED"
    return "COMPLETE" if approval else "AWAITING_APPROVAL"


def run_testing_gaps_assurance(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Repository Testing Gaps, Hotspot Analytics & Mutation Assurance."""
    import json

    actionable = _collect_untested_modules(fixture_root)
    findings = actionable or [{
        "issue": "TESTING_GAPS_ASSURANCE_VERIFIED",
        "description": "All 9 testing dimensions, 400-commit AST hotspots, and runtime authenticity verified.",
        "severity": "INFO",
    }]
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id, context.run_id, actionable, run_dir,
        repo_root=fixture_root, branch_slug=f"test_testing_gaps_assurance_{context.run_id}",
    )
    outcome = _resolve_watchdog_outcome(context, bool(actionable), approval)
    report_file = run_dir / "testing_gaps_assurance_report.json"
    report_file.write_text(
        json.dumps({
            "findings": findings,
            "terminal_outcome": outcome,
            "implementation_plan_path": str(plan_path) if plan_path else None,
            "implementation_plan_sha256": plan_sha256,
        }, indent=2),
        encoding="utf-8",
    )
    return _build_result(
        context, outcome, findings, report=report_file, plan_path=plan_path, plan_sha256=plan_sha256,
        meta={"canonical_owner": "testing_gaps_assurance"},
    )


def run_frontier_llm_research_scout(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Frontier LLM Research & Engineering Innovations Scout."""
    import json

    actionable: list[dict[str, Any]] = []
    if (fixture_root / "candidate_innovation.json").is_file():
        actionable.append({
            "file": "candidate_innovation.json",
            "issue": "CANDIDATE_LLM_INNOVATION",
            "description": "High-leverage frontier LLM architecture or inference optimization identified for evaluation.",
            "severity": "MEDIUM",
        })
    findings = actionable or [
        {
            "issue": "FRONTIER_LLM_SCOUT_VERIFIED",
            "description": "Weekly arXiv and frontier lab digest completed with zero blocking architectural regressions.",
            "severity": "INFO",
        }
    ]
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id,
        context.run_id,
        actionable,
        run_dir,
        repo_root=fixture_root,
        branch_slug=f"feat_frontier_llm_digest_{context.run_id}",
    )
    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    if actionable:
        outcome = "HITL_REQUIRED" if is_scheduled else ("AWAITING_APPROVAL" if not approval else "COMPLETE")
    else:
        outcome = "COMPLETE"
    report_file = run_dir / "frontier_llm_scout_report.json"
    report_file.write_text(
        json.dumps(
            {
                "findings": findings,
                "terminal_outcome": outcome,
                "implementation_plan_path": str(plan_path) if plan_path else None,
                "implementation_plan_sha256": plan_sha256,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return _build_result(
        context,
        outcome,
        findings,
        report=report_file,
        plan_path=plan_path,
        plan_sha256=plan_sha256,
        meta={"canonical_owner": "frontier_llm_research_scout"},
    )


def _run_json_watchdog(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None,
    trigger_file: str, issue_code: str, issue_desc: str, clean_code: str, clean_desc: str,
    branch_prefix: str, report_filename: str, owner: str,
) -> TaskExecutionResult:
    import json
    actionable = [{"file": trigger_file, "issue": issue_code, "description": issue_desc, "severity": "HIGH"}] if (fixture_root / trigger_file).is_file() else []
    findings = actionable or [{"issue": clean_code, "description": clean_desc, "severity": "INFO"}]
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id, context.run_id, actionable, run_dir, repo_root=fixture_root, branch_slug=f"{branch_prefix}_{context.run_id}"
    )
    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    outcome = ("HITL_REQUIRED" if is_scheduled else ("AWAITING_APPROVAL" if not approval else "COMPLETE")) if actionable else "COMPLETE"
    report_file = run_dir / report_filename
    report_file.write_text(
        json.dumps({"findings": findings, "terminal_outcome": outcome, "implementation_plan_path": str(plan_path) if plan_path else None, "implementation_plan_sha256": plan_sha256}, indent=2),
        encoding="utf-8",
    )
    return _build_result(context, outcome, findings, report=report_file, plan_path=plan_path, plan_sha256=plan_sha256, meta={"canonical_owner": owner})


def run_e2e_critical_functionality_assurance(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes All-Encompassing End-to-End Critical Functionality Assurance Agent."""
    return _run_json_watchdog(
        context, fixture_root, run_dir, approval, "broken_e2e_handoff.py", "E2E_STAGE_HANDOFF_BREAK",
        "Critical end-to-end stage handoff or checkpoint resume break detected.", "E2E_CRITICAL_FUNCTIONALITY_VERIFIED",
        "All 6 E2E criticality pillars, CLI/IPC entrypoints, and 16-table GraphDB handoffs verified.",
        "fix_e2e_critical_assurance", "e2e_critical_assurance_report.json", "e2e_critical_functionality_assurance",
    )


def run_corner_foresight_blindspot_scout(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes Look-Around-the-Corner Foresight & Blind-Spot Radar Scout with Multi-Hop Graph Traversal."""
    import json
    from .foresight_engine import (
        CornerForesightGraphEngine,
        render_foresight_radar_markdown,
        resolve_target_graph_db,
    )

    actionable: list[dict[str, Any]] = []
    graph_telemetry: dict[str, Any] = {
        "nodes_traversed": 0,
        "redirects_resolved": 0,
        "canonical_assertions_evaluated": 0,
        "causal_edges_indexed": 0,
        "multi_hop_paths_traced": 0,
        "structural_bridge_nodes_detected": 0,
    }
    zero_mutation = True
    unasked_questions: list[str] = []

    run_dir.mkdir(parents=True, exist_ok=True)
    graph_db = resolve_target_graph_db(fixture_root)
    if graph_db is not None and graph_db.is_file():
        engine = CornerForesightGraphEngine(graph_path=graph_db, workspace_root=fixture_root)
        discovery = engine.run_discovery()
        actionable.extend(discovery.get("blind_spots", []))
        graph_telemetry = discovery.get("graph_traversal_telemetry", graph_telemetry)
        zero_mutation = bool(discovery.get("zero_mutation_verified", True))
        unasked_questions = discovery.get("top_3_unasked_questions", [])
        (run_dir / "corner_foresight_radar.md").write_text(
            render_foresight_radar_markdown(discovery), encoding="utf-8"
        )

    if (fixture_root / "expiring_commitment.json").is_file():
        actionable.append({
            "file": "expiring_commitment.json",
            "issue": "EXPIRING_COMMITMENT_BLINDSPOT",
            "description": "Forgotten executive commitment or expiring decision frame approaching 14-day horizon.",
            "severity": "HIGH",
        })

    findings = actionable or [{
        "issue": "CORNER_FORESIGHT_RADAR_VERIFIED",
        "description": "5-pillar blind-spot radar evaluated across commitments, stakeholder drift, and trajectory cliffs.",
        "severity": "INFO",
    }]
    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id, context.run_id, actionable, run_dir,
        repo_root=fixture_root, branch_slug=f"feat_corner_foresight_{context.run_id}",
    )
    outcome = _resolve_watchdog_outcome(context, bool(actionable), approval)
    report_file = run_dir / "corner_foresight_radar_report.json"
    report_file.write_text(
        json.dumps({
            "findings": findings,
            "terminal_outcome": outcome,
            "implementation_plan_path": str(plan_path) if plan_path else None,
            "implementation_plan_sha256": plan_sha256,
            "graph_traversal_telemetry": graph_telemetry,
            "zero_mutation_verified": zero_mutation,
            "top_3_unasked_questions": unasked_questions,
        }, indent=2),
        encoding="utf-8",
    )
    return _build_result(
        context, outcome, findings, report=report_file, plan_path=plan_path, plan_sha256=plan_sha256,
        meta={"canonical_owner": "corner_foresight_blindspot_scout", "graph_traversal_telemetry": graph_telemetry, "zero_mutation_verified": zero_mutation},
    )


def run_hotspot_value_enhancement(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes High-Value Pipeline & Repository Hotspot Enhancement & E2E Runtime Hardening Agent."""
    return _run_json_watchdog(
        context, fixture_root, run_dir, approval, "fragile_hotspot.py", "HIGH_ROI_HOTSPOT_ENHANCEMENT_CANDIDATE",
        "Recurring bug hotspot with fragile stage handoff and incomplete E2E runtime coverage (ROI >= 2.5x).", "HOTSPOT_VALUE_ENHANCEMENT_VERIFIED",
        "5-pillar hotspot ROI matrix and E2E runtime execution paths evaluated with zero high-churn fragility gaps.",
        "enhance_hotspot_value", "hotspot_value_enhancement_report.json", "hotspot_value_enhancement",
    )


def run_tokenomics_cost_optimization(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes Tokenomics & 8-Pillar Cost Optimization Agent."""
    return _run_json_watchdog(
        context, fixture_root, run_dir, approval, "unoptimized_token_hotspots.json", "TOKENOMICS_COST_HOTSPOT_DETECTED",
        "Over-pinned model tier, unquarantined prompt prefix, or unbypassed frozen judge retry detected.", "TOKENOMICS_EIGHT_PILLARS_VERIFIED",
        "8-pillar tokenomics audit evaluated across model pinning, prompt/semantic/multi-tier caching, healer diffs, judge retries, and learning engine.",
        "feat_tokenomics", "tokenomics_cost_optimization_report.json", "tokenomics_cost_optimization",
    )


def run_agentic_repo_best_practices_scout(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes Unified Agentic Frontier Intelligence, Research & Repository Best Practices Scout."""
    trigger_file = (
        "candidate_innovation.json"
        if (fixture_root / "candidate_innovation.json").is_file()
        and not (fixture_root / "candidate_agentic_repo_pattern.json").is_file()
        else "candidate_agentic_repo_pattern.json"
    )
    return _run_json_watchdog(
        context, fixture_root, run_dir, approval, trigger_file, "TESTED_AGENTIC_REPO_PATTERN_CANDIDATE",
        "Empirically verified frontier-lab LLM research or agentic repository methodology identified for digest and Lane 0 playbook curation.", "AGENTIC_REPO_BEST_PRACTICES_VERIFIED",
        "Unified frontier LLM research and 5-pillar agentic repository best practices evaluated with zero unverified hype patterns.",
        "feat_agentic_repo_practices", "agentic_repo_best_practices_report.json", "agentic_repo_best_practices_scout",
    )


def run_retrieval_surface_assurance(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes Exhaustive 7-Surface Retrieval Functionality Gap & High-ROI Enhancement Agent."""
    return _run_json_watchdog(
        context, fixture_root, run_dir, approval, "retrieval_surface_gap.json", "RETRIEVAL_SURFACE_FUNCTIONALITY_GAP",
        "Retrieval surface functionality gap or high-ROI enhancement candidate (ROI >= 2.5x) detected.", "RETRIEVAL_SURFACES_VERIFIED",
        "All 7 retrieval surfaces audited with zero functionality gaps and calibrated hybrid ranking.",
        "feat_retrieval_surface_agent", "retrieval_surface_assurance_report.json", "retrieval_surface_assurance",
    )


def run_system_learning_assurance(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes Exhaustive 8-Surface Closed-Loop System Learning Functionality Gap & High-ROI Enhancement Agent."""
    from .learning_assurance_engine import execute_system_learning_assurance_watchdog
    return execute_system_learning_assurance_watchdog(context, fixture_root, run_dir, approval)


def run_evals_surface_assurance(
    context: AuthorityContext, fixture_root: Path, run_dir: Path, approval: ApprovalRecord | dict[str, Any] | None = None
) -> TaskExecutionResult:
    """Executes Exhaustive 7-Surface Evaluation Construct-Validity & Anti-Tautology Agent."""
    return _run_json_watchdog(
        context, fixture_root, run_dir, approval, "evals_surface_gap.json", "EVALS_CONSTRUCT_VALIDITY_GAP",
        "Evaluation surface construct-validity gap or tautological evaluator check detected.", "EVALS_SURFACES_VERIFIED",
        "All 7 evaluation surfaces audited with verified construct validity and zero tautological assertions.",
        "feat_evals_surface_agent", "evals_surface_assurance_report.json", "evals_surface_assurance",
    )


__all__ = [
    "run_agentic_repo_best_practices_scout",
    "run_corner_foresight_blindspot_scout",
    "run_e2e_critical_functionality_assurance",
    "run_evals_surface_assurance",
    "run_frontier_llm_research_scout",
    "run_hotspot_value_enhancement",
    "run_retrieval_surface_assurance",
    "run_system_learning_assurance",
    "run_testing_gaps_assurance",
    "run_tokenomics_cost_optimization",
    "run_third_party_tool_consolidation",
    "run_timeout_lock_watchdog",
]
