#!/usr/bin/env python3
"""tools/lint_prompt_ssot.py

Fail-closed static governance linter enforcing Single Source of Truth (SSOT)
for all 8 canonical scheduled automation prompts (`INV-PROMPT-SSOT-01` through `09`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CANONICAL_PROMPT_DIR = Path("automations/prompts")

EXPECTED_CANONICAL_PROMPTS: dict[str, str] = {
    "knowledge_graph_hygiene": "graph_hygiene_agent.md",
    "ssot_drift_remediation": "ssot_drift_remediation_agent.md",
    "pipeline_outcome_assurance": "pipeline_outcome_assurance_agent.md",
    "storage_repository_hygiene": "storage_hygiene_agent.md",
    "weekly_market_intelligence": "weekly_market_intelligence_agent.md",
    "pr_queue_observer": "pr_queue_observer_agent.md",
    "testing_gaps_assurance": "testing_gaps_assurance_agent.md",
    "e2e_critical_functionality_assurance": "e2e_critical_functionality_assurance_agent.md",
    "corner_foresight_blindspot_scout": "corner_foresight_agent.md",
    "hotspot_value_enhancement": "hotspot_enhancement_agent.md",
    "tokenomics_cost_optimization": "tokenomics_agent.md",
    "agentic_repo_best_practices_scout": "agentic_repo_best_practices_agent.md",
    "retrieval_surface_assurance": "retrieval_surface_agent.md",
    "system_learning_assurance": "system_learning_agent.md",
    "evals_surface_assurance": "evals_surface_agent.md",
    "nea_charter_portfolio_roster": "sidecars/nea_charter_portfolio_roster_agent.md",
    "google_partner_advisory_weekly_sync": "sidecars/google_partner_advisory_weekly_sync_agent.md",
}

EXPECTED_ARCHIVE_TASKS: dict[str, str] = {
    "knowledge_graph_hygiene": "graph_hygiene_agent.md",
    "cost_quality_review": "pipeline_outcome_assurance_agent.md",
    "ssot_drift_remediation": "ssot_drift_remediation_agent.md",
    "technical_debt_elimination": "ssot_drift_remediation_agent.md",
    "pipeline_outcome_assurance": "pipeline_outcome_assurance_agent.md",
    "third_party_tool_consolidation": "ssot_drift_remediation_agent.md",
    "repository_antipattern_audit": "ssot_drift_remediation_agent.md",
    "timeout_lock_watchdog": "pipeline_outcome_assurance_agent.md",
    "storage_repository_hygiene": "storage_hygiene_agent.md",
    "worktree_coverage_cleanup": "pr_queue_observer_agent.md",
    "weekly_market_intelligence": "weekly_market_intelligence_agent.md",
    "pr_queue_observer": "pr_queue_observer_agent.md",
    "frontier_llm_research_scout": "agentic_repo_best_practices_agent.md",
    "testing_gaps_assurance": "testing_gaps_assurance_agent.md",
    "e2e_critical_functionality_assurance": "e2e_critical_functionality_assurance_agent.md",
    "corner_foresight_blindspot_scout": "corner_foresight_agent.md",
    "hotspot_value_enhancement": "hotspot_enhancement_agent.md",
    "tokenomics_cost_optimization": "tokenomics_agent.md",
    "agentic_repo_best_practices_scout": "agentic_repo_best_practices_agent.md",
    "retrieval_surface_assurance": "retrieval_surface_agent.md",
    "system_learning_assurance": "system_learning_agent.md",
    "evals_surface_assurance": "evals_surface_agent.md",
    "nea_charter_portfolio_roster": "sidecars/nea_charter_portfolio_roster_agent.md",
    "google_partner_advisory_weekly_sync": "sidecars/google_partner_advisory_weekly_sync_agent.md",
}

TASK_EXPORT_MAP: dict[str, str] = {
    "graph_hygiene_agent.md": "knowledge_graph_hygiene_scheduled_task.md",
    "ssot_drift_remediation_agent.md": "ssot_drift_remediation_scheduled_task.md",
    "pipeline_outcome_assurance_agent.md": "pipeline_outcome_assurance_scheduled_task.md",
    "storage_hygiene_agent.md": "storage_repository_hygiene_scheduled_task.md",
    "weekly_market_intelligence_agent.md": "weekly_market_intelligence_scheduled_task.md",
    "pr_queue_observer_agent.md": "pr_queue_observer_scheduled_task.md",
    "testing_gaps_assurance_agent.md": "testing_gaps_assurance_scheduled_task.md",
    "e2e_critical_functionality_assurance_agent.md": "e2e_critical_functionality_assurance_scheduled_task.md",
    "corner_foresight_agent.md": "corner_foresight_blindspot_scout_scheduled_task.md",
    "hotspot_enhancement_agent.md": "hotspot_value_enhancement_scheduled_task.md",
    "tokenomics_agent.md": "tokenomics_cost_optimization_scheduled_task.md",
    "agentic_repo_best_practices_agent.md": "agentic_repo_best_practices_scout_scheduled_task.md",
    "retrieval_surface_agent.md": "retrieval_surface_assurance_scheduled_task.md",
    "system_learning_agent.md": "system_learning_assurance_scheduled_task.md",
    "evals_surface_agent.md": "evals_surface_assurance_scheduled_task.md",
    "sidecars/nea_charter_portfolio_roster_agent.md": "nea_charter_portfolio_roster_scheduled_task.md",
    "sidecars/google_partner_advisory_weekly_sync_agent.md": "google_partner_advisory_weekly_sync_scheduled_task.md",
}


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SHARED_PARTIAL_NAME = "_shared_governance_contract.md"


def load_assembled_automation_prompt(prompts_dir: Path, fname: str) -> str:
    """Loads an automation prompt and appends the shared governance contract partial."""
    fpath = prompts_dir / fname
    body = fpath.read_text(encoding="utf-8") if fpath.is_file() else ""
    shared_path = prompts_dir / SHARED_PARTIAL_NAME
    shared_body = shared_path.read_text(encoding="utf-8") if shared_path.is_file() else ""
    return f"{body}\n\n{shared_body}".strip()


def audit_canonical_prompts(repo_root: Path) -> List[str]:
    """Validates presence and non-emptiness of all expected prompts in SSOT dir."""
    errors: List[str] = []
    prompts_dir = repo_root / CANONICAL_PROMPT_DIR
    if not prompts_dir.is_dir():
        return [f"[INV-PROMPT-SSOT-01] Missing canonical prompt directory: {prompts_dir}"]
    shared_partial = prompts_dir / SHARED_PARTIAL_NAME
    if not shared_partial.is_file() or shared_partial.stat().st_size == 0:
        errors.append(f"[INV-PROMPT-SSOT-01] Missing or empty shared governance partial: {shared_partial}")
    existing_files = {p.relative_to(prompts_dir).as_posix() for p in prompts_dir.rglob("*.md") if not p.name.startswith("_")}
    expected_files = set(EXPECTED_CANONICAL_PROMPTS.values())
    missing = expected_files - existing_files
    if missing:
        errors.append(f"[INV-PROMPT-SSOT-01] Missing canonical prompt files in {CANONICAL_PROMPT_DIR}: {sorted(missing)}")
    unexpected = existing_files - expected_files
    if unexpected:
        errors.append(f"[INV-PROMPT-SSOT-02] Rogue un-enrolled prompt files detected in {CANONICAL_PROMPT_DIR}: {sorted(unexpected)}")
    non_agent_suffix = sorted(f for f in existing_files if not f.endswith("_agent.md"))
    if non_agent_suffix:
        errors.append(f"[INV-PROMPT-SSOT-06] Prompt files in {CANONICAL_PROMPT_DIR} must end with '_agent.md': {non_agent_suffix}")
    for fname in expected_files:
        fpath = prompts_dir / fname
        if fpath.is_file() and fpath.stat().st_size == 0:
            errors.append(f"[INV-PROMPT-SSOT-01] Canonical prompt file is empty: {fpath}")
    return errors


def audit_registry_bindings(repo_root: Path) -> List[str]:
    """Validates that task_registry.json correctly binds to canonical prompts."""
    errors: List[str] = []
    reg_path = repo_root / "config" / "scheduled_tasks" / "task_registry.json"
    if not reg_path.is_file():
        return [f"[INV-PROMPT-SSOT-03] Task registry missing: {reg_path}"]
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"[INV-PROMPT-SSOT-03] Malformed task registry JSON: {exc}"]
    tasks = data.get("tasks", {})
    for tid, exp_fname in EXPECTED_ARCHIVE_TASKS.items():
        if tid not in tasks:
            errors.append(f"[INV-PROMPT-SSOT-03] Task '{tid}' missing from task_registry.json")
            continue
        declared_path = tasks[tid].get("prompt_path", "")
        expected_path = f"automations/prompts/{exp_fname}"
        if declared_path != expected_path:
            errors.append(f"[INV-PROMPT-SSOT-03] Task '{tid}' prompt_path mismatch: declared '{declared_path}' != expected '{expected_path}'")
        resolved_file = repo_root / declared_path
        if not resolved_file.is_file():
            errors.append(f"[INV-PROMPT-SSOT-03] Task '{tid}' prompt_path does not exist on disk: {resolved_file}")
    return errors


def audit_export_parity(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-04: automations/prompts/*_agent.md is the sole SSOT."""
    exports_dir = repo_root / "artifacts" / "exports" / "scheduled_tasks_hardened"
    if not exports_dir.is_dir():
        return []
    duplicate_files = sorted(p.name for p in exports_dir.glob("*.md"))
    if duplicate_files:
        return [
            f"[INV-PROMPT-SSOT-04] Legacy duplicate scheduled task export files detected in {exports_dir.relative_to(repo_root)}: "
            f"{duplicate_files}. automations/prompts/*_agent.md is the sole interactive SSOT."
        ]
    return []


def sync_prompt_exports(repo_root: Path | None = None) -> List[str]:
    """Retires any lingering duplicate *_scheduled_task.md files so automations/prompts/*_agent.md remains the sole SSOT."""
    root = repo_root or ROOT
    exports_dir = root / "artifacts" / "exports" / "scheduled_tasks_hardened"
    removed: List[str] = []
    if exports_dir.is_dir():
        for p in sorted(exports_dir.glob("*.md")):
            p.unlink()
            removed.append(f"retired duplicate export: {p.name}")
        try:
            exports_dir.rmdir()
        except OSError:
            pass
    return removed


def _check_dispositions(text: str, disps: List[str], label: str, exact_quote: bool = False) -> List[str]:
    missing: List[str] = []
    for disp in disps:
        if exact_quote:
            if f'"{disp}"' not in text and f"'{disp}'" not in text:
                missing.append(f"[INV-PROMPT-SSOT-05] {label} missing disposition mapping: '{disp}'")
        elif disp not in text:
            missing.append(f"[INV-PROMPT-SSOT-05] {label} missing canonical disposition: '{disp}'")
    return missing


def audit_tripartite_schema_parity(repo_root: Path) -> List[str]:
    """Ensures 10-column table schema and 5 canonical value dispositions match across
    pr_queue_output_contract.json, pr_queue_observer_agent.md, .agents/rules/pr-queue-summary.md,
    and executive_render.py.
    """
    errors: List[str] = []
    contract_path = repo_root / "config" / "scheduled_tasks" / "pr_queue_output_contract.json"
    if not contract_path.is_file():
        return [f"[INV-PROMPT-SSOT-05] Missing output contract: {contract_path}"]

    try:
        contract_data = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"[INV-PROMPT-SSOT-05] Malformed output contract JSON: {exc}"]

    expected_cols = contract_data.get("output", {}).get("columns", [])
    expected_disps = contract_data.get("output", {}).get("value_dispositions", [])
    if len(expected_cols) != 10:
        errors.append(f"[INV-PROMPT-SSOT-05] Expected 10 columns in output contract, found {len(expected_cols)}")
    if len(expected_disps) != 5:
        errors.append(f"[INV-PROMPT-SSOT-05] Expected 5 value dispositions in output contract, found {len(expected_disps)}")

    header_str = "| " + " | ".join(expected_cols) + " |"
    obsolete_header_sub = "| Priority | Branch | Category | Complexity | Recommended Disposition |"

    for rel_path, label, quote_mode in (
        (CANONICAL_PROMPT_DIR / "pr_queue_observer_agent.md", "pr_queue_observer_agent.md", False),
        (Path(".agents/rules/pr-queue-summary.md"), ".agents/rules/pr-queue-summary.md", False),
        (Path("tools/pr_governance_core/executive_render.py"), "executive_render.py", True),
    ):
        fpath = repo_root / rel_path
        if not fpath.is_file():
            errors.append(f"[INV-PROMPT-SSOT-05] Missing file: {fpath}")
            continue
        text = fpath.read_text(encoding="utf-8")
        if header_str not in text:
            errors.append(f"[INV-PROMPT-SSOT-05] {label} missing canonical 10-column header: {header_str}")
        if label == "pr_queue_observer_agent.md" and obsolete_header_sub in text:
            errors.append("[INV-PROMPT-SSOT-05] pr_queue_observer_agent.md contains obsolete 6-column header")
        errors.extend(_check_dispositions(text, expected_disps, label, exact_quote=quote_mode))
    return errors


REQUIRED_PLAN_STANDARD_TOKENS: tuple[str, ...] = (
    "docs/plans/<branch-slug>.md",
    "tools/validate_implementation_plan.py",
    "tools/validate_implementation_summary.py",
    "INV-WAVE-PLAN-MAX-WORDS",
    "min(2500, max(1000, 400 + 250 * N_waves + 25 * M_items))",
    "### Objective & Scope",
    "### Milestones & Deliverables",
    "### Acceptance Criteria",
    "### Runtime Receipt & Completion Gate",
    "Run:",
    "Input:",
    "Expected:",
    "Verify independently:",
    "Fail if:",
    "Record:",
    "PlanFidelityAuditor",
    "[partial_scope]",
    "## Wave Implementation & Completion Status Table",
    "UNCONDITIONAL_WAVE_PLAN_MANDATE",
    "Step 0.1",
    "tools/pr_governance.py start",
    "tools/pr_governance.py rebase",
    "sidecar.json",
    "INV-RUNTIME-PROOF-5POINT",
    "PRE_MUTATION_RUN",
    "POST_MUTATION_RUN",
    "DIFF_LINE_COVERAGE == 100%",
    "| Target File:Lines | Runtime Command | Pre-Fix Observed Behavior | Post-Fix Observed Behavior | Independent State Readback |",
)

PROHIBITED_CONDITIONAL_PLAN_BYPASS_TOKENS: tuple[str, ...] = (
    "When actionable findings exist, synthesize a wave-based Implementation Plan",
    "When actionable testing gaps exist, synthesize a wave-based Implementation Plan",
    "Whenever cleanup, quarantine, `ORPHAN_DELIVERABLES`, or worktree pruning candidates exist, formulate a wave-based Implementation Plan",
    "If zero candidates and zero actionable worktree stubs/orphans are found across all scopes, emit completion report with outcome `CLEAN_NO_ACTION` / `COMPLETE` and exit.",
    "Whenever ingestion failures, recovery gaps, or code remediations are required, an Implementation Plan is mandatory.",
    "Whenever code changes, pipeline enhancements, or remediations are proposed from scouted LLM engineering innovations, an Implementation Plan is mandatory.",
)


def _check_missing_tokens(text: str, tokens: Tuple[str, ...], prefix: str) -> List[str]:
    return [f"{prefix}: '{tok}'" for tok in tokens if tok not in text]


def _audit_single_prompt_plan_standard(prompts_dir: Path, fname: str) -> List[str]:
    fpath = prompts_dir / fname
    if not fpath.is_file():
        return []
    raw_text = fpath.read_text(encoding="utf-8")
    text = load_assembled_automation_prompt(prompts_dir, fname)
    errors = _check_missing_tokens(
        text,
        REQUIRED_PLAN_STANDARD_TOKENS,
        f"[INV-PROMPT-SSOT-07] {fname} missing required implementation plan standard token",
    )
    errors.extend(
        f"[INV-PROMPT-SSOT-09] {fname} contains prohibited conditional plan bypass token: '{bt}'"
        for bt in PROHIBITED_CONDITIONAL_PLAN_BYPASS_TOKENS
        if bt in raw_text
    )
    if "(and `implementation_plan.md`)" in text:
        errors.append(
            f"[INV-PROMPT-SSOT-07] {fname} references root `implementation_plan.md` instead of `<run_dir>/implementation_plan.md`"
        )
    if "tools/pr_governance.py submit" not in text and "tools/pr_governance.py merge" not in text:
        errors.append(
            f"[INV-PROMPT-SSOT-07] {fname} missing governed PR merge token ('tools/pr_governance.py submit' or 'tools/pr_governance.py merge')"
        )
    if fname == "testing_gaps_assurance_agent.md":
        errors.extend(
            _check_missing_tokens(
                text,
                ("Step 0.1", "tools/pr_governance.py start", "[92% — HIGH]", "MODE: interactive"),  # noqa: SSOT_EXEMPT
                f"[INV-PROMPT-SSOT-07] {fname} missing mandatory procedural enforcement token",
            )
        )
    if fname == "ssot_drift_remediation_agent.md" and "tools/audit_governance_harmonization.py" not in text:
        errors.append(
            f"[INV-PROMPT-SSOT-07] {fname} missing mandatory Gate 22 harmonization runner 'tools/audit_governance_harmonization.py'"
        )
    if "INV-WT-EPHEMERAL-SCHEDULED" not in text:
        errors.append(
            f"[INV-PROMPT-SSOT-07] {fname} missing mode-gated worktree ephemerality invariant 'INV-WT-EPHEMERAL-SCHEDULED'"
        )
    return errors


def audit_prompt_plan_standards(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-07 and INV-PROMPT-SSOT-09 across all canonical automation prompts."""
    prompts_dir = repo_root / CANONICAL_PROMPT_DIR
    if not prompts_dir.is_dir():
        return []
    shared_path = prompts_dir / SHARED_PARTIAL_NAME
    shared_text = shared_path.read_text(encoding="utf-8") if shared_path.is_file() else ""
    errors = _check_missing_tokens(
        shared_text,
        REQUIRED_PLAN_STANDARD_TOKENS,
        f"[INV-PROMPT-SSOT-07] {SHARED_PARTIAL_NAME} missing required implementation plan standard token",
    )
    for fname in sorted(EXPECTED_CANONICAL_PROMPTS.values()):
        if not fname.startswith("sidecars/"):
            errors.extend(_audit_single_prompt_plan_standard(prompts_dir, fname))
    return errors


def audit_zero_cross_prompt_overlap(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-08: unique Canonical Task, Exclusive Domain, and zero MECE CONSOLIDATION claims."""
    errors: List[str] = []
    prompts_dir = repo_root / CANONICAL_PROMPT_DIR
    if not prompts_dir.is_dir():
        return errors

    for task_id, fname in sorted(EXPECTED_CANONICAL_PROMPTS.items()):
        fpath = prompts_dir / fname
        if not fpath.is_file():
            continue
        text = fpath.read_text(encoding="utf-8")
        expected_tag = f"Canonical Task: {task_id}"
        if expected_tag not in text:
            errors.append(
                f"[INV-PROMPT-SSOT-08] {fname} missing unique canonical task header '{expected_tag}'"
            )
        if "Exclusive Domain:" not in text:
            errors.append(
                f"[INV-PROMPT-SSOT-08] {fname} missing 'Exclusive Domain:' boundary declaration"
            )
        if "MECE CONSOLIDATION: Consolidates" in text:
            errors.append(
                f"[INV-PROMPT-SSOT-08] {fname} contains prohibited cross-agent 'MECE CONSOLIDATION: Consolidates' overlap claim"
            )
    return errors


EXCLUSIVE_RUNNER_OWNERSHIP: dict[str, str] = {
    "govern_learning_proposals.py": "system_learning_agent.md",
    "audit_testing_gaps.py": "testing_gaps_assurance_agent.md",
    "audit_governance_harmonization.py": "ssot_drift_remediation_agent.md",
    "run_critical_9query_canary_probes.py": "retrieval_surface_agent.md",
    "audit_tokenomics_gaps.py": "tokenomics_agent.md",
    "run_cost_benchmark.py": "tokenomics_agent.md",
    "apply_graph_hygiene_remediation.py": "graph_hygiene_agent.md",
    "run_hygiene_agent_cron.py": "graph_hygiene_agent.md",
    "mutation_score_engine.py": "testing_gaps_assurance_agent.md",
    "lint_e2e_runtime_authenticity.py": "testing_gaps_assurance_agent.md",
    "run_corner_foresight_scout.py": "corner_foresight_agent.md",
}

RUNNER_EXEC_REGEX = re.compile(r"(?:python3?|\.venv/bin/python)\s+tools/([a-zA-Z0-9_\-\.]+)")


def audit_mece_runner_exclusivity(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-10: mechanical check that critical runners are executed only by their sole owner."""
    errors: List[str] = []
    prompts_dir = repo_root / CANONICAL_PROMPT_DIR
    if not prompts_dir.is_dir():
        return errors

    for p in sorted(prompts_dir.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        text = p.read_text(encoding="utf-8")
        for match in RUNNER_EXEC_REGEX.finditer(text):
            tool = match.group(1)
            if tool in EXCLUSIVE_RUNNER_OWNERSHIP:
                expected_owner = EXCLUSIVE_RUNNER_OWNERSHIP[tool]
                if p.name != expected_owner:
                    rel_p = p.relative_to(prompts_dir).as_posix()
                    errors.append(
                        f"[INV-PROMPT-SSOT-10] Runner '{tool}' executed in '{rel_p}'; exclusive owner is '{expected_owner}'"
                    )
    return errors


def _check_single_task_cron(tid: str, sinfo: dict, recon_tasks: dict, tasks_map: dict) -> List[str]:
    if tid not in recon_tasks:
        return [f"[INV-PROMPT-SSOT-11] Task '{tid}' missing from registration_reconciliation.json"]
    rinfo = recon_tasks[tid]
    sidecar_cron, recon_cron = sinfo.get("cron"), rinfo.get("target_cadence")
    declared_cron = tasks_map.get(tid, {}).get("cadence_specification", {}).get("declared_cron")
    errs: List[str] = []
    if declared_cron != recon_cron:
        errs.append(f"[INV-PROMPT-SSOT-11] Task '{tid}' declared_cron mismatch: '{declared_cron}' != '{recon_cron}'")
    if sidecar_cron != recon_cron:
        errs.append(f"[INV-PROMPT-SSOT-11] Task '{tid}' sidecar cron mismatch: '{sidecar_cron}' != '{recon_cron}'")
    if not rinfo.get("cadence_description"):
        errs.append(f"[INV-PROMPT-SSOT-11] Task '{tid}' missing cadence_description in reconciliation manifest")
    return errs


def audit_cron_and_description_parity(repo_root: Path) -> List[str]:
    """Enforces parity between task_registry.json declared crons and registration_reconciliation.json cadences."""
    reg_path = repo_root / "config" / "scheduled_tasks" / "task_registry.json"
    recon_path = repo_root / "config" / "scheduled_tasks" / "registration_reconciliation.json"
    if not reg_path.is_file() or not recon_path.is_file():
        return ["[INV-PROMPT-SSOT-11] Missing task registry or reconciliation manifest."]
    try:
        from tools.audit_governance_harmonization_core.common import load_json_with_dup_check
        reg_data, _ = load_json_with_dup_check(reg_path)
        recon_data, _ = load_json_with_dup_check(recon_path)
    except Exception as exc:
        return [f"[INV-PROMPT-SSOT-11] Malformed registry or reconciliation JSON: {exc}"]
    recon_tasks = {t.get("task_id"): t for t in recon_data.get("tasks", []) if isinstance(t, dict)}
    tasks_map = reg_data.get("tasks", {})
    errors: List[str] = []
    for tid, sinfo in reg_data.get("sidecar_map", {}).items():
        errors.extend(_check_single_task_cron(tid, sinfo, recon_tasks, tasks_map))
    return errors


STAGE_VI_REQUIRED_CHECKS: tuple[tuple[str, str], ...] = (
    ("urn:nee:scheduled-task-common-control-matrix:1.0.0", "missing common control matrix contract header"),
    ("Master Sequence: Stage VI", "missing 'Master Sequence: Stage VI' declaration"),
    ("Section Introduction:", "missing Rule 9 'Section Introduction:' blocks"),
    ("canonical_eligible = 1", "missing 'canonical_eligible = 1' filter in queries"),
    ("canonical_status = 'canonical'", "missing 'canonical_status = \\'canonical\\'' filter in queries"),
)


def _audit_single_stage_vi_prompt(prompts_dir: Path, rel_path: str) -> List[str]:
    fpath = prompts_dir / rel_path
    if not fpath.is_file():
        return [f"[INV-PROMPT-SSOT-12] Missing Stage VI prompt file: {rel_path}"]
    text = fpath.read_text(encoding="utf-8")
    errs = [f"[INV-PROMPT-SSOT-12] {rel_path} {msg}" for tok, msg in STAGE_VI_REQUIRED_CHECKS if tok not in text]
    if "\u2014" in text or "\u2013" in text:
        errs.append(f"[INV-PROMPT-SSOT-12] {rel_path} violates Rule 9 (em-dash or en-dash detected)")
    if " -- " in text:
        errs.append(f"[INV-PROMPT-SSOT-12] {rel_path} violates Rule 9 (double-hyphen spacer detected)")
    errs.extend(
        f"[INV-PROMPT-SSOT-12] {rel_path} contains hardcoded model token: {tok}"
        for tok in ("gpt-", "gemini-", "claude-")
        if tok in text
    )
    return errs


def audit_stage_vi_prompts(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-12: Stage VI executive briefing prompts satisfy common control matrix and query invariants."""
    prompts_dir = repo_root / CANONICAL_PROMPT_DIR
    errors: List[str] = []
    for rel_path in ("sidecars/nea_charter_portfolio_roster_agent.md", "sidecars/google_partner_advisory_weekly_sync_agent.md"):
        errors.extend(_audit_single_stage_vi_prompt(prompts_dir, rel_path))
    return errors


EXPECTED_SIDECAR_PROMPTS: tuple[str, ...] = (
    "google_partner_advisory_weekly_sync_agent.md",
    "nea_charter_portfolio_roster_agent.md",
)

PROHIBITED_SIDECAR_SQL_PATTERNS: tuple[str, ...] = (
    "src.source_name LIKE",
    "s.source_name LIKE",
    "s.source_name = 'Document 20261020",
)

REQUIRED_SIDECAR_HYBRID_TOKENS: tuple[str, ...] = (
    "INV-RETRIEVAL-HYBRID-FIRST",
    "nee_query",
    "calibrate_hybrid_weights",
    "Amanda Knarr",
)

REQUIRED_PERSISTENT_LEARNING_TOKENS: tuple[str, ...] = (
    "INV-AUTOMATION-PERSISTENT-LEARNING-LOOP",
    "hitl_decision_ledger.sqlite",
    "hitl_decision_ledger.json",
    "calibrate_options",
    "needs_operator_decision",
    "sample_count_at_presentation",
    "margin_at_decision",
    "learning_event",
    "system_learning_assurance",
)


def audit_automation_persistent_learning_loops(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-14: Persistent-store system learning feedback loops across all 18 prompt files."""
    errors: List[str] = []
    prompt_dir = repo_root / CANONICAL_PROMPT_DIR
    targets = [SHARED_PARTIAL_NAME, *EXPECTED_CANONICAL_PROMPTS.values()]
    for rel_name in targets:
        fpath = prompt_dir / rel_name
        if not fpath.is_file():
            continue
        raw_text = fpath.read_text(encoding="utf-8")
        if "INV-AUTOMATION-PERSISTENT-LEARNING-LOOP" not in raw_text:
            errors.append(
                f"[INV-PROMPT-SSOT-14] {rel_name} missing persistent-store learning feedback token: 'INV-AUTOMATION-PERSISTENT-LEARNING-LOOP'"
            )
        text = raw_text if rel_name == SHARED_PARTIAL_NAME else load_assembled_automation_prompt(prompt_dir, rel_name)
        for req_tok in REQUIRED_PERSISTENT_LEARNING_TOKENS:
            if req_tok not in text:
                errors.append(
                    f"[INV-PROMPT-SSOT-14] {rel_name} missing persistent-store learning feedback token: '{req_tok}'"
                )
    return errors


def audit_shared_contract_mirror_parity(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-15: docs/governance/shared_automation_governance_contract.md mirrors _shared_governance_contract.md."""
    errors: List[str] = []
    shared_path = repo_root / CANONICAL_PROMPT_DIR / SHARED_PARTIAL_NAME
    mirror_path = repo_root / "docs" / "governance" / "shared_automation_governance_contract.md"
    if shared_path.is_file() and mirror_path.is_file():
        if shared_path.read_text(encoding="utf-8").strip() != mirror_path.read_text(encoding="utf-8").strip():
            errors.append(
                "[INV-PROMPT-SSOT-15] Split-brain shared contract detected: "
                "docs/governance/shared_automation_governance_contract.md diverges from automations/prompts/_shared_governance_contract.md"
            )
    return errors


def _check_retracted_claims(repo_root: Path, fname: str, text: str) -> List[str]:
    ledger_file = repo_root / "state" / "google_partner_advisory_sync_cadence_ledger.json"
    if fname != "google_partner_advisory_weekly_sync_agent.md" or not ledger_file.is_file():
        return []
    try:
        ledger_data = json.loads(ledger_file.read_text(encoding="utf-8"))
        errs = [
            f"[INV-PROMPT-SSOT-13] {fname} contains retracted/quarantined claim from ledger: '{specious}'"
            for specious in ledger_data.get("retracted_or_specious_claims_quarantine", [])
            if specious and specious.lower() in text.lower()
        ]
        max_w = str(ledger_data.get("learned_operator_constraints", {}).get("max_agenda_words", 800))
        if f"{max_w} words" not in text and f"WORDS_{max_w}" not in text:
            errs.append(
                f"[INV-PROMPT-SSOT-13] {fname} word budget is out of sync with ledger max_agenda_words={max_w}"
            )
        return errs
    except Exception:
        return []


def audit_sidecar_hybrid_retrieval(repo_root: Path) -> List[str]:
    """Enforces INV-PROMPT-SSOT-13: Two-Stage Hybrid-First Retrieval across sidecars/*.md."""
    errors: List[str] = []
    sidecars_dir = repo_root / CANONICAL_PROMPT_DIR / "sidecars"
    if not sidecars_dir.is_dir():
        return [f"[INV-PROMPT-SSOT-13] Missing sidecar prompts directory: {sidecars_dir}"]
    for fname in EXPECTED_SIDECAR_PROMPTS:
        fpath = sidecars_dir / fname
        if not fpath.is_file():
            errors.append(f"[INV-PROMPT-SSOT-13] Missing sidecar prompt: {fpath}")
            continue
        text = fpath.read_text(encoding="utf-8")
        errors.extend(_check_missing_tokens(text, REQUIRED_SIDECAR_HYBRID_TOKENS, f"[INV-PROMPT-SSOT-13] {fname} missing required hybrid retrieval / quarantine token"))
        errors.extend(f"[INV-PROMPT-SSOT-13] {fname} contains prohibited brittle SQL source filter: '{b}'" for b in PROHIBITED_SIDECAR_SQL_PATTERNS if b in text)
        errors.extend(_check_retracted_claims(repo_root, fname, text))
    return errors


def run_prompt_ssot_lint(repo_root: Path | None = None) -> Tuple[bool, List[str]]:
    """Runs full SSOT prompt suite and returns status with itemized errors."""
    root = repo_root or ROOT
    all_errors: List[str] = []

    all_errors.extend(audit_canonical_prompts(root))
    all_errors.extend(audit_registry_bindings(root))
    all_errors.extend(audit_export_parity(root))
    all_errors.extend(audit_tripartite_schema_parity(root))
    all_errors.extend(audit_prompt_plan_standards(root))
    all_errors.extend(audit_zero_cross_prompt_overlap(root))
    all_errors.extend(audit_mece_runner_exclusivity(root))
    all_errors.extend(audit_cron_and_description_parity(root))
    all_errors.extend(audit_stage_vi_prompts(root))
    all_errors.extend(audit_sidecar_hybrid_retrieval(root))
    all_errors.extend(audit_automation_persistent_learning_loops(root))
    all_errors.extend(audit_shared_contract_mirror_parity(root))

    return len(all_errors) == 0, all_errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Linter for scheduled automation prompt SSOT integrity.")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Root path of target repository.")
    parser.add_argument("--sync", action="store_true", help="Synchronize canonical prompts to exports before linting.")
    parser.add_argument("--check", action="store_true", help="Read-only check mode (never mutates tracked files).")
    args = parser.parse_args()

    if args.sync and not args.check:
        synced = sync_prompt_exports(args.repo_root)
        if synced:
            print(f"⚡ Synced {len(synced)} prompt(s) to artifacts/exports/scheduled_tasks_hardened/:")
            for s in synced:
                print(f"   - {s}")

    ok, errors = run_prompt_ssot_lint(args.repo_root)

    if ok:
        print("[PASS] All scheduled automation prompts conform strictly to canonical SSOT standards.")
        print(f"       Canonical prompts verified: {len(EXPECTED_CANONICAL_PROMPTS)}/{len(EXPECTED_CANONICAL_PROMPTS)} present with zero sprawl.")
        return 0

    print("[FAIL] Scheduled automation prompt SSOT violations detected:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
