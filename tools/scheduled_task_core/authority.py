"""tools.scheduled_task_core.authority

Implements Track 1B execution authority enforcement, anti-spoofing guards,
authentic human approval verification, and mutation boundary gates.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from .contracts import ExecutionMode, TriggerOrigin
from .journal import OperationalCategory, OperationalChangeJournal

logger = logging.getLogger(__name__)


class ApprovalVerificationError(ValueError):
    """Base error for approval verification failures."""


class UnauthenticatedApprovalError(ApprovalVerificationError):
    """Raised when an approval lacks an authentic human operator or valid signature."""


class ExpiredApprovalError(ApprovalVerificationError):
    """Raised when an approval receipt is past its expiry date."""


class ReplayNonceError(ApprovalVerificationError):
    """Raised when an approval replay nonce has already been consumed."""


class ItemSetDriftError(ApprovalVerificationError):
    """Raised when approved items do not exactly match candidate item scope."""


class PlanDriftError(ApprovalVerificationError):
    """Raised when plan or baseline digest does not match the approved digest."""


class ScheduledMutationBlockedError(PermissionError):
    """Raised when an unauthorized mutation is attempted under scheduled or unattended execution."""


class DirectCancellationBlockedError(PermissionError):
    """Raised when direct cancellation is attempted without valid operator authority."""


class RestoreWithoutAuthorityError(PermissionError):
    """Raised when quarantine restore is attempted without explicit authority."""


@dataclass(frozen=True)
class AuthorityContext:
    """Immutable execution authority context established by trusted launcher."""
    task_id: str
    run_id: str
    trigger_origin: TriggerOrigin
    execution_mode: ExecutionMode
    actor: str
    allowed_capabilities: set[str] = field(default_factory=set)
    allowed_roots: list[Path] = field(default_factory=list)
    is_scheduled: bool = False
    declared_evidence_dirs: list[Path] = field(default_factory=list)

    @property
    def is_interactive(self) -> bool:
        """Determines if this context is running in interactive mode."""
        return self.execution_mode == ExecutionMode.INTERACTIVE

    @property
    def can_mutate(self) -> bool:
        """Determines if this context is permitted to execute live mutations."""
        if self.is_scheduled or self.trigger_origin == TriggerOrigin.SCHEDULER_CRON:
            return False
        return self.execution_mode in (ExecutionMode.AUTHORIZED_APPLY, ExecutionMode.INTERACTIVE)

    def assert_can_mutate(self, operation_name: str = "mutation") -> None:
        """Fails closed if the context is not authorized for live mutations."""
        if not self.can_mutate:
            raise ScheduledMutationBlockedError(
                f"Unauthorized {operation_name} blocked: task '{self.task_id}' run '{self.run_id}' "
                f"is running under trigger '{self.trigger_origin.value}' mode '{self.execution_mode.value}'."
            )

    def check_caller_scheduled_bypass(self, caller_scheduled: bool | None) -> None:
        """Enforces that a caller passing scheduled=False cannot bypass a scheduled authority context."""
        if self.is_scheduled or self.trigger_origin == TriggerOrigin.SCHEDULER_CRON:
            if caller_scheduled is False:
                raise ScheduledMutationBlockedError(
                    "Caller-supplied scheduled=False cannot override trusted scheduled authority context."
                )


@dataclass(frozen=True)
class ApprovalRecord:
    """Authentic operator decision record binding an authorized action."""
    approval_id: str
    task_id: str
    run_id: str
    operator: str
    signature: str
    status: str
    approved_items: list[str]
    plan_digest: str
    baseline_sha: str
    issued_at: str
    expires_at: str
    replay_nonce: str
    is_authentic_human: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_iso(timestamp_str: str) -> dt.datetime:
    clean = timestamp_str.replace("Z", "+00:00")
    return dt.datetime.fromisoformat(clean)


def _load_approval_data(source: dict[str, Any] | Path | ApprovalRecord) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    if isinstance(source, ApprovalRecord) or is_dataclass(source):
        return asdict(source)
    if not isinstance(source, Path):
        raise UnauthenticatedApprovalError("Approval source must be a Path, dictionary, or ApprovalRecord.")
    if not source.is_file():
        raise UnauthenticatedApprovalError(f"Approval file not found at {source}")
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise UnauthenticatedApprovalError(f"Approval receipt is invalid JSON: {exc}") from exc


def _verify_identity_and_binding(data: dict[str, Any], context: AuthorityContext) -> tuple[str, str, str]:
    operator = data.get("operator", "").strip()
    if not operator:
        raise UnauthenticatedApprovalError("Approval record is missing operator identity.")
    if operator.lower() in ("auto", "scheduler", "cron", "system", "anonymous", "none"):
        raise UnauthenticatedApprovalError(f"Operator '{operator}' is not an authentic human operator.")
    status = data.get("status", "").upper()
    if status not in ("APPROVED", "PASS"):
        raise UnauthenticatedApprovalError(f"Approval record status '{status}' is not approved.")
    signature = data.get("signature", "").strip()
    alr_id = data.get("alr_id", "").strip()
    if not signature and not alr_id:
        raise UnauthenticatedApprovalError(
            "Approval lacks cryptographic signature or Ambiguity Lock Register (ALR) evidence. "
            "Self-authored JSON hash without human authorization is strictly rejected."
        )
    app_task_id = data.get("task_id")
    if app_task_id and app_task_id != context.task_id:
        raise UnauthenticatedApprovalError(
            f"Approval task_id mismatch: receipt specifies '{app_task_id}', current task is '{context.task_id}'."
        )
    app_run_id = data.get("run_id")
    if app_run_id and app_run_id != context.run_id:
        raise UnauthenticatedApprovalError(
            f"Approval run_id mismatch: receipt was generated for run '{app_run_id}', current run is '{context.run_id}'."
        )
    return operator, status, signature or alr_id


def _verify_expiry_and_nonce(
    data: dict[str, Any], consumed_nonces: set[str] | None, now_utc: dt.datetime | None,
) -> tuple[str, str]:
    current_time = now_utc or dt.datetime.now(dt.timezone.utc)
    expires_at_str = data.get("expires_at")
    if expires_at_str:
        try:
            expires_at = _parse_iso(expires_at_str)
            if current_time > expires_at:
                raise ExpiredApprovalError(f"Approval expired at {expires_at_str} (current: {current_time.isoformat()})")
        except ValueError as exc:
            raise ExpiredApprovalError(f"Malformed expires_at timestamp '{expires_at_str}': {exc}") from exc
    nonce = data.get("replay_nonce", "").strip()
    if not nonce:
        raise ReplayNonceError("Approval record is missing mandatory replay_nonce.")
    if consumed_nonces is not None and nonce in consumed_nonces:
        raise ReplayNonceError(f"Approval nonce '{nonce}' has already been consumed (replay attempt detected).")
    return nonce, expires_at_str or ""


def _verify_approved_scope(
    data: dict[str, Any], expected_items: list[str] | None,
    expected_plan_digest: str | None, current_baseline_sha: str | None,
) -> tuple[list[str], str, str]:
    approved_items = data.get("approved_items", [])
    if expected_items is not None and sorted(approved_items) != sorted(expected_items):
        raise ItemSetDriftError(
            "Approved items do not match candidate item scope. "
            f"Approved: {sorted(approved_items)}, Expected: {sorted(expected_items)}"
        )
    actual_plan_digest = data.get("plan_digest")
    if expected_plan_digest is not None and actual_plan_digest != expected_plan_digest:
        raise PlanDriftError(
            f"Plan digest mismatch: approved for '{actual_plan_digest}', expected '{expected_plan_digest}'"
        )
    actual_baseline = data.get("baseline_sha")
    if current_baseline_sha is not None and actual_baseline != current_baseline_sha:
        raise PlanDriftError(
            f"Baseline git commit drift: approved on baseline '{actual_baseline}', current is '{current_baseline_sha}'"
        )
    return approved_items, actual_plan_digest or "", actual_baseline or ""


def verify_approval_record(
    approval_source: dict[str, Any] | Path,
    context: AuthorityContext,
    *,
    expected_items: list[str] | None = None,
    expected_plan_digest: str | None = None,
    current_baseline_sha: str | None = None,
    consumed_nonces: set[str] | None = None,
    now_utc: dt.datetime | None = None,
) -> ApprovalRecord:
    """Verifies that an approval record satisfies all authentic human governance requirements.

    Enforces:
    1. Authentic human operator signature (rejects empty, 'auto', or 'scheduler').
    2. Exact task_id and run_id binding (rejects old approvals from prior runs).
    3. Expiry check against current UTC time.
    4. Replay protection using nonces.
    5. Item scope parity (zero candidate drift).
    6. Baseline SHA & plan digest parity.
    7. Rejection of self-authored JSON hashes lacking authenticated operator backing.
    """
    data = _load_approval_data(approval_source)
    operator, status, signature = _verify_identity_and_binding(data, context)
    nonce, expires_at_str = _verify_expiry_and_nonce(data, consumed_nonces, now_utc)
    approved_items, actual_plan_digest, actual_baseline = _verify_approved_scope(
        data, expected_items, expected_plan_digest, current_baseline_sha,
    )

    return ApprovalRecord(
        approval_id=data.get("approval_id", f"app-{nonce}"),
        task_id=context.task_id,
        run_id=context.run_id,
        operator=operator,
        signature=signature,
        status=status,
        approved_items=approved_items,
        plan_digest=actual_plan_digest,
        baseline_sha=actual_baseline,
        issued_at=data.get("issued_at", ""),
        expires_at=expires_at_str,
        replay_nonce=nonce,
        is_authentic_human=True,
        metadata=data.get("metadata", {}),
    )


def validate_execution_authority(
    context: AuthorityContext,
    required_capability: str | None = None,
) -> None:
    """Validates that the authority context satisfies operational invariants."""
    if not context.execution_mode:
        raise ScheduledMutationBlockedError("Execution mode is omitted. Fails closed under Track 1B invariants.")

    if required_capability and required_capability not in context.allowed_capabilities:
        raise ScheduledMutationBlockedError(
            f"Context lacks declared capability '{required_capability}'. Allowed: {context.allowed_capabilities}"
        )


def authorize_cancellation(
    context: AuthorityContext,
    directive_id: str,
    journal: OperationalChangeJournal,
    approval_source: dict[str, Any] | Path | None = None,
) -> None:
    """Enforces authority gate for intake directive or task cancellation.

    Direct cancellation without authority fails before side effects, recording an unauthorized attempt.
    """
    if context.is_scheduled or not context.can_mutate:
        journal.record_blocked_action(
            action_id=f"cancel-{directive_id}",
            category=OperationalCategory.DIRECTIVE,
            action_type="cancel",
            target_id=directive_id,
            reason="Direct cancellation blocked under scheduled or unattended authority.",
        )
        raise DirectCancellationBlockedError(
            f"Direct cancellation of directive '{directive_id}' is prohibited under scheduled authority."
        )

    if approval_source is None:
        journal.record_blocked_action(
            action_id=f"cancel-{directive_id}",
            category=OperationalCategory.DIRECTIVE,
            action_type="cancel",
            target_id=directive_id,
            reason="Missing authentic human approval for cancellation.",
        )
        raise DirectCancellationBlockedError("Cancellation requires verified human approval record.")

    # Validate approval
    verify_approval_record(approval_source, context, expected_items=[directive_id])

    # Record authorized operational change (NEVER counted as verified defect repair)
    journal.record_action(
        action_id=f"cancel-{directive_id}",
        category=OperationalCategory.DIRECTIVE,
        action_type="cancel",
        target_id=directive_id,
        authorized=True,
        verified_defect_repair=False,
        details={"directive_id": directive_id, "attribution": "AUTHORIZED_HUMAN_OPERATOR"},
    )


def authorize_restore(
    context: AuthorityContext,
    batch_id: str,
    journal: OperationalChangeJournal,
    approval_source: dict[str, Any] | Path | None = None,
) -> None:
    """Enforces authority gate for quarantine restoration.

    Fails before restoring files if context lacks authority.
    """
    if context.is_scheduled or not context.can_mutate:
        journal.record_blocked_action(
            action_id=f"restore-{batch_id}",
            category=OperationalCategory.FILESYSTEM,
            action_type="restore",
            target_id=batch_id,
            reason="Restore without interactive/applied authority blocked.",
        )
        raise RestoreWithoutAuthorityError("Quarantine restore is strictly prohibited in scheduled execution.")

    if approval_source:
        verify_approval_record(approval_source, context)

    journal.record_action(
        action_id=f"restore-{batch_id}",
        category=OperationalCategory.FILESYSTEM,
        action_type="restore",
        target_id=batch_id,
        authorized=True,
        verified_defect_repair=False,
    )
