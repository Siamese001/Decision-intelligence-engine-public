"""tools.scheduled_task_core.handoff

Implements durable handoff packages for scheduled tasks, ensuring that
unauthorized repair attempts stop cleanly with an inspectable package
rather than hanging or attempting interactive question loops.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .authority import AuthorityContext
from .findings import DurableFinding

logger = logging.getLogger(__name__)

CONTRACT_ID = "urn:nee:scheduled-task-handoff:1.0.0"
SCHEMA_VERSION = "1.0.0"


class HandoffError(Exception):
    """Base exception for handoff package operations."""


class InteractiveLoopBlockedError(HandoffError):
    """Raised when an unattended scheduled task attempts an interactive question loop."""


@dataclass
class ProposedAction:
    action_type: str
    target_resource: str
    description: str
    requires_approval: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class DurableHandoffPackage:
    """Encapsulates durable remediation context for human operator review."""

    handoff_id: str
    task_id: str
    run_id: str
    created_at_utc: str
    finding_ids: list[str]
    summary: str
    required_approval_scope: list[str]
    proposed_actions: list[dict[str, Any]]
    reproduction_commands: list[str]
    status: str = "AWAITING_OPERATOR_REVIEW"
    implementation_plan_path: str | None = None
    implementation_plan_sha256: str | None = None
    unattended_stop_reason: str = (
        "Unattended scheduled run lacks authorized repair credentials. "
        "Interactive prompts are barred in automated runs; emitting durable handoff package."
    )
    review_instructions: str = (
        "Inspect reproduction commands and proposed actions. To authorize, issue a governed "
        "operator approval token binding the run ID, item set digest, and target commit SHA."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": CONTRACT_ID,
            "schema_version": SCHEMA_VERSION,
            "handoff_id": self.handoff_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "created_at_utc": self.created_at_utc,
            "finding_ids": list(self.finding_ids),
            "summary": self.summary,
            "required_approval_scope": list(self.required_approval_scope),
            "proposed_actions": self.proposed_actions,
            "reproduction_commands": list(self.reproduction_commands),
            "status": self.status,
            "implementation_plan_path": self.implementation_plan_path,
            "implementation_plan_sha256": self.implementation_plan_sha256,
            "unattended_stop_reason": self.unattended_stop_reason,
            "review_instructions": self.review_instructions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DurableHandoffPackage:
        return cls(
            handoff_id=str(data["handoff_id"]),
            task_id=str(data["task_id"]),
            run_id=str(data["run_id"]),
            created_at_utc=str(data["created_at_utc"]),
            finding_ids=list(data.get("finding_ids", [])),
            summary=str(data.get("summary", "")),
            required_approval_scope=list(data.get("required_approval_scope", [])),
            proposed_actions=list(data.get("proposed_actions", [])),
            reproduction_commands=list(data.get("reproduction_commands", [])),
            status=str(data.get("status", "AWAITING_OPERATOR_REVIEW")),
            implementation_plan_path=data.get("implementation_plan_path"),
            implementation_plan_sha256=data.get("implementation_plan_sha256"),
            unattended_stop_reason=str(data.get("unattended_stop_reason", "")),
            review_instructions=str(data.get("review_instructions", "")),
        )

    def verify_plan_sealed(self) -> bool:
        """Returns True only if a verified implementation plan path and SHA-256 digest are sealed (`UNCONDITIONAL_WAVE_PLAN_MANDATE`)."""
        return bool(self.implementation_plan_path and self.implementation_plan_sha256)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> DurableHandoffPackage:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


def create_durable_handoff(
    task_id: str,
    run_id: str,
    findings: list[DurableFinding],
    authority: AuthorityContext,
    summary: str | None = None,
    required_approval_scope: list[str] | None = None,
    proposed_actions: list[dict[str, Any]] | None = None,
    output_path: Path | None = None,
    implementation_plan_path: str | Path | None = None,
    implementation_plan_sha256: str | None = None,
    require_plan: bool = False,
) -> DurableHandoffPackage:
    """Creates an inspectable durable handoff package when repair execution is not authorized."""
    if require_plan and (not implementation_plan_path or not implementation_plan_sha256):
        raise HandoffError(
            f"Durable handoff for task '{task_id}' ({len(findings)} finding(s)) requires "
            "a verified implementation plan path and SHA-256 digest (`UNCONDITIONAL_WAVE_PLAN_MANDATE`)."
        )
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    handoff_id = f"handoff-{task_id}-{run_id}"

    finding_ids = [f.finding_id for f in findings]
    repro_cmds: list[str] = []
    for f in findings:
        if f.reproduction_evidence and f.reproduction_evidence.test_command:
            repro_cmds.append(f.reproduction_evidence.test_command)

    default_summary = (
        f"Scheduled task '{task_id}' detected {len(findings)} finding(s) requiring remediation. "
        "Execution stopped cleanly under unattended governance boundaries."
    )
    default_scope = required_approval_scope or ["APPLY_PATCH", "MUTATE_GRAPH"]
    actions = proposed_actions or [
        {
            "action_type": "APPLY_LOCAL_PATCH",
            "target_resource": f"finding_ids: {','.join(finding_ids)}",
            "description": "Apply proposed and verified code fixes in isolated worktree.",
            "requires_approval": True,
        }
    ]

    stop_reason = (
        "Unattended scheduled run lacks authorized repair credentials. "
        "Interactive prompts are barred in automated runs; emitting durable handoff package."
        if not authority.can_mutate
        else "Repairs proposed but required specific operator token authorization."
    )

    plan_path_str = str(implementation_plan_path) if implementation_plan_path else None

    package = DurableHandoffPackage(
        handoff_id=handoff_id,
        task_id=task_id,
        run_id=run_id,
        created_at_utc=now_iso,
        finding_ids=finding_ids,
        summary=summary or default_summary,
        required_approval_scope=default_scope,
        proposed_actions=actions,
        reproduction_commands=repro_cmds,
        status="AWAITING_OPERATOR_REVIEW",
        implementation_plan_path=plan_path_str,
        implementation_plan_sha256=implementation_plan_sha256,
        unattended_stop_reason=stop_reason,
    )

    if output_path:
        package.save(output_path)

    return package
