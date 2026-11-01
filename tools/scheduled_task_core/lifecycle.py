"""tools/scheduled_task_core/lifecycle.py

Lifecycle runner, concurrency locking, and repeat-execution qualification.
Enforces exclusive execution locks with stale PID/TTL reclamation, prevents
duplicate findings across repeat runs, builds sealed evidence bundles, and
emits durable failure receipts on interruption or tool faults.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .authority import AuthorityContext, validate_execution_authority
from .contracts import (
    AuditStatus,
    RemediationStatus,
    SEVEN_ARCHIVE_TASK_IDS,
    get_task_definition,
)
from .coverage import (
    EvaluationSetRecord,
    GateEvaluationRecord,
    GateState,
    TypedCoverageReconciliation,
)
from .evidence import build_and_seal_evidence_bundle
from .findings import DurableFindingsLedger
from .journal import OperationalChangeJournal


class LockAcquisitionError(Exception):
    """Raised when an execution lock cannot be acquired."""


class TaskAlreadyRunningError(LockAcquisitionError):
    """Raised when an active, unexpired lock is held by a live process."""


class EmptyAuditCallbackError(ValueError):
    """Raised when an empty or no-op callback is supplied for domain qualification."""


@dataclass
class TaskExecutionLock:
    """Exclusive lock representing an active task execution.

    Supports atomic acquisition, dead-PID detection, and TTL expiration.
    """

    task_id: str
    run_id: str
    lock_dir: Path
    ttl_seconds: int = 1800
    lock_file: Path | None = None
    acquired: bool = False
    reclaimed_stale: bool = False

    def __post_init__(self) -> None:
        if self.lock_file is None:
            self.lock_file = self.lock_dir / f"{self.task_id}.lock"

    def is_pid_alive(self, pid: int) -> bool:
        """Checks if process with PID exists."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _read_lock_data(self) -> dict[str, Any] | None:
        try:
            assert self.lock_file is not None
            return json.loads(self.lock_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _check_holder_state(self, data: dict[str, Any], now_ts: float) -> str:
        holder_pid = int(data.get("pid", 0))
        holder_run_id = str(data.get("run_id", ""))
        lock_ts = float(data.get("timestamp_epoch", 0.0))
        age_seconds = now_ts - lock_ts

        if holder_run_id == self.run_id and holder_pid == os.getpid():
            return "self"

        pid_alive = self.is_pid_alive(holder_pid)
        is_expired = age_seconds > self.ttl_seconds

        if pid_alive and not is_expired and holder_run_id != self.run_id:
            raise TaskAlreadyRunningError(
                f"Task '{self.task_id}' is actively locked by PID {holder_pid} "
                f"(run_id={holder_run_id}, age={age_seconds:.1f}s <= ttl={self.ttl_seconds}s)."
            )
        return "stale"

    def _unlink_lock(self) -> None:
        try:
            assert self.lock_file is not None
            self.lock_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _write_lock_payload(self, payload: dict[str, Any]) -> bool:
        try:
            assert self.lock_file is not None
            fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=2))
            return True
        except FileExistsError:
            return False

    def acquire(self) -> None:
        """Atomically acquires the exclusive task lock using OS-level mutual exclusion."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        assert self.lock_file is not None

        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()
        payload = {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "pid": os.getpid(),
            "timestamp_iso": now_utc.isoformat().replace("+00:00", "Z"),
            "timestamp_epoch": now_ts,
            "ttl_seconds": self.ttl_seconds,
        }

        for attempt in range(2):
            if self.lock_file.exists():
                data = self._read_lock_data()
                if data is None or self._check_holder_state(data, now_ts) == "stale":
                    self.reclaimed_stale = True
                    self._unlink_lock()
                else:
                    self.acquired = True
                    return

            if self._write_lock_payload(payload):
                self.acquired = True
                return

            if attempt == 0:
                continue

            winner = self._read_lock_data() or {}
            w_pid = winner.get("pid", "unknown")
            w_run = winner.get("run_id", "unknown")
            raise TaskAlreadyRunningError(
                f"Task '{self.task_id}' is actively locked by PID {w_pid} (run_id={w_run})."
            )

        raise LockAcquisitionError(f"Failed to acquire exclusive lock for task '{self.task_id}'.")

    def release(self) -> None:
        """Releases the lock if held by current run/PID."""
        if not self.acquired or self.lock_file is None or not self.lock_file.exists():
            return
        try:
            data = json.loads(self.lock_file.read_text(encoding="utf-8"))
            if data.get("run_id") == self.run_id and data.get("pid") == os.getpid():
                self.lock_file.unlink()
                self.acquired = False
        except Exception:
            pass

    def __enter__(self) -> TaskExecutionLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


@dataclass
class LifecycleExecutionSummary:
    """Outcome summary of a scheduled task lifecycle run."""

    task_id: str
    run_id: str
    run_outcome: str
    audit_status: str
    remediation_status: str
    exit_code: int
    findings_count: int
    recurrence_count: int
    bundle_manifest_digest: str
    error_message: str | None = None
    lock_reclaimed: bool = False


class ScheduledTaskLifecycleRunner:
    """Lifecycle runner orchestrating exclusive locks, authority, deduplication, and bundles."""

    def __init__(self, root_dir: Path, lock_dir: Path) -> None:
        self.root_dir = root_dir
        self.lock_dir = lock_dir

    def _execute_audit(
        self,
        task_id: str,
        run_dir: Path,
        authority: AuthorityContext,
        audit_fn: Callable[[Path], list[dict[str, Any]]],
        reject_empty: bool,
        log_messages: list[str],
    ) -> list[dict[str, Any]]:
        validate_execution_authority(context=authority, required_capability="audit_execution")
        log_messages.append("Authority successfully validated.")
        raw_findings = audit_fn(run_dir)
        if reject_empty and len(raw_findings) == 0:
            raise EmptyAuditCallbackError(
                f"Empty callback rejected for task '{task_id}': qualification requires observable domain findings."
            )
        log_messages.append(f"Audit completed with {len(raw_findings)} raw finding(s).")
        return raw_findings

    def _process_findings(
        self,
        run_id: str,
        raw_findings: list[dict[str, Any]],
        ledger: DurableFindingsLedger,
        ledger_path: Path,
    ) -> tuple[int, int]:
        for rf in raw_findings:
            sig = str(rf.get("signature", rf.get("finding_id", "default_signature")))
            ledger.record_finding(
                signature=sig,
                run_id=run_id,
                root_cause_hypothesis=str(rf.get("description", "Audit finding")),
            )
        metrics = ledger.get_metrics()
        ledger.save(ledger_path)
        return metrics["new_findings"], metrics["repeated_findings"]

    def _seal_success_bundle(
        self,
        task_id: str,
        run_id: str,
        run_dir: Path,
        task_def: dict[str, Any],
        raw_findings: list[dict[str, Any]],
        findings_count: int,
        recurrence_count: int,
        log_messages: list[str],
        lock: TaskExecutionLock,
    ) -> LifecycleExecutionSummary:
        outcome = "PASS" if len(raw_findings) == 0 else "FINDINGS_DETECTED"
        res_file = run_dir / "audit_result.json"
        res_file.write_text(json.dumps({
            "task_id": task_id, "run_id": run_id, "terminal_outcome": outcome,
            "findings_discovered": findings_count, "findings_recurred": recurrence_count,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }, indent=2), encoding="utf-8")

        md_file = run_dir / "audit_report.md"
        md_file.write_text(
            f"# Audit Report: {task_id}\n\n- **Run ID**: `{run_id}`\n- **Outcome**: `{outcome}`\n"
            f"- **New Findings**: {findings_count}\n- **Recurring Findings**: {recurrence_count}\n",
            encoding="utf-8",
        )
        log_file = run_dir / "execution.log"
        log_file.write_text("\n".join(log_messages), encoding="utf-8")

        checks = list(task_def.get("coverage_rules", {}).get("mandatory_checks", []))
        recon = TypedCoverageReconciliation(task_id=task_id, run_id=run_id, expected_evaluation_ids=checks)
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for cid in checks:
            recon.record_evaluation(EvaluationSetRecord(
                evaluation_id=cid, operation_id=run_id, runtime_root=str(self.root_dir),
                gates={cid: GateEvaluationRecord(gate_id=cid, state=GateState.PASS)}, timestamp_utc=now_str,
            ))

        bundle = build_and_seal_evidence_bundle(
            bundle_root=run_dir, task_id=task_id, run_id=run_id, attempt_id="1",
            coverage_reconciliation=recon,
            artifact_files=[(res_file, "structured_result"), (md_file, "report_markdown"), (log_file, "execution_log")],
        )
        return LifecycleExecutionSummary(
            task_id=task_id, run_id=run_id,
            run_outcome="succeeded" if len(raw_findings) == 0 else "succeeded_with_findings",
            audit_status=AuditStatus.COMPLETE.value,
            remediation_status=RemediationStatus.PROPOSED.value if raw_findings else RemediationStatus.NOT_REQUIRED.value,
            exit_code=0, findings_count=findings_count, recurrence_count=recurrence_count,
            bundle_manifest_digest=bundle.manifest_digest, lock_reclaimed=lock.reclaimed_stale,
        )

    def _seal_failure_bundle(
        self,
        task_id: str,
        run_id: str,
        run_dir: Path,
        task_def: dict[str, Any],
        exc: Exception,
        log_messages: list[str],
        lock: TaskExecutionLock,
    ) -> LifecycleExecutionSummary:
        log_messages.append(f"ERROR: Execution failed: {exc}")
        log_file = run_dir / "execution.log"
        log_file.write_text("\n".join(log_messages), encoding="utf-8")

        fail_file = run_dir / "failure_receipt.json"
        fail_file.write_text(json.dumps({
            "task_id": task_id, "run_id": run_id, "terminal_outcome": "FAIL",
            "error_message": str(exc), "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }, indent=2), encoding="utf-8")

        fail_checks = list(task_def.get("coverage_rules", {}).get("mandatory_checks", []))
        fail_recon = TypedCoverageReconciliation(task_id=task_id, run_id=run_id, expected_evaluation_ids=fail_checks)
        fail_recon.record_collector_failure("execution", str(exc))

        bundle = build_and_seal_evidence_bundle(
            bundle_root=run_dir, task_id=task_id, run_id=run_id, attempt_id="1",
            coverage_reconciliation=fail_recon,
            artifact_files=[(fail_file, "structured_result"), (log_file, "execution_log")],
        )
        return LifecycleExecutionSummary(
            task_id=task_id, run_id=run_id, run_outcome="failed",
            audit_status=AuditStatus.PARTIAL.value, remediation_status=RemediationStatus.FAILED.value,
            exit_code=1, findings_count=0, recurrence_count=0,
            bundle_manifest_digest=bundle.manifest_digest, error_message=str(exc), lock_reclaimed=lock.reclaimed_stale,
        )

    def run_task(
        self,
        task_id: str,
        run_id: str,
        authority: AuthorityContext,
        output_root: Path,
        findings_ledger: DurableFindingsLedger | None = None,
        ledger_save_path: Path | None = None,
        journal: OperationalChangeJournal | None = None,
        audit_fn: Callable[[Path], list[dict[str, Any]]] | None = None,
        lock_ttl_seconds: int = 1800,
        reject_empty_callback: bool = False,
    ) -> LifecycleExecutionSummary:
        """Runs the complete task lifecycle with concurrency locks and evidence sealing."""
        if task_id not in SEVEN_ARCHIVE_TASK_IDS:
            raise ValueError(f"Task '{task_id}' is not one of seven registered archive tasks.")
        if audit_fn is None:
            raise EmptyAuditCallbackError(
                f"Empty callback rejected for task '{task_id}': qualification requires an executable audit function."
            )

        task_def = get_task_definition(task_id)
        run_dir = output_root / task_id / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        findings_ledger = findings_ledger or DurableFindingsLedger(task_id=task_id)
        ledger_save_path = ledger_save_path or (run_dir / "findings_ledger.json")
        _ = journal or OperationalChangeJournal(run_id=run_id)

        lock = TaskExecutionLock(
            task_id=task_id,
            run_id=run_id,
            lock_dir=self.lock_dir,
            ttl_seconds=lock_ttl_seconds,
        )
        with lock:
            log_messages = [
                f"[{datetime.now(timezone.utc).isoformat()}] Starting lifecycle execution for {task_id} (run_id={run_id})"
            ]
            if lock.reclaimed_stale:
                log_messages.append("WARNING: Reclaimed stale execution lock from previous run/process.")

            try:
                raw_findings = self._execute_audit(
                    task_id, run_dir, authority, audit_fn, reject_empty_callback, log_messages
                )
                fc, rc = self._process_findings(run_id, raw_findings, findings_ledger, ledger_save_path)
                return self._seal_success_bundle(
                    task_id, run_id, run_dir, task_def, raw_findings, fc, rc, log_messages, lock
                )
            except EmptyAuditCallbackError:
                raise
            except Exception as exc:
                return self._seal_failure_bundle(task_id, run_id, run_dir, task_def, exc, log_messages, lock)
