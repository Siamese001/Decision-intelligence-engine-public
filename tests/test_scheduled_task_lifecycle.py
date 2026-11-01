"""tests/test_scheduled_task_lifecycle.py

Repeat execution, concurrency locking, and lifecycle qualification tests for Wave 6.
Verifies exclusive locks, stale PID/TTL reclamation, finding deduplication across repeat
runs, durable failure receipts on interruption, and bundle tamper detection.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tools.scheduled_task_core import (
    AuditStatus,
    AuthorityContext,
    DurableFindingsLedger,
    ExecutionMode,
    OperationalCategory,
    OperationalChangeJournal,
    RemediationStatus,
    SEVEN_ARCHIVE_TASK_IDS,
    ScheduledTaskLifecycleRunner,
    TamperedArtifactError,
    TaskAlreadyRunningError,
    TaskExecutionLock,
    TriggerOrigin,
    verify_sealed_evidence_bundle,
)


@pytest.fixture
def temp_env(tmp_path: Path):
    """Provides disposable directories for locks, outputs, and ledgers."""
    lock_dir = tmp_path / "locks"
    output_root = tmp_path / "reports"
    ledger_file = tmp_path / "findings_ledger.json"
    journal_file = tmp_path / "operational_journal.json"
    return {
        "lock_dir": lock_dir,
        "output_root": output_root,
        "ledger_file": ledger_file,
        "journal_file": journal_file,
    }


def test_task_execution_lock_clean_lifecycle(temp_env: dict[str, Path]) -> None:
    """Verifies atomic acquisition, metadata writing, and clean release of task locks."""
    lock_dir = temp_env["lock_dir"]
    lock = TaskExecutionLock(
        task_id="pr_queue_observer",
        run_id="run-001",
        lock_dir=lock_dir,
    )
    with lock:
        assert lock.acquired is True
        assert lock.lock_file is not None and lock.lock_file.exists()
        data = json.loads(lock.lock_file.read_text(encoding="utf-8"))
        assert data["task_id"] == "pr_queue_observer"
        assert data["run_id"] == "run-001"
        assert data["pid"] == os.getpid()

    assert lock.acquired is False
    assert not lock.lock_file.exists()


def test_task_execution_lock_concurrency_race(temp_env: dict[str, Path]) -> None:
    """Verifies that a concurrent execution with a live process is safely blocked."""
    lock_dir = temp_env["lock_dir"]
    lock1 = TaskExecutionLock(
        task_id="graph_hygiene",
        run_id="run-101",
        lock_dir=lock_dir,
    )
    lock2 = TaskExecutionLock(
        task_id="graph_hygiene",
        run_id="run-102",
        lock_dir=lock_dir,
    )

    with lock1:
        with pytest.raises(TaskAlreadyRunningError) as exc_info:
            lock2.acquire()
        assert "is actively locked by PID" in str(exc_info.value)

    # After lock1 releases, lock2 can acquire cleanly
    with lock2:
        assert lock2.acquired is True
        assert lock2.lock_file.exists()


def test_task_execution_lock_stale_expired_reclamation(temp_env: dict[str, Path]) -> None:
    """Verifies that an expired lock (age > TTL) is reclaimed automatically."""
    lock_dir = temp_env["lock_dir"]
    lock_dir.mkdir(parents=True, exist_ok=True)
    stale_file = lock_dir / "storage_hygiene.lock"
    # Write a lock file from 3600 seconds ago
    stale_payload = {
        "task_id": "storage_hygiene",
        "run_id": "run-old",
        "pid": os.getpid(),
        "timestamp_iso": "2026-10-06T12:00:00Z",
        "timestamp_epoch": time.time() - 3600,
        "ttl_seconds": 1800,
    }
    stale_file.write_text(json.dumps(stale_payload), encoding="utf-8")

    new_lock = TaskExecutionLock(
        task_id="storage_hygiene",
        run_id="run-new",
        lock_dir=lock_dir,
        ttl_seconds=1800,
    )
    with new_lock:
        assert new_lock.acquired is True
        assert new_lock.reclaimed_stale is True
        data = json.loads(stale_file.read_text(encoding="utf-8"))
        assert data["run_id"] == "run-new"


def test_task_execution_lock_dead_pid_reclamation(temp_env: dict[str, Path]) -> None:
    """Verifies that a lock held by a dead process PID is reclaimed."""
    lock_dir = temp_env["lock_dir"]
    lock_dir.mkdir(parents=True, exist_ok=True)
    stale_file = lock_dir / "weekly_market_intelligence.lock"
    # Write a lock file with dead PID
    dead_pid = 99999999
    stale_payload = {
        "task_id": "weekly_market_intelligence",
        "run_id": "run-dead",
        "pid": dead_pid,
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "timestamp_epoch": time.time(),
        "ttl_seconds": 3600,
    }
    stale_file.write_text(json.dumps(stale_payload), encoding="utf-8")

    new_lock = TaskExecutionLock(
        task_id="weekly_market_intelligence",
        run_id="run-live",
        lock_dir=lock_dir,
        ttl_seconds=3600,
    )
    with new_lock:
        assert new_lock.acquired is True
        assert new_lock.reclaimed_stale is True


def _make_auth(task_id: str, run_id: str, capabilities: set[str] | None = None) -> AuthorityContext:
    return AuthorityContext(
        task_id=task_id,
        run_id=run_id,
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="system_scheduler",
        allowed_capabilities=capabilities or {"audit_execution"},
        is_scheduled=True,
    )


def test_lifecycle_runner_clean_run(temp_env: dict[str, Path]) -> None:
    """Verifies full lifecycle execution with zero findings producing sealed bundle."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=temp_env["lock_dir"].parent,
        lock_dir=temp_env["lock_dir"],
    )
    ledger = DurableFindingsLedger(task_id="worktree_coverage_cleanup")
    journal = OperationalChangeJournal(temp_env["journal_file"])

    auth = _make_auth("worktree_coverage_cleanup", "run-clean-01", {"read_worktree_metadata", "audit_execution"})

    def mock_audit(run_dir: Path) -> list[dict[str, Any]]:
        return []

    summary = runner.run_task(
        task_id="worktree_coverage_cleanup",
        run_id="run-clean-01",
        authority=auth,
        output_root=temp_env["output_root"],
        findings_ledger=ledger,
        ledger_save_path=temp_env["ledger_file"],
        journal=journal,
        audit_fn=mock_audit,
    )

    assert summary.run_outcome == "succeeded"
    assert summary.audit_status == AuditStatus.COMPLETE.value
    assert summary.remediation_status == RemediationStatus.NOT_REQUIRED.value
    assert summary.findings_count == 0

    run_dir = temp_env["output_root"] / "worktree_coverage_cleanup" / "run-clean-01"
    valid, errors = verify_sealed_evidence_bundle(run_dir)
    assert valid is True
    assert len(errors) == 0
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_digest"] == summary.bundle_manifest_digest


def test_lifecycle_runner_repeat_deduplication(temp_env: dict[str, Path]) -> None:
    """Verifies that repeat runs against the same finding increment recurrence count without duplicating."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=temp_env["lock_dir"].parent,
        lock_dir=temp_env["lock_dir"],
    )
    ledger = DurableFindingsLedger(task_id="repository_antipattern_audit")
    journal = OperationalChangeJournal(temp_env["journal_file"])

    persistent_finding = {
        "signature": "repo-antipattern-f01",
        "description": "Unbounded module line count",
    }

    def mock_audit(run_dir: Path) -> list[dict[str, Any]]:
        return [persistent_finding]

    # Run 1: First discovery
    auth1 = _make_auth("repository_antipattern_audit", "run-rep-01")
    sum1 = runner.run_task(
        task_id="repository_antipattern_audit",
        run_id="run-rep-01",
        authority=auth1,
        output_root=temp_env["output_root"],
        findings_ledger=ledger,
        ledger_save_path=temp_env["ledger_file"],
        journal=journal,
        audit_fn=mock_audit,
    )
    assert sum1.findings_count == 1
    assert sum1.recurrence_count == 0

    # Run 2: Repeat execution (loading prior ledger from disk)
    auth2 = _make_auth("repository_antipattern_audit", "run-rep-02")
    ledger2 = DurableFindingsLedger.load(temp_env["ledger_file"])
    sum2 = runner.run_task(
        task_id="repository_antipattern_audit",
        run_id="run-rep-02",
        authority=auth2,
        output_root=temp_env["output_root"],
        findings_ledger=ledger2,
        ledger_save_path=temp_env["ledger_file"],
        journal=journal,
        audit_fn=mock_audit,
    )
    assert sum2.findings_count == 0
    assert sum2.recurrence_count == 1

    # Verify ledger state
    saved_ledger = DurableFindingsLedger.load(temp_env["ledger_file"])
    fid = DurableFindingsLedger.compute_finding_id("repository_antipattern_audit", "repo-antipattern-f01")
    f = saved_ledger.findings.get(fid)
    assert f is not None
    assert f.occurrence_count == 2


def test_lifecycle_runner_interruption_failure_receipt(temp_env: dict[str, Path]) -> None:
    """Verifies that an unhandled fault produces a durable sealed failure receipt."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=temp_env["lock_dir"].parent,
        lock_dir=temp_env["lock_dir"],
    )
    ledger = DurableFindingsLedger(task_id="ssot_drift_remediation")
    journal = OperationalChangeJournal(temp_env["journal_file"])

    auth = _make_auth("ssot_drift_remediation", "run-fault-01")

    def failing_audit(run_dir: Path) -> list[dict[str, Any]]:
        raise RuntimeError("Simulated unhandled pipeline interruption")

    summary = runner.run_task(
        task_id="ssot_drift_remediation",
        run_id="run-fault-01",
        authority=auth,
        output_root=temp_env["output_root"],
        findings_ledger=ledger,
        ledger_save_path=temp_env["ledger_file"],
        journal=journal,
        audit_fn=failing_audit,
    )

    assert summary.run_outcome == "failed"
    assert summary.audit_status == AuditStatus.PARTIAL.value
    assert summary.exit_code == 1
    assert "Simulated unhandled pipeline interruption" in str(summary.error_message)

    run_dir = temp_env["output_root"] / "ssot_drift_remediation" / "run-fault-01"
    valid, errors = verify_sealed_evidence_bundle(run_dir)
    assert valid is True
    assert len(errors) == 0
    assert (run_dir / "failure_receipt.json").exists()


def test_lifecycle_runner_tamper_detection(temp_env: dict[str, Path]) -> None:
    """Verifies that tampering with any bundle artifact fails verification closed."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=temp_env["lock_dir"].parent,
        lock_dir=temp_env["lock_dir"],
    )
    ledger = DurableFindingsLedger(task_id="pr_queue_observer")
    journal = OperationalChangeJournal(temp_env["journal_file"])

    auth = _make_auth("pr_queue_observer", "run-tamper-01")

    summary = runner.run_task(
        task_id="pr_queue_observer",
        run_id="run-tamper-01",
        authority=auth,
        output_root=temp_env["output_root"],
        findings_ledger=ledger,
        ledger_save_path=temp_env["ledger_file"],
        journal=journal,
        audit_fn=lambda p: [],
    )

    run_dir = temp_env["output_root"] / "pr_queue_observer" / "run-tamper-01"
    # Tamper with markdown report
    md_file = run_dir / "audit_report.md"
    original_text = md_file.read_text(encoding="utf-8")
    md_file.write_text(original_text + "\n<!-- unauthorized modification -->", encoding="utf-8")

    valid, errors = verify_sealed_evidence_bundle(run_dir)
    assert valid is False
    assert any("hash mismatch" in err or "TAMPERED" in err for err in errors)


@pytest.mark.parametrize("task_id", sorted(list(SEVEN_ARCHIVE_TASK_IDS)))
def test_all_seven_tasks_lifecycle_qualification(temp_env: dict[str, Path], task_id: str) -> None:
    """Verifies that all seven archive tasks qualify under repeat execution and clean isolation."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=temp_env["lock_dir"].parent,
        lock_dir=temp_env["lock_dir"],
    )
    ledger = DurableFindingsLedger(task_id=task_id)
    journal = OperationalChangeJournal(temp_env["journal_file"])

    run_id = f"run-qual-{task_id}"
    auth = _make_auth(task_id, run_id)

    summary = runner.run_task(
        task_id=task_id,
        run_id=run_id,
        authority=auth,
        output_root=temp_env["output_root"],
        findings_ledger=ledger,
        ledger_save_path=temp_env["ledger_file"],
        journal=journal,
        audit_fn=lambda p: [],
    )

    assert summary.run_outcome == "succeeded"
    assert summary.exit_code == 0
    run_dir = temp_env["output_root"] / task_id / run_id
    valid, errors = verify_sealed_evidence_bundle(run_dir)
    assert valid is True
    assert len(errors) == 0
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_digest"] == summary.bundle_manifest_digest
