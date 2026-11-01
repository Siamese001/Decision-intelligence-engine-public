"""tools/scheduled_task_core/remediation_planner.py

Deterministic remediation plan generator and validator for scheduled tasks.
Ensures every scheduled or interactive task discovering actionable findings or gaps
generates a wave-based implementation plan complying with repository governance.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from tools.validate_implementation_plan import validate_plan_content


_TASK_DOMAIN_TITLES: dict[str, str] = {
    "cost_quality_review": "Cost, Latency & Quality Optimization",
    "pipeline_outcome_assurance": "Pipeline Outcome & Retrieval Assurance",
    "repository_antipattern_audit": "Repository Architecture & Anti-Pattern Remediation",
    "ssot_drift_remediation": "SSOT & Configuration Drift Remediation",
    "storage_repository_hygiene": "Storage & Clutter Hygiene Quarantine",
    "worktree_coverage_cleanup": "Worktree & Deliverable Coverage Hygiene",
    "knowledge_graph_hygiene": "Knowledge Graph Structural & Evidence Hygiene",
    "weekly_market_intelligence": "Weekly Market Intelligence Signal Promotion",
    "pr_queue_observer": "PR Queue & Branch Collision Remediation",
    "technical_debt_elimination": "Technical Debt Elimination",
    "timeout_lock_watchdog": "Runtime Timeout, Hung Process & Lock Watchdog Remediation",
    "third_party_tool_consolidation": "Local Third-Party Tool & Custom Code Consolidation",
    "testing_gaps_assurance": "Testing Gaps, Runtime Authenticity & Hotspot Assurance",
    "frontier_llm_research_scout": "Frontier LLM Research & Engineering Innovations",
    "e2e_critical_functionality_assurance": "End-to-End Critical Functionality & Stage Handoff Assurance",
    "corner_foresight_blindspot_scout": "Look-Around-the-Corner Foresight & Blind-Spot Radar",
    "hotspot_value_enhancement": "High-Value Pipeline & Repository Hotspot Enhancement",
    "tokenomics_cost_optimization": "Tokenomics & 8-Pillar Cost Optimization",
    "agentic_repo_best_practices_scout": "Weekly Frontier Lab Agentic Repository Best Practices Curation",
    "retrieval_surface_assurance": "Exhaustive 7-Surface Retrieval Gap Remediation & High-ROI Enhancement",
    "system_learning_assurance": "Exhaustive 8-Surface Closed-Loop System Learning Gap Remediation & High-ROI Enhancement",
    "evals_surface_assurance": "Exhaustive 7-Surface Evaluation Construct-Validity & Anti-Tautology Remediation",
}


def _extract_target_deliverables(task_id: str, findings: list[dict[str, Any]], branch_slug: str) -> str:
    """Extracts concrete backticked file targets from findings for PlanFidelityAuditor compliance."""
    extracted_paths: list[str] = []
    for f in findings:
        for key in ("file", "target", "path", "module"):
            val = f.get(key)
            if isinstance(val, str) and val.strip() and ("/" in val or "." in val):
                cleaned = val.strip()
                if cleaned not in extracted_paths:
                    extracted_paths.append(cleaned)
    lines: list[str] = []
    if extracted_paths:
        for p in extracted_paths[:5]:
            lines.append(
                f"- `[MODIFY]` `{p}`: Remediate identified governance, schema, or structural drift within isolated worktree scope."
            )
    else:
        lines.append(
            f"- `[MODIFY]` `docs/plans/{branch_slug}.md`: Record verified wave remediation scope, deliverable lineage, and completion status."
        )
    lines.append(
        f"- `[NEW]` `tests/test_{task_id}_remediation.py`: Add deterministic regression and boundary qualification tests covering all discovered findings."
    )
    lines.append(
        f"- `[NEW]` `reports/wave1_{task_id}_remediation_receipt.json`: Emit cryptographically verifiable Wave 1 execution receipt with pass counts and duration telemetry."
    )
    return "\n".join(lines)


def _format_finding_summary(findings: list[dict[str, Any]]) -> str:
    """Formats an itemized summary of up to 5 findings for plan context."""
    if not findings:
        return (
            "1. **ZERO_DEFECT_BASELINE_VERIFIED**: Read-only discovery scan across all governed surfaces "
            "detected 0 actionable findings or boundary violations (`+0` projected delta); executing "
            "mandatory 2-wave Baseline Discovery & Zero-Delta Verification Plan (`UNCONDITIONAL_WAVE_PLAN_MANDATE`)."
        )
    items = []
    for idx, f in enumerate(findings[:5], start=1):
        issue = f.get("issue") or f.get("finding_id") or f.get("status") or "UNSPECIFIED_ISSUE"
        desc = f.get("detail") or f.get("description") or f.get("reason") or f.get("target") or f.get("path") or str(f)
        items.append(f"{idx}. **{issue}**: {desc}")
    if len(findings) > 5:
        items.append(f"... and {len(findings) - 5} additional finding(s).")
    return "\n".join(items)


def build_remediation_plan_content(
    task_id: str,
    run_id: str,
    findings: list[dict[str, Any]],
    branch_slug: str | None = None,
) -> str:
    """Builds a complete, wave-based remediation plan string meeting all strict validation gates."""
    title = _TASK_DOMAIN_TITLES.get(task_id, task_id.replace("_", " ").title())
    finding_summary = _format_finding_summary(findings)
    finding_count = len(findings)
    effective_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", branch_slug) if branch_slug else f"feat_{task_id}_remediation"
    review_branch = branch_slug if branch_slug else f"feat/{task_id}_remediation"
    deliverable_lines = _extract_target_deliverables(task_id, findings, effective_slug)

    return f"""# Remediation Plan for {title}

## Forensic Findings Summary
Scheduled audit run `{run_id}` for `{task_id}` identified {finding_count} actionable finding(s) or governance gap(s) requiring structured remediation:

{finding_summary}

All remediation work executes strictly within an isolated feature worktree (`docs/plans/{effective_slug}.md`), enforcing two-stage human-in-the-loop approval, zero direct commits to `main`, and zero live database mutations prior to governed pull request merge. Word budget adheres to `INV-WAVE-PLAN-MAX-WORDS` (`500–800` word base target, scaling up to `min(2500, max(1000, 400 + 250 * N_waves + 25 * M_items))` words with HITL decision tables exempt).

---

## Wave 1: Targeted Domain Remediation for {task_id}

### Objective & Scope
Isolate and remediate all {finding_count} discovered finding(s) from run `{run_id}` within a dedicated feature worktree while maintaining strict backwards compatibility, unidirectional layer boundaries (`L0`–`L6`), and zero live knowledge graph or production directory mutations prior to pull request merge. Scope covers targeted source module adjustments, deterministic regression test authoring, and empirical verification against reproduction fixtures.

### Milestones & Deliverables
{deliverable_lines}

### Acceptance Criteria
- 100% of discovered finding(s) in run `{run_id}` are verified resolved by deterministic automated tests and domain re-scans.
- Zero regression in existing unit and adversarial test suites; per-test timeout ceiling is enforced at 60 seconds (`--timeout=60`).
- All modified Python modules satisfy the 400-line clean module budget, cognitive complexity ceiling (`<= 15`), and unidirectional `L0`–`L6` import invariants.
- Deferred deliverables in partial-wave approvals are marked with `[partial_scope]` to preserve `PlanFidelityAuditor` parity.

### Runtime Receipt & Completion Gate
- **Run**: `.venv/bin/python tools/run_tests_with_progress.py --timeout=60 tests/test_{task_id}_remediation.py`
- **Input**: Isolated hermetic test fixtures reproducing the {finding_count} discovered finding(s) from run `{run_id}`.
- **Expected**: 100% test pass count with zero unexpected failures, errors, or timeouts; domain audit re-scan reports zero remaining gaps.
- **Verify independently**: Re-execute the domain audit scanner against the isolated worktree and inspect `reports/wave1_{task_id}_remediation_receipt.json` to confirm finding count drops to zero.
- **Fail if**: Any regression test fails, the domain audit scanner detects unresolved gaps, module budgets are exceeded, or unapproved mutations occur.
- **Record**: `reports/wave1_{task_id}_remediation_receipt.json`

---

## Wave 2: Verification, Guardrail Enforcement & PR Readiness

### Objective & Scope
Execute regression and governance verification across the isolated remediation worktree, enforce credential isolation, file budget ceilings, and plan-to-diff fidelity (`PlanFidelityAuditor`), update the wave completion status table, and qualify the branch through `tools/pr_governance.py review`.

### Milestones & Deliverables
- `[MODIFY]` `docs/plans/{effective_slug}.md`: Update the `Wave Implementation & Completion Status Table` with empirical Wave 1 and Wave 2 verification evidence and receipt links.
- `[NEW]` `reports/wave2_{task_id}_readiness_receipt.json`: Generate pull request review readiness receipt validating all mainline merge prerequisites and plan fidelity checks.

### Acceptance Criteria
- Main readiness review and proportional PR governance gates pass with zero blocking violations (`terminal_outcome == "PASS"`).
- `tools/validate_implementation_plan.py --file docs/plans/{effective_slug}.md` and `tools/validate_implementation_summary.py --file docs/plans/{effective_slug}.md` both exit with code 0.
- Working tree remains clean with zero untracked files or uncommitted modifications; live canonical graph databases (`graph.sqlite`, `CANONICAL_GRAPH.db`) remain byte-identical prior to merge.

### Runtime Receipt & Completion Gate
- **Run**: `.venv/bin/python tools/validate_implementation_plan.py --file docs/plans/{effective_slug}.md && .venv/bin/python tools/validate_implementation_summary.py --file docs/plans/{effective_slug}.md && .venv/bin/python tools/pr_governance.py review {review_branch}`
- **Input**: Clean feature worktree containing verified Wave 1 remediations and updated `docs/plans/{effective_slug}.md`.
- **Expected**: Plan and summary validators exit 0; PR governance review emits terminal outcome `PASS` across all required readiness gates.
- **Verify independently**: Inspect `reports/wave2_{task_id}_readiness_receipt.json` and verify SHA-256 invariance of live database files prior to merge authorization.
- **Fail if**: Any readiness gate fails, `PlanFidelityAuditor` detects undeclared file modifications, or live database digests change prior to merge.
- **Record**: `reports/wave2_{task_id}_readiness_receipt.json`

---

## Wave Implementation & Completion Status Table

| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- | :--- |
| **Wave 1** | Targeted Domain Remediation for `{task_id}` | `OPEN` | Pending execution of targeted fixes and `tests/test_{task_id}_remediation.py` in isolated worktree. | `.venv/bin/python tools/run_tests_with_progress.py --timeout=60 tests/test_{task_id}_remediation.py` |
| **Wave 2** | Verification, Guardrail Enforcement & PR Readiness | `OPEN` | Pending PR readiness qualification and status table verification in `docs/plans/{effective_slug}.md`. | `.venv/bin/python tools/pr_governance.py review {review_branch}` |
"""


def generate_and_validate_remediation_plan(
    task_id: str,
    run_id: str,
    findings: list[dict[str, Any]],
    run_dir: Path,
    repo_root: Path | None = None,
    branch_slug: str | None = None,
) -> tuple[Path, str, str]:
    """Generates, validates, and writes a wave-based remediation plan.

    Returns:
        (plan_path, plan_sha256, plan_content)
    Raises:
        RuntimeError if the generated plan fails strict wave governance validation.
    """
    content = build_remediation_plan_content(task_id, run_id, findings, branch_slug=branch_slug)
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "implementation_plan.md"

    # Validate against strict repository governance rules
    is_valid, errors = validate_plan_content(content, filename=plan_path.name, strict=True)
    if not is_valid:
        error_msg = f"Generated remediation plan for '{task_id}' failed validation: {'; '.join(errors)}"
        raise RuntimeError(error_msg)

    # Write plan to run directory
    plan_path.write_text(content, encoding="utf-8")
    plan_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # If repo_root and branch_slug provided, also persist to docs/plans/<branch_slug>.md
    if repo_root and branch_slug:
        docs_plans_dir = repo_root / "docs" / "plans"
        docs_plans_dir.mkdir(parents=True, exist_ok=True)
        slug_clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", branch_slug)
        repo_plan_file = docs_plans_dir / f"{slug_clean}.md"
        repo_plan_file.write_text(content, encoding="utf-8")

    return plan_path, plan_sha256, content


def attach_plan_if_findings(
    task_id: str,
    run_id: str,
    findings: list[dict[str, Any]],
    run_dir: Path,
    repo_root: Path | None = None,
    branch_slug: str | None = None,
) -> tuple[Path, str]:
    """Unconditionally generates, validates, and returns (plan_path, plan_sha256) on every run (`UNCONDITIONAL_WAVE_PLAN_MANDATE`), including 0-finding runs."""
    plan_path, plan_sha256, _ = generate_and_validate_remediation_plan(
        task_id, run_id, findings, run_dir, repo_root=repo_root, branch_slug=branch_slug
    )
    return plan_path, plan_sha256


attach_unconditional_wave_plan = attach_plan_if_findings

