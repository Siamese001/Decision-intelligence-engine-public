"""tools.scheduled_task_core.contracts

Implements Track 1A shared contracts, registry lookup, schema validation,
and trusted launch context resolution for scheduled tasks.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)

CONTRACT_ID = "urn:nee:scheduled-task-run:1.0.0"
SCHEMA_VERSION = "1.0.0"

SIX_CANONICAL_TASK_IDS: tuple[str, ...] = (
    "knowledge_graph_hygiene",
    "ssot_drift_remediation",
    "pr_queue_observer",
    "storage_repository_hygiene",
    "pipeline_outcome_assurance",
    "weekly_market_intelligence",
)

FIVE_MECE_TASK_IDS: tuple[str, ...] = SIX_CANONICAL_TASK_IDS

TWELVE_GOVERNED_TASK_IDS: tuple[str, ...] = (
    "knowledge_graph_hygiene",
    "pr_queue_observer",
    "repository_antipattern_audit",
    "ssot_drift_remediation",
    "storage_repository_hygiene",
    "weekly_market_intelligence",
    "worktree_coverage_cleanup",
    "cost_quality_review",
    "pipeline_outcome_assurance",
    "technical_debt_elimination",
    "timeout_lock_watchdog",
    "third_party_tool_consolidation",
    "frontier_llm_research_scout",
    "testing_gaps_assurance",
    "e2e_critical_functionality_assurance",
    "corner_foresight_blindspot_scout",
    "hotspot_value_enhancement",
    "tokenomics_cost_optimization",
    "agentic_repo_best_practices_scout",
    "retrieval_surface_assurance",
    "system_learning_assurance",
    "evals_surface_assurance",
    "nea_charter_portfolio_roster",
    "google_partner_advisory_weekly_sync",
)
TEN_GOVERNED_TASK_IDS: tuple[str, ...] = TWELVE_GOVERNED_TASK_IDS
NINE_GOVERNED_TASK_IDS: tuple[str, ...] = TWELVE_GOVERNED_TASK_IDS
GOVERNED_TASK_IDS: tuple[str, ...] = TWELVE_GOVERNED_TASK_IDS
SEVEN_ARCHIVE_TASK_IDS: tuple[str, ...] = TWELVE_GOVERNED_TASK_IDS


class TriggerOrigin(str, Enum):
    SCHEDULER_CRON = "scheduler_cron"
    MANUAL_OPERATOR = "manual_operator"
    WEBHOOK_DISPATCH = "webhook_dispatch"
    TEST_RUNNER = "test_runner"


class ExecutionMode(str, Enum):
    SCHEDULED = "scheduled"
    INTERACTIVE = "interactive"
    AUDIT_ONLY = "audit_only"
    DRY_RUN = "dry_run"
    ISOLATED_PATCH_PROPOSAL = "isolated_patch_proposal"
    AUTHORIZED_APPLY = "authorized_apply"


class RunOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NOT_RUN = "not_run"


class RemediationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PROPOSED = "proposed"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    PATCH_VERIFIED = "patch_verified"
    APPLIED_VERIFIED = "applied_verified"
    FAILED = "failed"


class NotificationStatus(str, Enum):
    QUIET = "quiet"
    REQUIRED = "required"
    DELIVERED = "delivered"
    FAILED = "failed"


class ScheduledTaskContractError(ValueError):
    """Base error for scheduled task contract violations."""


class SchemaValidationError(ScheduledTaskContractError):
    """Raised when a task run payload fails JSON Schema validation."""


class UnauthorizedExecutionModeError(ScheduledTaskContractError):
    """Raised when an unknown or unauthorized execution mode is encountered."""


class PromptModeSpoofingError(ScheduledTaskContractError):
    """Raised when prompt text or input payload attempts to override trusted launcher mode."""


class TaskNotRegisteredError(ScheduledTaskContractError):
    """Raised when an unknown task ID is requested from the registry."""


class UnresolvedRegistrationError(ScheduledTaskContractError):
    """Raised when attempting to deploy or claim active registration for an unresolved task."""


def _find_repo_root() -> Path:
    # Anchor to workspace root containing contracts/ and config/
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "config" / "scheduled_tasks" / "task_registry.json").is_file():
        return candidate
    parts = candidate.parts
    if ".worktrees" in parts:
        idx = parts.index(".worktrees")
        return Path(*parts[:idx])
    return candidate


def load_task_registry(registry_path: Path | None = None) -> dict[str, Any]:
    """Loads the canonical task registry mapping the governed tasks."""
    path = registry_path or (_find_repo_root() / "config" / "scheduled_tasks" / "task_registry.json")
    if not path.is_file():
        raise FileNotFoundError(f"Task registry not found at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks", {})
    return tasks


def get_task_definition(task_id: str, registry_path: Path | None = None) -> dict[str, Any]:
    """Retrieves definition for a specific task ID, verifying membership."""
    tasks = load_task_registry(registry_path=registry_path)
    if task_id not in tasks:
        raise TaskNotRegisteredError(
            f"Task '{task_id}' is not in the canonical registry. Valid tasks: {list(tasks.keys())}"
        )
    return tasks[task_id]


def validate_task_run_payload(
    payload: dict[str, Any],
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Validates a scheduled task run payload against contracts/scheduled-task-run.schema.json."""
    path = schema_path or (_find_repo_root() / "contracts" / "scheduled-task-run.schema.json")
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found at {path}")
    schema = json.loads(path.read_text(encoding="utf-8"))

    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as err:
        raise SchemaValidationError(f"Payload failed contract schema validation: {err.message}") from err

    # Additional semantic checks
    task_id = payload.get("task_id")
    if task_id not in GOVERNED_TASK_IDS:
        raise SchemaValidationError(f"Task ID '{task_id}' is not one of the governed tasks.")

    return payload


def resolve_trusted_launch_context(
    trigger_origin: str | TriggerOrigin,
    requested_mode: str | ExecutionMode | None = None,
    prompt_content: str | None = None,
    strict_spoof_detection: bool = False,
) -> tuple[TriggerOrigin, ExecutionMode]:
    """Resolves immutable trigger origin and execution mode from trusted launcher context.

    Enforces that prompt text, documents, or untrusted payload content CANNOT spoof or escalate
    the execution mode.
    """
    # 1. Resolve trigger origin
    try:
        origin = TriggerOrigin(trigger_origin)
    except ValueError as exc:
        raise ScheduledTaskContractError(f"Invalid trigger origin '{trigger_origin}'") from exc

    # 2. Resolve requested mode from trusted launcher context
    if requested_mode is None or requested_mode == "":
        resolved_mode = ExecutionMode.SCHEDULED
    else:
        try:
            resolved_mode = ExecutionMode(requested_mode)
        except ValueError as exc:
            raise UnauthorizedExecutionModeError(f"Unknown or unauthorized execution mode '{requested_mode}'") from exc

    # 3. Check for prompt-level mode spoofing attempts
    if prompt_content:
        # Canonical dual-mode specifications (governed by urn:nee:scheduled-task-common-control-matrix:1.0.0)
        # legitimately document both 'MODE: scheduled' and 'MODE: interactive' branches.
        # Spoof detection targets untrusted caller overrides (e.g. '<!-- MODE: interactive -->' or
        # prompts without the canonical governed contract header claiming 'MODE: interactive').
        lower_prompt = prompt_content.lower()
        is_governed_dual_mode_spec = (
            "governed common contract: urn:nee:scheduled-task-common-control-matrix:1.0.0" in lower_prompt
            and "mode: scheduled" in lower_prompt
        )
        has_spoof_comment_or_override = (
            "<!-- mode: interactive -->" in lower_prompt
            or "override_mode: interactive" in lower_prompt
        )
        if resolved_mode != ExecutionMode.INTERACTIVE and (
            has_spoof_comment_or_override
            or ("mode: interactive" in lower_prompt and not is_governed_dual_mode_spec)
        ):
            msg = "Prompt content contains 'MODE: interactive' attempting to override trusted launcher mode."
            logger.warning(msg)
            if strict_spoof_detection:
                raise PromptModeSpoofingError(msg)
            # Under non-strict, trusted launcher mode unconditionally prevails (spoofer ignored)

    # In scheduled trigger origin, interactive mutations are strictly prohibited
    if origin == TriggerOrigin.SCHEDULER_CRON and resolved_mode in (
        ExecutionMode.INTERACTIVE,
        ExecutionMode.AUTHORIZED_APPLY,
    ):
        raise UnauthorizedExecutionModeError(
            f"Trigger origin '{origin.value}' cannot execute in mutating mode '{resolved_mode.value}'."
        )

    return origin, resolved_mode


def assert_known_execution_mode(mode: str) -> None:
    """Denies mutation and fails closed if mode is unknown."""
    if not mode or not isinstance(mode, str):
        raise UnauthorizedExecutionModeError("Execution mode cannot be empty or non-string.")
    valid_modes = {m.value for m in ExecutionMode}
    if mode not in valid_modes:
        raise UnauthorizedExecutionModeError(
            f"Execution mode '{mode}' is unknown. Mutations denied under AGENTS.md invariant."
        )


def assert_registration_resolved(
    task_id: str,
    reconciliation_path: Path | None = None,
    require_active: bool = False,
) -> None:
    """Verifies whether a task has an authentic, verified live registration.

    Raises UnresolvedRegistrationError if the task is currently UNRESOLVED (or pending
    authorization when require_active=True) to prevent inventing non-existent registration IDs.
    """
    path = reconciliation_path or (_find_repo_root() / "config" / "scheduled_tasks" / "registration_reconciliation.json")
    if not path.is_file():
        raise FileNotFoundError(f"Registration reconciliation report not found at {path}")
    reconciliation = json.loads(path.read_text(encoding="utf-8"))

    # Map consolidated legacy sub-task IDs to their parent canonical task ID when not explicitly overridden in a custom fixture
    consolidated_aliases = {
        "repository_antipattern_audit": "ssot_drift_remediation",
        "technical_debt_elimination": "ssot_drift_remediation",
        "third_party_tool_consolidation": "ssot_drift_remediation",
        "worktree_coverage_cleanup": "pr_queue_observer",
        "cost_quality_review": "pipeline_outcome_assurance",
        "timeout_lock_watchdog": "pipeline_outcome_assurance",
        "frontier_llm_research_scout": "agentic_repo_best_practices_scout",
    }
    tasks_by_id = {t.get("task_id"): t for t in reconciliation.get("tasks", [])}
    task_info = tasks_by_id.get(task_id)
    if task_info is None and task_id in consolidated_aliases:
        task_info = tasks_by_id.get(consolidated_aliases[task_id])

    if task_info is not None:
        status = str(task_info.get("status", "")).upper()
        if status == "UNRESOLVED":
            raise UnresolvedRegistrationError(
                f"Task '{task_id}' has registration status UNRESOLVED. "
                "Inventing active registration IDs is prohibited under Track 1A invariants."
            )
        if require_active and status not in ("AUTHORIZED_INSTALLED", "ACTIVE"):
            raise UnresolvedRegistrationError(
                f"Task '{task_id}' has registration status '{task_info.get('status')}'. "
                "Active, installed registration required under Track 1A invariants."
            )
        return

    raise TaskNotRegisteredError(f"Task '{task_id}' not found in reconciliation report.")


def assert_active_registration(task_id: str, reconciliation_path: Path | None = None) -> None:
    """Verifies that a task is actively installed and authorized, not pending or unresolved."""
    return assert_registration_resolved(task_id, reconciliation_path=reconciliation_path, require_active=True)

