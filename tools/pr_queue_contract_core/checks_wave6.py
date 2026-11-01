"""tools/pr_queue_contract_core/checks_wave6.py

Wave 6 verification checks for the PR Queue Durable Prioritization Plan.
Covers Antigravity observer qualification: single-run file locking, isolated report destinations,
immutable run identities, notification deduplication, sleeping-host resilience, shadow scenarios,
and prompt/runbook operational artifacts.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]


def check_observer_single_run_locking_and_overlap_detection() -> Dict[str, Any]:
    name = "observer_single_run_locking_and_overlap"
    try:
        from tools.pr_governance_core.observer import (
            ObserverLockCollisionError,
            acquire_observer_lock,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "test_observer.lock"

            with acquire_observer_lock(lock_path) as info:
                assert lock_path.exists(), "Lockfile was not created on disk"
                lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
                assert lock_data["pid"] == os.getpid()

                # Attempting to acquire again with active PID must raise collision error
                collision_detected = False
                try:
                    with acquire_observer_lock(lock_path):
                        pass
                except ObserverLockCollisionError:
                    collision_detected = True

                assert collision_detected, "Active lock collision did not raise ObserverLockCollisionError"

            # Lock should be cleanly removed upon exit
            assert not lock_path.exists(), "Lockfile was not removed upon normal context manager exit"

            # Test stale lock reclamation (fake dead PID)
            stale_info = {
                "pid": 99999999,
                "acquired_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                "hostname": "test-host",
                "lock_file": str(lock_path),
            }
            lock_path.write_text(json.dumps(stale_info), encoding="utf-8")

            with acquire_observer_lock(lock_path) as info2:
                assert info2.pid == os.getpid()

        return {"name": name, "status": "PASS", "details": "Lock acquisition, active collision rejection, and stale reclamation verified"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_observer_immutable_run_identity_and_isolated_output() -> Dict[str, Any]:
    name = "observer_run_identity_and_isolation"
    try:
        from tools.pr_governance_core.observer import run_observation
        from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
            snap = QueueSnapshot(
                observed_at=datetime.now(timezone.utc).isoformat(),
                branches=[
                    BranchSnapshot(branch="feat/test-alpha", head_sha="aaaa", ahead=1, behind=0, completed_waves="1"),
                ],
            )

            receipt = run_observation(repo_root=ROOT, output_dir=out_dir, snapshot=snap)

            assert receipt.run_id, "Run ID was not generated"
            assert "_" in receipt.run_id, "Run ID must format as {timestamp}_{snapshot_hash[:8]}"
            assert receipt.snapshot_digest, "Snapshot digest was not computed"

            receipt_file = out_dir / "runs" / f"{receipt.run_id}.json"
            assert receipt_file.exists(), f"Run receipt not found at {receipt_file}"

            last_accepted_file = out_dir / "last_accepted_snapshot.json"
            assert last_accepted_file.exists(), "last_accepted_snapshot.json pointer not updated"
            last_data = json.loads(last_accepted_file.read_text(encoding="utf-8"))
            assert last_data["snapshot_digest"] == receipt.snapshot_digest
            assert last_data["run_id"] == receipt.run_id

        return {"name": name, "status": "PASS", "details": "Run identity formatting, isolated runs destination, and atomic last-accepted pointer verified"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_observer_notification_deduplication_and_repeated_error_suppression() -> Dict[str, Any]:
    name = "observer_notification_dedup"
    try:
        from tools.pr_governance_core.observer import run_observation
        from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
            snap1 = QueueSnapshot(
                observed_at=datetime.now(timezone.utc).isoformat(),
                branches=[
                    BranchSnapshot(branch="feat/gov-alpha", head_sha="1111", ahead=2, behind=0, completed_waves="1"),
                ],
            )
            # Run 1: initial baseline -> notify
            r1 = run_observation(repo_root=ROOT, output_dir=out_dir, snapshot=snap1)
            assert r1.assessment.notify is True

            # Run 2: identical state -> suppress notification (quiet)
            r2 = run_observation(repo_root=ROOT, output_dir=out_dir, snapshot=snap1)
            assert r2.assessment.notify is False, "Identical state did not suppress notification"
            assert r2.assessment.terminal_outcome == "NO_NOTIFICATION"

            # Run 3: deliberate collection fault -> alert
            snap_err = QueueSnapshot(
                observed_at=datetime.now(timezone.utc).isoformat(),
                completeness_status="INCONSISTENT",
                errors=["Concurrent lock timeout"],
            )
            r3 = run_observation(repo_root=ROOT, output_dir=out_dir, snapshot=snap_err)
            assert r3.assessment.notify is True, "Collection error did not alert"
            assert r3.assessment.terminal_outcome == "COLLECTION_FAILURE_ALERT"
            assert r3.assessment.failure_signature is not None

            # Run 4: repeated identical error -> suppress repeated error
            r4 = run_observation(repo_root=ROOT, output_dir=out_dir, snapshot=snap_err)
            assert r4.assessment.notify is False, "Repeated identical error was not suppressed"
            assert r4.assessment.terminal_outcome == "NO_NOTIFICATION"
            assert "Repeated unchanged collection failure signature" in r4.assessment.reason

        return {"name": name, "status": "PASS", "details": "Unchanged state suppression and repeated failure signature deduplication verified"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_observer_sleeping_host_and_missed_runs_handling() -> Dict[str, Any]:
    name = "observer_sleeping_host_handling"
    try:
        from tools.pr_governance_core.observer import run_observation
        from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
            last_accepted_file = out_dir / "last_accepted_snapshot.json"
            out_dir.mkdir(parents=True, exist_ok=True)

            snap_now = QueueSnapshot(
                observed_at=datetime.now(timezone.utc).isoformat(),
                branches=[
                    BranchSnapshot(branch="feat/gov-alpha", head_sha="1111", ahead=1, behind=0, completed_waves="1", open_waves="—"),
                ],
            )
            # Run 1: baseline
            run_observation(repo_root=ROOT, output_dir=out_dir, snapshot=snap_now)

            # Simulate 72 hours passing while machine was asleep
            data = json.loads(last_accepted_file.read_text(encoding="utf-8"))
            data["observed_at"] = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
            last_accepted_file.write_text(json.dumps(data), encoding="utf-8")

            # Run 2: resumed run
            receipt = run_observation(repo_root=ROOT, output_dir=out_dir, snapshot=snap_now)

            assert receipt.hours_since_last_observation is not None
            assert receipt.hours_since_last_observation >= 71.0, f"Expected >= 71h, got {receipt.hours_since_last_observation}"
            assert receipt.missed_interval_hours is not None
            assert receipt.missed_interval_hours >= 71.0
            # Missed intervals do not cause replay storms or crashes
            assert receipt.assessment.terminal_outcome == "NO_NOTIFICATION"

        return {"name": name, "status": "PASS", "details": "Missed interval calculated (72.0h) without replay storms or crashes"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_observer_shadow_scenarios_qualification() -> Dict[str, Any]:
    name = "observer_shadow_qualification"
    try:
        from tools.pr_governance_core.observer_shadow import run_shadow_scenarios

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "reports" / "pr_queue_observer"
            scenarios = run_shadow_scenarios(repo_root=ROOT, output_dir=out_dir)

            assert len(scenarios) >= 5, f"Expected at least 5 scenarios, got {len(scenarios)}"
            failures = [sc for sc in scenarios if not sc["passed"]]
            if failures:
                return {
                    "name": name,
                    "status": "FAIL",
                    "error": f"Shadow scenarios failed: {[f['scenario'] for f in failures]}",
                }

        return {
            "name": name,
            "status": "PASS",
            "details": f"All {len(scenarios)} shadow scenarios passed qualification (baseline, unchanged, rank transition, fault alert, fault dedup, lost progress)",
        }
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_observer_prompt_and_runbook_artifacts() -> Dict[str, Any]:
    name = "observer_prompt_and_runbook_artifacts"
    try:
        prompt_file = ROOT / "automations" / "prompts" / "pr_queue_observer_agent.md"
        runbook_file = ROOT / "documentation" / "runbooks" / "pr_queue_observer_runbook.md"

        if not prompt_file.exists():
            return {"name": name, "status": "FAIL", "error": f"Missing observer prompt at {prompt_file}"}
        if not runbook_file.exists():
            return {"name": name, "status": "FAIL", "error": f"Missing observer runbook at {runbook_file}"}

        p_text = prompt_file.read_text(encoding="utf-8")
        rb_text = runbook_file.read_text(encoding="utf-8")

        assert "Read-Only Invariant" in p_text, "Prompt missing Read-Only Invariant"
        assert "observer.lock" in p_text, "Prompt missing observer.lock single-run lock"
        assert "reports/pr_queue_observer" in p_text, "Prompt missing isolated reports destination"
        assert "Notification Deduplication" in p_text, "Prompt missing Notification Deduplication"
        assert "America/New_York" in p_text, "Prompt missing America/New_York timezone SSOT"
        assert "No Autonomous Activation" in rb_text, "Runbook missing No Autonomous Activation warning"

        return {"name": name, "status": "PASS", "details": "Prompt and runbook declare read-only, lock, isolated reports, deduplication, and non-autonomous activation"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}
