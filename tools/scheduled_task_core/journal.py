"""tools.scheduled_task_core.journal

Implements Track 1B Operational Change Journaling across eight discrete categories,
strictly separating operational changes from verified defect repairs.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class OperationalCategory(str, Enum):
    GRAPH = "graph"
    SIDECAR = "sidecar"
    DIRECTIVE = "directive"
    APPROVAL = "approval"
    RELEASE = "release"
    FILESYSTEM = "filesystem"
    GIT = "git"
    SCHEDULER = "scheduler"


@dataclass(frozen=True)
class OperationalChangeRecord:
    """Immutable record of an operational action or external change."""
    action_id: str
    category: OperationalCategory
    action_type: str
    target_id: str
    authorized: bool
    verified_defect_repair: bool = False
    is_external: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    )


class OperationalChangeJournal:
    """Durable in-memory and exportable change journal.

    Separates graph, sidecar, directive, approval, release, filesystem, Git, and scheduler changes.
    Enforces that administrative actions (such as directive cancellations or file quaratines)
    are accounted for as operational changes, never falsely claimed as verified defect repairs.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._records: list[OperationalChangeRecord] = []

    def record_action(
        self,
        action_id: str,
        category: OperationalCategory | str,
        action_type: str,
        target_id: str,
        *,
        authorized: bool = True,
        verified_defect_repair: bool = False,
        details: dict[str, Any] | None = None,
    ) -> OperationalChangeRecord:
        """Records an authorized or attempted operational change."""
        cat = OperationalCategory(category)
        rec = OperationalChangeRecord(
            action_id=action_id,
            category=cat,
            action_type=action_type,
            target_id=target_id,
            authorized=authorized,
            verified_defect_repair=verified_defect_repair,
            is_external=False,
            details=details or {},
        )
        self._records.append(rec)
        return rec

    def record_blocked_action(
        self,
        action_id: str,
        category: OperationalCategory | str,
        action_type: str,
        target_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> OperationalChangeRecord:
        """Records a blocked/unauthorized action attempt."""
        d = dict(details or {})
        d["rejection_reason"] = reason
        rec = OperationalChangeRecord(
            action_id=action_id,
            category=OperationalCategory(category),
            action_type=action_type,
            target_id=target_id,
            authorized=False,
            verified_defect_repair=False,
            is_external=False,
            details=d,
        )
        self._records.append(rec)
        return rec

    def record_external_change(
        self,
        action_id: str,
        category: OperationalCategory | str,
        target_id: str,
        details: dict[str, Any] | None = None,
    ) -> OperationalChangeRecord:
        """Records an external or concurrent change that is not attributed to the task."""
        rec = OperationalChangeRecord(
            action_id=action_id,
            category=OperationalCategory(category),
            action_type="external_mutation_detected",
            target_id=target_id,
            authorized=False,
            verified_defect_repair=False,
            is_external=True,
            details=details or {"attribution": "UNRESOLVED_CONCURRENT_EXTERNAL"},
        )
        self._records.append(rec)
        return rec

    @property
    def records(self) -> list[OperationalChangeRecord]:
        return list(self._records)

    @property
    def total_operational_changes(self) -> int:
        """Count of all authorized operational changes performed by the task."""
        return sum(1 for r in self._records if r.authorized and not r.is_external)

    @property
    def total_verified_defect_repairs(self) -> int:
        """Count of verified defect repairs (e.g. passing reproduction + test patch)."""
        return sum(1 for r in self._records if r.authorized and r.verified_defect_repair and not r.is_external)

    @property
    def unauthorized_attempts_count(self) -> int:
        """Count of unauthorized or blocked action attempts."""
        return sum(1 for r in self._records if not r.authorized and not r.is_external)

    @property
    def external_changes_count(self) -> int:
        """Count of concurrent external changes."""
        return sum(1 for r in self._records if r.is_external)

    def get_category_count(self, category: OperationalCategory) -> int:
        """Count of authorized operational actions in a specific category."""
        return sum(1 for r in self._records if r.category == category and r.authorized and not r.is_external)

    def to_summary_dict(self) -> dict[str, int]:
        """Returns summary dictionary conforming to the contract schema."""
        return {
            "graph_delta": self.get_category_count(OperationalCategory.GRAPH),
            "sidecar_delta": self.get_category_count(OperationalCategory.SIDECAR),
            "directive_delta": self.get_category_count(OperationalCategory.DIRECTIVE),
            "approval_delta": self.get_category_count(OperationalCategory.APPROVAL),
            "release_delta": self.get_category_count(OperationalCategory.RELEASE),
            "filesystem_delta": self.get_category_count(OperationalCategory.FILESYSTEM),
            "git_delta": self.get_category_count(OperationalCategory.GIT),
            "scheduler_delta": self.get_category_count(OperationalCategory.SCHEDULER),
            "verified_defect_repairs": self.total_verified_defect_repairs,
            "unauthorized_attempts": self.unauthorized_attempts_count,
        }

    def to_audit_payload(self) -> dict[str, Any]:
        """Exports complete operational accounting payload with all individual records."""
        return {
            "run_id": self.run_id,
            "summary": self.to_summary_dict(),
            "total_operational_changes": self.total_operational_changes,
            "total_verified_defect_repairs": self.total_verified_defect_repairs,
            "unauthorized_attempts_count": self.unauthorized_attempts_count,
            "external_changes_count": self.external_changes_count,
            "records": [asdict(r) for r in self._records],
        }
