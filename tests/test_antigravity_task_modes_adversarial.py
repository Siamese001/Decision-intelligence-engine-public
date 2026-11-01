"""tests/test_antigravity_task_modes_adversarial.py

Adversarial resilience, concurrency lock contention, input malformation,
protected-resource immutability, and export parity tests for Antigravity dual-mode tasks.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tools.generate_antigravity_task_exports import (
    TASK_EXPORT_MAP,
    generate_task_exports,
    verify_export_parity,
)
from tools.scheduled_task_core import (
    AntigravityHostLaunchAdapter,
    ApprovalRecord,
    EmptyAuditCallbackError,
    EmptyCallbackDefenseError,
    ExecutionMode,
    ItemSetDriftError,
    PlanDriftError,
    RestoreWithoutAuthorityError,
    ScheduledMutationBlockedError,
    ScheduledTaskLifecycleRunner,
    TaskAlreadyRunningError,
    TaskExecutionLock,
    TaskExecutionResult,
    TriggerOrigin,
    UnauthenticatedApprovalError,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def adversarial_env(tmp_path: Path):
    """Provides an isolated disposable environment with mock graphs, worktrees, and locks."""
    fixture_root = tmp_path / "adversarial_fixture"
    fixture_root.mkdir(parents=True, exist_ok=True)

    # Protected resource: mock canonical graph db
    graph_file = fixture_root / "CANONICAL_GRAPH.db"
    graph_file.write_text("MOCK_CANONICAL_GRAPH_SQLITE_BYTES_V1", encoding="utf-8")

    # Protected resource: production output directory
    output_dir = fixture_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    authoritative_doc = output_dir / "01_deliverable.md"
    authoritative_doc.write_text("# Authoritative Deliverable\nDo not modify.", encoding="utf-8")

    lock_dir = fixture_root / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    return {
        "fixture_root": fixture_root,
        "graph_file": graph_file,
        "authoritative_doc": authoritative_doc,
        "lock_dir": lock_dir,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -----------------------------------------------------------------------------
# 1. Missing & Malformed Inputs
# -----------------------------------------------------------------------------

def test_missing_and_malformed_inputs_adversarial(adversarial_env: dict[str, Any]) -> None:
    """Verifies that corrupt or missing inputs fail closed with typed errors."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = adversarial_env["fixture_root"]
    run_id = "test-malformed-input"

    # 1. Unknown / invalid task ID
    with pytest.raises(ValueError, match=r"(Unknown domain task|not one of governed tasks|not one of seven registered archive tasks)"):
        launcher.launch_task(
            task_id="non_existent_task_id",
            mode="scheduled",
            fixture_root=fixture_root,
            run_id=run_id,
        )

    # 2. Corrupted approval JSON (string that cannot be loaded as dict/Path)
    with pytest.raises(UnauthenticatedApprovalError):
        launcher.launch_task(
            task_id="storage_repository_hygiene",
            mode="interactive",
            fixture_root=fixture_root,
            run_id=run_id,
            approval_record=12345,  # Invalid type
        )

    # 3. Missing operator identity in approval dict
    bad_approval_missing_op = {
        "status": "APPROVED",
        "signature": "sig-123",
        "task_id": "storage_repository_hygiene",
    }
    with pytest.raises(UnauthenticatedApprovalError, match="missing operator identity"):
        launcher.launch_task(
            task_id="storage_repository_hygiene",
            mode="interactive",
            fixture_root=fixture_root,
            run_id=run_id,
            approval_record=bad_approval_missing_op,
        )

    # 4. Synthetic / auto operator rejected
    bad_approval_auto_op = {
        "operator": "auto",
        "status": "APPROVED",
        "signature": "sig-123",
        "task_id": "storage_repository_hygiene",
    }
    with pytest.raises(UnauthenticatedApprovalError, match="not an authentic human operator"):
        launcher.launch_task(
            task_id="storage_repository_hygiene",
            mode="interactive",
            fixture_root=fixture_root,
            run_id=run_id,
            approval_record=bad_approval_auto_op,
        )


# -----------------------------------------------------------------------------
# 2. Real Concurrent Launches
# -----------------------------------------------------------------------------

def test_real_concurrent_launches_lock_contention_adversarial(adversarial_env: dict[str, Any]) -> None:
    """Verifies that simultaneous task executions contend safely: one acquires lock, other fails closed."""
    import threading

    lock_dir = adversarial_env["lock_dir"]
    task_id = "knowledge_graph_hygiene"

    worker1_holding = threading.Event()
    worker2_done = threading.Event()
    results: list[tuple[str, str | None]] = []

    def _worker_1() -> None:
        lock = TaskExecutionLock(
            task_id=task_id,
            run_id="run-worker-1",
            lock_dir=lock_dir,
            ttl_seconds=30,
        )
        with lock:
            results.append(("worker-1", "ACQUIRED"))
            worker1_holding.set()
            worker2_done.wait(timeout=5)

    def _worker_2() -> None:
        worker1_holding.wait(timeout=5)
        lock = TaskExecutionLock(
            task_id=task_id,
            run_id="run-worker-2",
            lock_dir=lock_dir,
            ttl_seconds=30,
        )
        try:
            with lock:
                results.append(("worker-2", "ACQUIRED"))
        except TaskAlreadyRunningError as exc:
            results.append(("worker-2", f"LOCKED: {exc}"))
        finally:
            worker2_done.set()

    # Spawn two concurrent threads attempting to run the same task
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_worker_1)
        f2 = executor.submit(_worker_2)
        f1.result()
        f2.result()

    outcomes = [r[1] for r in results]
    assert "ACQUIRED" in outcomes, "At least one worker must acquire lock"
    assert any("LOCKED" in str(o) for o in outcomes), "Contending worker must encounter TaskAlreadyRunningError"


# -----------------------------------------------------------------------------
# 3. Interruption and Recovery
# -----------------------------------------------------------------------------

def test_interrupted_execution_and_restore_adversarial(adversarial_env: dict[str, Any], tmp_path: Path) -> None:
    """Verifies that an interrupted or faulted run produces a durable failure receipt and releases lock."""
    runner = ScheduledTaskLifecycleRunner(
        root_dir=tmp_path,
        lock_dir=tmp_path / "locks",
    )
    launcher = AntigravityHostLaunchAdapter()
    auth = launcher.build_authority_context(
        task_id="knowledge_graph_hygiene",
        run_id="run-fault-inject",
        mode="scheduled",
    )

    def _exploding_audit(run_dir: Path):
        raise RuntimeError("SIMULATED_PROCESS_CRASH_OR_INTERRUPTION")

    # Run should catch exception and record durable failure receipt
    summary = runner.run_task(
        task_id="knowledge_graph_hygiene",
        run_id="run-fault-inject",
        authority=auth,
        output_root=tmp_path / "output",
        audit_fn=_exploding_audit,
    )

    assert summary.run_outcome == "failed"
    assert summary.exit_code == 1
    assert "SIMULATED_PROCESS_CRASH" in summary.error_message

    fail_receipt = tmp_path / "output" / "knowledge_graph_hygiene" / "run-fault-inject" / "failure_receipt.json"
    assert fail_receipt.is_file(), "Durable failure receipt must be preserved"
    data = json.loads(fail_receipt.read_text(encoding="utf-8"))
    assert data["terminal_outcome"] == "FAIL"

    # Verify lock was cleanly released
    lock_file = tmp_path / "locks" / "knowledge_graph_hygiene.lock"
    assert not lock_file.exists(), "Lock must be released upon failure receipt sealing"


# -----------------------------------------------------------------------------
# 4. Repeated Approval Waits
# -----------------------------------------------------------------------------

def test_repeated_approval_waits_idempotency_adversarial(adversarial_env: dict[str, Any]) -> None:
    """Verifies that consecutive scheduled runs waiting for approval remain safely pending without duplicate requests."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = adversarial_env["fixture_root"]
    run_id = "test-wmi-idempotent-wait"

    # Run 1: scheduled execution persists approval request
    res1 = launcher.launch_task(
        task_id="weekly_market_intelligence",
        mode="scheduled",
        fixture_root=fixture_root,
        run_id=run_id,
    )
    assert res1.terminal_outcome == "AWAITING_APPROVAL"

    req_dir = fixture_root / "approval_requests"
    request_files_before = list(req_dir.glob("*.json"))
    assert len(request_files_before) == 1

    # Run 2: repeated scheduled run under same run ID re-checks without creating duplicate files
    res2 = launcher.launch_task(
        task_id="weekly_market_intelligence",
        mode="scheduled",
        fixture_root=fixture_root,
        run_id=run_id,
    )
    assert res2.terminal_outcome == "AWAITING_APPROVAL"
    request_files_after = list(req_dir.glob("*.json"))
    assert len(request_files_after) == 1, "Approval requests must not duplicate on repeated runs"
    assert res2.mutations_count == 0, "Repeated wait must produce zero mutations"


# -----------------------------------------------------------------------------
# 5. Quiet Repeated Runs
# -----------------------------------------------------------------------------

def test_quiet_repeated_runs_adversarial(adversarial_env: dict[str, Any]) -> None:
    """Verifies that runs with unchanged inputs produce quiet outcomes and deduplicate findings."""
    launcher = AntigravityHostLaunchAdapter()
    fixture_root = adversarial_env["fixture_root"]

    # PR queue observer run 1 detects initial change / records snapshot
    res1 = launcher.launch_task(
        task_id="pr_queue_observer",
        mode="scheduled",
        fixture_root=fixture_root,
        run_id="run-quiet-1",
    )
    assert res1.terminal_outcome == "NOTIFICATION_EMITTED"
    assert res1.findings_count == 1

    # PR queue observer run 2 on empty queue (repeated unchanged run)
    res2 = launcher.launch_task(
        task_id="pr_queue_observer",
        mode="scheduled",
        fixture_root=fixture_root,
        run_id="run-quiet-2",
    )
    assert res2.terminal_outcome == "NO_NOTIFICATION"
    assert res2.findings_count == 0
    assert res2.mutations_count == 0


# -----------------------------------------------------------------------------
# 6. Protected-Resource Immutability
# -----------------------------------------------------------------------------

def test_protected_resource_immutability_adversarial(adversarial_env: dict[str, Any]) -> None:
    """Verifies that protected canonical resources remain strictly untouched across failed or denied executions."""
    graph_file = adversarial_env["graph_file"]
    authoritative_doc = adversarial_env["authoritative_doc"]
    fixture_root = adversarial_env["fixture_root"]

    graph_sha_before = _sha256(graph_file)
    doc_sha_before = _sha256(authoritative_doc)

    launcher = AntigravityHostLaunchAdapter()

    # Attempt unauthorized / denied operations across multiple tasks
    with pytest.raises(ItemSetDriftError):
        bad_approval = ApprovalRecord(
            approval_id="app-tamper",
            task_id="storage_repository_hygiene",
            run_id="run-tamper",
            operator="Human Operator",
            signature="valid-sig",
            status="APPROVED",
            approved_items=["rogue_item.txt"],
            plan_digest="bad-digest",
            baseline_sha="bad-sha",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at="2099-01-01T00:00:00Z",
            replay_nonce="nonce-tamper-1",
        )
        launcher.launch_task(
            task_id="storage_repository_hygiene",
            mode="interactive",
            fixture_root=fixture_root,
            run_id="run-tamper",
            approval_record=bad_approval,
        )

    # Verify protected assets were NOT touched
    graph_sha_after = _sha256(graph_file)
    doc_sha_after = _sha256(authoritative_doc)

    assert graph_sha_before == graph_sha_after, "CANONICAL_GRAPH.db was mutated during failed execution!"
    assert doc_sha_before == doc_sha_after, "Authoritative 01_* deliverable was modified!"


# -----------------------------------------------------------------------------
# 7. Export Bodies Parity and Tamper Detection
# -----------------------------------------------------------------------------

def test_export_bodies_parity_and_tamper_detection_adversarial(tmp_path: Path) -> None:
    """Verifies canonical prompt SSOT, duplicate export rejection, and absence of obsolete runner bindings."""
    # 1. Real repository SSOT check must pass (zero duplicate exports)
    ok, errors = verify_export_parity(repo_root=ROOT)
    assert ok, f"Export parity check failed: {errors}"

    # 2. Check each canonical prompt has no obsolete runner binding
    for task_id, mapping in TASK_EXPORT_MAP.items():
        canon_file = ROOT / mapping["canonical_prompt"]
        assert canon_file.is_file()
        content = canon_file.read_text(encoding="utf-8")
        assert "tools.verify_scheduled_task_wave" not in content, (
            f"Canonical prompt for '{task_id}' contains obsolete test verifier binding."
        )

    # 3. Simulate introducing a rogue duplicate export file: verify_export_parity must fail closed
    sandbox = tmp_path / "sandbox_repo"
    shutil.copytree(ROOT / "automations", sandbox / "automations")
    tampered_dir = sandbox / "artifacts" / "exports" / "scheduled_tasks_hardened"
    tampered_dir.mkdir(parents=True, exist_ok=True)
    tampered_file = tampered_dir / "knowledge_graph_hygiene_scheduled_task.md"
    tampered_file.write_text("ROGUE_DUPLICATE_EXPORT_FILE", encoding="utf-8")

    tamper_ok, tamper_errors = verify_export_parity(repo_root=sandbox)
    assert not tamper_ok, "verify_export_parity must fail when a duplicate scheduled task export is present"
    assert any("parity mismatch" in e for e in tamper_errors)
