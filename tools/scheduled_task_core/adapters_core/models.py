"""tools/scheduled_task_core/adapters_core/models.py

Data models for domain task execution results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..contracts import ExecutionMode, TriggerOrigin


@dataclass
class TaskExecutionResult:
    """Structured result of executing a domain task adapter."""
    task_id: str
    run_id: str
    mode: ExecutionMode
    trigger_origin: TriggerOrigin
    terminal_outcome: str
    findings_count: int
    findings: list[dict[str, Any]] = field(default_factory=list)
    mutations_count: int = 0
    report_path: Path | None = None
    approval_request_id: str | None = None
    implementation_plan_path: Path | None = None
    implementation_plan_sha256: str | None = None
    is_read_only: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}
        if "learning_feedback_loop" not in self.metadata:
            candidate_dir = None
            if self.implementation_plan_path is not None:
                candidate_dir = Path(self.implementation_plan_path).parent
            elif self.report_path is not None:
                candidate_dir = Path(self.report_path).parent
            if candidate_dir is not None and candidate_dir.exists():
                from ..learning_feedback import record_task_decision_feedback

                self.metadata["learning_feedback_loop"] = record_task_decision_feedback(
                    task_name=self.task_id,
                    findings=self.findings,
                    run_dir=candidate_dir,
                    status=self.terminal_outcome,
                )

