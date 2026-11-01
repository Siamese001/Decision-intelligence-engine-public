"""tools/scheduled_task_core/launch_adapters.py

Host launch adapters establishing trusted execution contexts, dispatching
real domain workflows, and preventing empty-callback qualification bypass.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import (
    TaskExecutionResult,
    run_agentic_repo_best_practices_scout,
    run_corner_foresight_blindspot_scout,
    run_cost_quality_review,
    run_e2e_critical_functionality_assurance,
    run_evals_surface_assurance,
    run_frontier_llm_research_scout,
    run_google_partner_advisory_weekly_sync,
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
from .authority import ApprovalRecord, AuthorityContext
from .contracts import (
    GOVERNED_TASK_IDS,
    ExecutionMode,
    TriggerOrigin,
    get_task_definition,
    resolve_trusted_launch_context,
)

logger = logging.getLogger(__name__)


@dataclass
class WorktreeIsolationContext:
    """Encapsulates worktree execution isolation, ensuring tasks run off worktree branches."""
    repo_root: Path
    worktree_path: Path
    is_isolated: bool
    branch_name: str | None = None

    @classmethod
    def resolve(cls, repo_root: Path | None = None) -> WorktreeIsolationContext:
        root = repo_root or Path(__file__).resolve().parents[2]
        resolved = root.resolve()
        parts = resolved.parts
        if ".worktrees" in parts:
            idx = parts.index(".worktrees")
            primary_root = Path(*parts[:idx])
            branch = parts[idx + 1] if len(parts) > idx + 1 else None
            return cls(
                repo_root=primary_root,
                worktree_path=resolved,
                is_isolated=True,
                branch_name=branch,
            )
        return cls(
            repo_root=resolved,
            worktree_path=resolved,
            is_isolated=False,
            branch_name="main",
        )


class EmptyCallbackDefenseError(ValueError):
    """Raised when an empty or no-op callback is supplied for domain qualification."""


class AntigravityHostLaunchAdapter:
    """Canonical host launcher for Antigravity dual-mode tasks."""

    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.worktree_context = WorktreeIsolationContext.resolve(self.repo_root)

    def verify_worktree_isolation(self, require_isolated: bool = False) -> bool:
        """Verifies that execution occurs in an isolated worktree branch, not root main."""
        wt = WorktreeIsolationContext.resolve(self.repo_root)
        self.worktree_context = wt
        if require_isolated and not wt.is_isolated:
            raise PermissionError(
                f"Execution must run off an isolated worktree branch from local main, not repository root ({self.repo_root})."
            )
        return wt.is_isolated

    def build_authority_context(
        self,
        task_id: str,
        run_id: str,
        mode: str | ExecutionMode,
        trigger_origin: str | TriggerOrigin | None = None,
        actor: str | None = None,
        allowed_capabilities: set[str] | None = None,
        prompt_content: str | None = None,
    ) -> AuthorityContext:
        """Establishes immutable AuthorityContext enforcing launch mode and origins."""
        if task_id not in GOVERNED_TASK_IDS:
            raise ValueError(f"Task '{task_id}' is not one of governed tasks: {GOVERNED_TASK_IDS}")

        # Default trigger origin based on mode if unspecified
        if trigger_origin is None:
            if isinstance(mode, ExecutionMode):
                mode_val = mode.value
            else:
                mode_val = str(mode)
            trigger_origin = (
                TriggerOrigin.SCHEDULER_CRON
                if mode_val == "scheduled"
                else TriggerOrigin.MANUAL_OPERATOR
            )

        origin, resolved_mode = resolve_trusted_launch_context(
            trigger_origin=trigger_origin,
            requested_mode=mode,
            prompt_content=prompt_content,
            strict_spoof_detection=True,
        )

        caps = allowed_capabilities or {
            "audit_execution",
            "graph_repair",
            "storage_quarantine",
            "market_intelligence_ingest",
            "prune_worktrees",
            "remediation_planning",
            "ssot_remediation",
            "cost_quality_audit",
            "pipeline_assurance_audit",
        }

        default_actor = "scheduler" if resolved_mode == ExecutionMode.SCHEDULED else "operator"

        return AuthorityContext(
            task_id=task_id,
            run_id=run_id,
            trigger_origin=origin,
            execution_mode=resolved_mode,
            actor=actor or default_actor,
            allowed_capabilities=caps,
            is_scheduled=(resolved_mode == ExecutionMode.SCHEDULED),
        )

    def launch_task(
        self,
        task_id: str,
        mode: str | ExecutionMode,
        fixture_root: Path,
        run_id: str | None = None,
        trigger_origin: str | TriggerOrigin | None = None,
        actor: str | None = None,
        approval_record: ApprovalRecord | dict[str, Any] | None = None,
        decision: str | None = None,
        confirm_token: str | None = None,
        test_fails: bool = False,
        reject_empty_callback: bool = True,
    ) -> TaskExecutionResult:
        """Dispatches to the real domain adapter and verifies execution proof."""
        eff_run_id = run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        run_dir = fixture_root / "reports" / task_id / eff_run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        context = self.build_authority_context(
            task_id=task_id,
            run_id=eff_run_id,
            mode=mode,
            trigger_origin=trigger_origin,
            actor=actor,
        )

        # Dispatch to domain adapter
        if task_id == "knowledge_graph_hygiene":
            result = run_knowledge_graph_hygiene(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "storage_repository_hygiene":
            result = run_storage_repository_hygiene(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "weekly_market_intelligence":
            result = run_weekly_market_intelligence(
                context, fixture_root, run_dir, approval=approval_record, decision=decision
            )
        elif task_id == "worktree_coverage_cleanup":
            result = run_worktree_coverage_cleanup(
                context, fixture_root, run_dir, confirm_token=confirm_token, approval=approval_record
            )
        elif task_id == "repository_antipattern_audit":
            result = run_repository_antipattern_audit(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "ssot_drift_remediation":
            result = run_ssot_drift_remediation(
                context, fixture_root, run_dir, approval=approval_record, test_fails=test_fails
            )
        elif task_id == "pr_queue_observer":
            result = run_pr_queue_observer(context, fixture_root, run_dir)
        elif task_id == "cost_quality_review":
            result = run_cost_quality_review(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "pipeline_outcome_assurance":
            result = run_pipeline_outcome_assurance(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "technical_debt_elimination":
            result = run_technical_debt_elimination(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "third_party_tool_consolidation":
            result = run_third_party_tool_consolidation(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "timeout_lock_watchdog":
            result = run_timeout_lock_watchdog(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "frontier_llm_research_scout":
            result = run_frontier_llm_research_scout(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "testing_gaps_assurance":
            result = run_testing_gaps_assurance(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "e2e_critical_functionality_assurance":
            result = run_e2e_critical_functionality_assurance(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "corner_foresight_blindspot_scout":
            result = run_corner_foresight_blindspot_scout(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "hotspot_value_enhancement":
            result = run_hotspot_value_enhancement(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "tokenomics_cost_optimization":
            result = run_tokenomics_cost_optimization(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "agentic_repo_best_practices_scout":
            result = run_agentic_repo_best_practices_scout(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "retrieval_surface_assurance":
            result = run_retrieval_surface_assurance(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "system_learning_assurance":
            result = run_system_learning_assurance(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "evals_surface_assurance":
            result = run_evals_surface_assurance(context, fixture_root, run_dir, approval=approval_record)
        elif task_id == "google_partner_advisory_weekly_sync":
            result = run_google_partner_advisory_weekly_sync(context, fixture_root, run_dir, approval=approval_record)
        else:
            raise ValueError(f"Unknown domain task: {task_id}")

        # Empty callback defense: fail closed if no domain activity occurred
        if reject_empty_callback and result.findings_count == 0 and result.mutations_count == 0:
            if result.terminal_outcome not in ("NO_NOTIFICATION", "OBSERVATION_EXPLAINED"):
                raise EmptyCallbackDefenseError(
                    f"Task '{task_id}' returned 0 findings and 0 mutations under outcome '{result.terminal_outcome}'. "
                    "Empty callbacks cannot qualify task execution."
                )

        return result
