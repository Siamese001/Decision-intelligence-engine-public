"""tools.scheduled_task_core.coverage

Implements Wave 2 typed reconciliation, honest coverage accounting,
time-window boundary enforcement, and shared JSON/Markdown rendering.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class GateState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class GateEvaluationRecord:
    gate_id: str
    state: GateState
    receipt_digest: str | None = None
    evidence_ref: str | None = None
    error: str | None = None


@dataclass
class EvaluationSetRecord:
    evaluation_id: str
    operation_id: str
    runtime_root: str
    gates: dict[str, GateEvaluationRecord] = field(default_factory=dict)
    is_unreadable: bool = False
    error: str | None = None
    timestamp_utc: str | None = None

    @property
    def identity_key(self) -> str:
        """Composite identity preventing collision across runtime roots and operation IDs."""
        return f"{self.runtime_root}::{self.operation_id}::{self.evaluation_id}"

    @property
    def all_gates_passed(self) -> bool:
        if not self.gates or self.is_unreadable:
            return False
        return all(g.state == GateState.PASS for g in self.gates.values())


@dataclass(frozen=True)
class TimeWindow:
    """Enforces start inclusive, end exclusive UTC interval semantics."""
    start_utc: dt.datetime
    end_utc: dt.datetime

    def contains(self, timestamp: dt.datetime | str) -> bool:
        if isinstance(timestamp, str):
            clean = timestamp.replace("Z", "+00:00")
            parsed = dt.datetime.fromisoformat(clean)
        else:
            parsed = timestamp
        # Ensure UTC comparison
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        start = self.start_utc if self.start_utc.tzinfo else self.start_utc.replace(tzinfo=dt.timezone.utc)
        end = self.end_utc if self.end_utc.tzinfo else self.end_utc.replace(tzinfo=dt.timezone.utc)
        return start <= parsed < end


class TypedCoverageReconciliation:
    """Typed reconciliation engine deriving mathematical coverage identically for JSON and Markdown."""

    def __init__(
        self,
        task_id: str,
        run_id: str,
        time_window: TimeWindow | None = None,
        expected_evaluation_ids: list[str] | None = None,
    ) -> None:
        self.task_id = task_id
        self.run_id = run_id
        self.time_window = time_window
        self.expected_evaluation_ids: list[str] = list(expected_evaluation_ids or [])
        self._evaluations: dict[str, EvaluationSetRecord] = {}
        self.excluded_items: list[dict[str, Any]] = []
        self.collector_failures: list[dict[str, Any]] = []

    def record_evaluation(self, record: EvaluationSetRecord) -> None:
        """Records an evaluation set, checking time window if configured."""
        if self.time_window and record.timestamp_utc:
            if not self.time_window.contains(record.timestamp_utc):
                self.excluded_items.append({
                    "evaluation_id": record.evaluation_id,
                    "operation_id": record.operation_id,
                    "timestamp": record.timestamp_utc,
                    "reason": "EXCLUDED_OUTSIDE_TIME_WINDOW",
                })
                return

        self._evaluations[record.identity_key] = record

    def record_unreadable_item(
        self,
        raw_identifier: str,
        operation_id: str,
        runtime_root: str,
        error_message: str,
    ) -> None:
        """Records malformed or unreadable evidence into expected denominator."""
        rec = EvaluationSetRecord(
            evaluation_id=raw_identifier,
            operation_id=operation_id,
            runtime_root=runtime_root,
            is_unreadable=True,
            error=error_message,
        )
        self._evaluations[rec.identity_key] = rec
        if raw_identifier not in self.expected_evaluation_ids:
            self.expected_evaluation_ids.append(raw_identifier)

    def record_collector_failure(self, stage: str, error_message: str) -> None:
        """Records collector failure as typed evidence."""
        self.collector_failures.append({
            "stage": stage,
            "error": error_message,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        })

    @property
    def total_expected_evaluations(self) -> int:
        # Expected count is union of declared expected IDs and any discovered unreadable items
        all_expected = set(self.expected_evaluation_ids)
        for rec in self._evaluations.values():
            all_expected.add(rec.evaluation_id)
        return len(all_expected)

    @property
    def inspected_evaluations(self) -> list[EvaluationSetRecord]:
        return [r for r in self._evaluations.values() if not r.is_unreadable]

    @property
    def unreadable_items_count(self) -> int:
        return sum(1 for r in self._evaluations.values() if r.is_unreadable)

    @property
    def total_inspected_evaluations(self) -> int:
        return len(self.inspected_evaluations)

    @property
    def coverage_ratio(self) -> float:
        expected = self.total_expected_evaluations
        if expected == 0:
            return 1.0 if not self.collector_failures else 0.0
        return round(self.total_inspected_evaluations / expected, 4)

    @property
    def audit_status(self) -> str:
        if self.collector_failures:
            return "partial"
        if self.total_expected_evaluations == 0:
            return "not_run"
        if self.coverage_ratio >= 1.0 and self.unreadable_items_count == 0:
            return "complete"
        if self.total_inspected_evaluations > 0:
            return "partial"
        return "not_run"

    def get_aggregate_gate_metrics(self) -> tuple[int, int, int]:
        """Returns (total_evaluated, total_passed, total_failed) across all inspected evaluations."""
        evaluated = 0
        passed = 0
        failed = 0
        for ev in self.inspected_evaluations:
            for g in ev.gates.values():
                evaluated += 1
                if g.state == GateState.PASS:
                    passed += 1
                elif g.state in (GateState.FAIL, GateState.NOT_RUN):
                    failed += 1
        return evaluated, passed, failed

    def to_dict(self) -> dict[str, Any]:
        """Export serialized dictionary with strictly derived totals."""
        evaluated, passed, failed = self.get_aggregate_gate_metrics()
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "audit_status": self.audit_status,
            "coverage_ratio": self.coverage_ratio,
            "coverage_percentage": round(self.coverage_ratio * 100.0, 2),
            "total_expected_evaluations": self.total_expected_evaluations,
            "total_inspected_evaluations": self.total_inspected_evaluations,
            "unreadable_items_count": self.unreadable_items_count,
            "gates_evaluated": evaluated,
            "gates_passed": passed,
            "gates_failed": failed,
            "evaluation_ids": [r.evaluation_id for r in self._evaluations.values()],
            "collector_failures": list(self.collector_failures),
            "excluded_items": list(self.excluded_items),
        }

    def render_markdown(self) -> str:
        """Renders identical figures into a human-readable markdown table."""
        d = self.to_dict()
        lines = [
            "# Audit Coverage Reconciliation Report",
            "",
            f"- **Task ID**: `{d['task_id']}`",
            f"- **Run ID**: `{d['run_id']}`",
            f"- **Audit Status**: `{d['audit_status'].upper()}`",
            f"- **Coverage Ratio**: `{d['coverage_ratio']} ({d['coverage_percentage']}%)`",
            f"- **Evaluations**: Expected: {d['total_expected_evaluations']} | Inspected: {d['total_inspected_evaluations']} | Unreadable: {d['unreadable_items_count']}",
            f"- **Gates**: Evaluated: {d['gates_evaluated']} | Passed: {d['gates_passed']} | Failed: {d['gates_failed']}",
            "",
            "## Inspected Evaluation Sets",
            "",
            "| Evaluation ID | Operation ID | Runtime Root | Status | Passed Gates | Total Gates |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        if not self._evaluations:
            lines.append("| *(None)* | *(None)* | *(None)* | NOT_RUN | 0 | 0 |")
        else:
            for rec in self._evaluations.values():
                if rec.is_unreadable:
                    status = f"UNREADABLE ({rec.error})"
                    p_gates = 0
                    t_gates = 0
                else:
                    status = "PASS" if rec.all_gates_passed else "FAIL"
                    p_gates = sum(1 for g in rec.gates.values() if g.state == GateState.PASS)
                    t_gates = len(rec.gates)
                lines.append(f"| `{rec.evaluation_id}` | `{rec.operation_id}` | `{rec.runtime_root}` | {status} | {p_gates} | {t_gates} |")

        if self.collector_failures:
            lines.extend([
                "",
                "## Collector Failures",
                "",
                "| Stage | Error | Timestamp |",
                "| :--- | :--- | :--- |",
            ])
            for fail in self.collector_failures:
                lines.append(f"| `{fail['stage']}` | {fail['error']} | {fail['timestamp']} |")

        if self.excluded_items:
            lines.extend([
                "",
                "## Excluded Items",
                "",
                "| Evaluation ID | Reason | Timestamp |",
                "| :--- | :--- | :--- |",
            ])
            for ex in self.excluded_items:
                lines.append(f"| `{ex['evaluation_id']}` | {ex['reason']} | {ex['timestamp']} |")

        return "\n".join(lines) + "\n"
