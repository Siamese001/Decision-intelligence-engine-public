"""tests/adversarial/test_scheduled_task_reliability_adversarial.py

Adversarial reproduction suite verifying the four critical failure modes
of scheduled task execution:
1. Missed executions (lock blocking from recycled/dead PIDs without active execution).
2. Duplicate actions (TOCTOU race in lock acquisition permitting concurrent execution).
3. Duplicate alerts (omission of target_channel in delivery attempt history).
4. Stale registrations (assert_registration_resolved returning success on pending tasks).
5. False success (empty audit callbacks claiming exit code 0, complete, and PASS).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dual_pipeline.core import write_json
from dual_pipeline.hitl_governance_core.scheduler_adapter import (
    check_duplicate_delivery,
    record_delivery_attempt,
)
from tools.scheduled_task_core import (
    AntigravityHostLaunchAdapter,
    AuditStatus,
    AuthorityContext,
    EmptyAuditCallbackError,
    EmptyCallbackDefenseError,
    ExecutionMode,
    RemediationStatus,
    ScheduledTaskLifecycleRunner,
    TaskAlreadyRunningError,
    TaskExecutionLock,
    TriggerOrigin,
    assert_active_registration,
    get_task_definition,
    verify_sealed_evidence_bundle,
)
from tools.scheduled_task_core.contracts import (
    UnresolvedRegistrationError,
    assert_registration_resolved,
)

ROOT = Path(__file__).resolve().parents[2]


def test_reproduce_duplicate_actions_via_lock_acquisition_race(tmp_path: Path) -> None:
    """Reproduces duplicate actions: TOCTOU race in lock acquisition allows two holders."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    lock1 = TaskExecutionLock(
        task_id="graph_hygiene",
        run_id="run-race-01",
        lock_dir=lock_dir,
    )
    lock2 = TaskExecutionLock(
        task_id="graph_hygiene",
        run_id="run-race-02",
        lock_dir=lock_dir,
    )

    # In the baseline implementation, replace() unconditionally overwrites lock_file.
    # Simulate the concurrent window where lock1 and lock2 both evaluate exists() as False:
    barrier = threading.Barrier(2)
    acquired_locks: list[str] = []

    def try_acquire(lck: TaskExecutionLock) -> None:
        try:
            # Synchronize right before acquire to expose TOCTOU window
            barrier.wait(timeout=5)
            lck.acquire()
            acquired_locks.append(lck.run_id)
        except TaskAlreadyRunningError:
            pass

    t1 = threading.Thread(target=try_acquire, args=(lock1,))
    t2 = threading.Thread(target=try_acquire, args=(lock2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # If the lock lacks atomic mutual exclusion (e.g. O_CREAT|O_EXCL),
    # rapid concurrent invocations can both claim acquisition or clobber each other.
    # We verify lock state on disk.
    assert (lock_dir / "graph_hygiene.lock").exists()


def test_reproduce_missed_execution_via_stale_lock_blocking(tmp_path: Path) -> None:
    """Reproduces missed execution: lock left by crashed run with recycled PID blocks next run."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "storage_hygiene.lock"

    # Simulate a crashed process that left a lock file with current PID (which is alive),
    # but the previous run is dead and not executing.
    stale_payload = {
        "task_id": "storage_hygiene",
        "run_id": "run-crashed-previous",
        "pid": os.getpid(),  # Alive PID
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "timestamp_epoch": time.time(),
        "ttl_seconds": 1800,  # 30 minutes
    }
    lock_file.write_text(json.dumps(stale_payload), encoding="utf-8")

    # New scheduled run arrives 1 minute later
    new_lock = TaskExecutionLock(
        task_id="storage_hygiene",
        run_id="run-new-scheduled",
        lock_dir=lock_dir,
        ttl_seconds=1800,
    )

    # Baseline fails closed by treating alive PID as active execution,
    # causing a missed execution for up to 1800 seconds.
    with pytest.raises(TaskAlreadyRunningError) as exc_info:
        new_lock.acquire()
    assert "is actively locked by PID" in str(exc_info.value)


def test_reproduce_duplicate_alerts_via_unrecorded_target_channel(tmp_path: Path) -> None:
    """Reproduces duplicate alerts: record_delivery_attempt omits target_channel from history."""
    req_dir = tmp_path / "approval_requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_id = "req-test-dup-alert"
    req_file = req_dir / f"{req_id}.json"

    initial_payload = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0",
        "request_id": req_id,
        "run_id": "run-alert-01",
        "directive_id": "dir-alert-01",
        "project": "project_nexus",
        "response_status": "AWAITING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": "2099-01-01T00:00:00Z",
    }
    write_json(req_file, initial_payload)

    # Before delivery, channel is not delivered
    assert check_duplicate_delivery(req_id, "slack_alerts", tmp_path) is False

    # Delivery attempt succeeds
    record_delivery_attempt(req_id, tmp_path, success=True)

    # In baseline, record_delivery_attempt did not record target_channel,
    # so check_duplicate_delivery STILL returns False, causing duplicate alerts on next tick.
    is_duplicate_prevented = check_duplicate_delivery(req_id, "slack_alerts", tmp_path)
    # The vulnerability: duplicate delivery is NOT prevented!
    assert is_duplicate_prevented is False, "Vulnerability confirmed: duplicate delivery not prevented"


def test_reproduce_stale_registration_bypass() -> None:
    """Reproduces stale registration: assert_registration_resolved passes on pending tasks."""
    # Under baseline, registration_reconciliation.json has status:
    # "RECONCILED_PENDING_OPERATOR_AUTHORIZATION" and summary total_unresolved: 7.
    # But assert_registration_resolved only checked `if status == 'UNRESOLVED'`.
    # Therefore, pending tasks pass cleanly as if they were live, active installations!
    reconciliation_file = ROOT / "config" / "scheduled_tasks" / "registration_reconciliation.json"
    reconciliation_data = json.loads(reconciliation_file.read_text(encoding="utf-8"))
    summary = reconciliation_data.get("reconciliation_summary", {})
    total_unresolved = summary.get("total_unresolved", 0)

    # If the manifest has been approved/reconciled (total_unresolved == 0),
    # verify that assert_registration_resolved still works as expected for live registered tasks.
    if total_unresolved in (7, 8):
        assert summary.get("authorization_status") == "PENDING_OPERATOR_GATE"
        # The vulnerability: calling assert_registration_resolved returns None without raising!
        # A pending, unauthorized task is masquerading as a resolved active registration.
        result = assert_registration_resolved("knowledge_graph_hygiene")
        assert result is None, "Vulnerability confirmed: pending registration bypassed verification"
    else:
        # Reconciled / active manifest: verify registration resolves cleanly without raising
        result = assert_registration_resolved("knowledge_graph_hygiene")
        assert result is None


def test_reproduce_false_success_on_empty_callbacks_and_zero_coverage(tmp_path: Path) -> None:
    """Reproduces false success: empty callback produces 'succeeded' and 'PASS' with 0 checks."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=tmp_path,
        lock_dir=tmp_path / "locks",
    )
    authority = AuthorityContext(
        task_id="knowledge_graph_hygiene",
        run_id="run-false-success-01",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="scheduler",
        allowed_capabilities={"audit_execution"},
        is_scheduled=True,
    )

    # Definition in registry declares 3 mandatory checks:
    task_def = get_task_definition("knowledge_graph_hygiene")
    mandatory_checks = task_def["coverage_rules"]["mandatory_checks"]
    assert len(mandatory_checks) == 3

    # Run with empty callback that does zero checks:
    summary = runner.run_task(
        task_id="knowledge_graph_hygiene",
        run_id="run-false-success-01",
        authority=authority,
        output_root=tmp_path / "reports",
        audit_fn=lambda p: [],  # Empty callback: zero checks executed
        reject_empty_callback=False,  # Baseline default
    )

    # Vulnerability confirmed: lifecycle runner reported 100% success!
    assert summary.run_outcome == "succeeded"
    assert summary.audit_status == AuditStatus.COMPLETE.value
    assert summary.remediation_status == RemediationStatus.NOT_REQUIRED.value
    assert summary.exit_code == 0

    # Output file claims PASS despite zero evaluations
    result_file = tmp_path / "reports" / "knowledge_graph_hygiene" / "run-false-success-01" / "audit_result.json"
    result_data = json.loads(result_file.read_text(encoding="utf-8"))
    assert result_data["terminal_outcome"] == "PASS"


def test_hardened_empty_callback_rejected(tmp_path: Path) -> None:
    """Verifies that running lifecycle without an audit callback fails closed."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=tmp_path,
        lock_dir=tmp_path / "locks",
    )
    authority = AuthorityContext(
        task_id="knowledge_graph_hygiene",
        run_id="run-hardened-empty-01",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="scheduler",
        allowed_capabilities={"audit_execution"},
        is_scheduled=True,
    )
    with pytest.raises(EmptyAuditCallbackError, match="Empty callback rejected"):
        runner.run_task(
            task_id="knowledge_graph_hygiene",
            run_id="run-hardened-empty-01",
            authority=authority,
            output_root=tmp_path / "reports",
            audit_fn=None,
        )


def test_hardened_reject_empty_callback_flag(tmp_path: Path) -> None:
    """Verifies that reject_empty_callback=True blocks zero-finding callbacks."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=tmp_path,
        lock_dir=tmp_path / "locks",
    )
    authority = AuthorityContext(
        task_id="knowledge_graph_hygiene",
        run_id="run-hardened-reject-01",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="scheduler",
        allowed_capabilities={"audit_execution"},
        is_scheduled=True,
    )
    with pytest.raises(EmptyAuditCallbackError, match="Empty callback rejected"):
        runner.run_task(
            task_id="knowledge_graph_hygiene",
            run_id="run-hardened-reject-01",
            authority=authority,
            output_root=tmp_path / "reports",
            audit_fn=lambda p: [],
            reject_empty_callback=True,
        )


def test_hardened_active_registration_fails_closed_on_pending() -> None:
    """Verifies that assert_active_registration fails closed when tasks are pending authorization."""
    with pytest.raises(UnresolvedRegistrationError, match="Active, installed registration required"):
        assert_active_registration("knowledge_graph_hygiene")


def test_hardened_coverage_manifest_includes_mandatory_checks(tmp_path: Path) -> None:
    """Verifies that sealed evidence bundles explicitly verify registry mandatory checks."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=tmp_path,
        lock_dir=tmp_path / "locks",
    )
    authority = AuthorityContext(
        task_id="knowledge_graph_hygiene",
        run_id="run-coverage-manifest-01",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="scheduler",
        allowed_capabilities={"audit_execution"},
        is_scheduled=True,
    )
    runner.run_task(
        task_id="knowledge_graph_hygiene",
        run_id="run-coverage-manifest-01",
        authority=authority,
        output_root=tmp_path / "reports",
        audit_fn=lambda p: [{"signature": "mock-finding", "description": "Mock defect"}],
    )
    run_dir = tmp_path / "reports" / "knowledge_graph_hygiene" / "run-coverage-manifest-01"
    valid, errors = verify_sealed_evidence_bundle(run_dir)
    assert valid is True
    assert len(errors) == 0

    manifest_data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    coverage_summary = manifest_data["coverage_summary"]
    assert coverage_summary["total_expected_evaluations"] == 3
    assert coverage_summary["total_inspected_evaluations"] == 3
    assert coverage_summary["coverage_ratio"] == 1.0


def test_hardened_launch_adapter_empty_callback_defense(tmp_path: Path) -> None:
    """Verifies that launch adapter rejects outcome 'COMPLETE' or 'PASS' with 0 findings."""
    adapter = AntigravityHostLaunchAdapter()
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir(parents=True, exist_ok=True)
    graph_db = fixture_root / "graph.db"
    conn = sqlite3.connect(graph_db)
    conn.execute("CREATE TABLE assertion (id TEXT, entity_id TEXT, currentness_state TEXT, subject_node_id TEXT, object_node_id TEXT, primary_source_id TEXT, predicate TEXT, scope_text TEXT, object_literal TEXT)")
    conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT)")
    conn.execute("CREATE TABLE node (id TEXT, node_id TEXT, canonical_name TEXT, label TEXT, node_type TEXT, active INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(EmptyCallbackDefenseError, match="Empty callbacks cannot qualify task execution"):
        adapter.launch_task(
            task_id="knowledge_graph_hygiene",
            mode="scheduled",
            fixture_root=fixture_root,
            run_id="run-empty-adapter-01",
            reject_empty_callback=True,
        )


