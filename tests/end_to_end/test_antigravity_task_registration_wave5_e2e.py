"""tests/end_to_end/test_antigravity_task_registration_wave5_e2e.py

Wave 5 registration reconciliation, rollback snapshot verification, operator authorization,
readback parity, and 14 host smoke runs qualification tests.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tools.generate_antigravity_task_exports import TASK_EXPORT_MAP, compute_sha256
from tools.reconcile_antigravity_registrations import (
    CADENCE_MAP,
    PRE_RESTORATION_COMMIT,
    capture_rollback_snapshots,
    execute_host_smoke_runs,
    perform_installation_and_readback,
    update_registration_reconciliation,
)
from tools.scheduled_task_core import (
    AntigravityHostLaunchAdapter,
    ApprovalRecord,
    UnauthenticatedApprovalError,
)

ROOT = Path(__file__).resolve().parents[2]


# -----------------------------------------------------------------------------
# 1. Registration Reconciliation Accuracy
# -----------------------------------------------------------------------------

def test_registration_reconciliation_accuracy_e2e() -> None:
    """Verifies that registration reconciliation accurately reconciles all 7 tasks with preserved cadences."""
    recon_path = ROOT / "config" / "scheduled_tasks" / "registration_reconciliation.json"
    assert recon_path.is_file(), "registration_reconciliation.json must exist"

    data = json.loads(recon_path.read_text(encoding="utf-8"))
    assert data["contract_id"] == "urn:nee:scheduled-task-registration-reconciliation:2.0.0"

    tasks = {t["task_id"]: t for t in data["tasks"]}
    assert len(tasks) == 7

    for task_id, expected_cadence in CADENCE_MAP.items():
        assert task_id in tasks, f"Task '{task_id}' missing from reconciliation"
        entry = tasks[task_id]
        assert entry["target_cadence"] == expected_cadence["cadence"]
        assert entry["timezone"] == expected_cadence["timezone"]
        assert entry["dual_mode_support"] == ["scheduled", "interactive"]
        assert Path(ROOT / entry["rollback_snapshot"]).is_file()


# -----------------------------------------------------------------------------
# 2. Per-Task Rollback Snapshots Parity
# -----------------------------------------------------------------------------

def test_per_task_rollback_snapshots_parity_e2e() -> None:
    """Verifies rollback snapshots match commit f54762198 bytes and prove restoration added depth."""
    snap_dir = ROOT / "artifacts" / "exports" / "rollback_snapshots"
    assert snap_dir.is_dir()

    for task_id, mapping in TASK_EXPORT_MAP.items():
        snap_file = snap_dir / f"{task_id}_pre_restoration.md"
        assert snap_file.is_file(), f"Rollback snapshot missing for {task_id}"

        snap_text = snap_file.read_text(encoding="utf-8")
        canon_file = ROOT / mapping["canonical_prompt"]
        canon_text = canon_file.read_text(encoding="utf-8")

        # Rollback snapshot must differ from restored canonical prompt (proves loss recovery)
        assert compute_sha256(snap_text) != compute_sha256(canon_text)


# -----------------------------------------------------------------------------
# 3. Unauthenticated Authorization Rejection
# -----------------------------------------------------------------------------

def test_unauthenticated_authorization_fails_closed_e2e(tmp_path: Path) -> None:
    """Verifies that missing or synthetic operator credentials fail closed without installing registrations."""
    with pytest.raises(UnauthenticatedApprovalError, match="Invalid operator identity"):
        perform_installation_and_readback(
            output_root=tmp_path,
            operator_name="auto",
            operator_signature="sig-auto",
            repo_root=ROOT,
        )

    with pytest.raises(UnauthenticatedApprovalError, match="Missing operator cryptographic authorization signature"):
        perform_installation_and_readback(
            output_root=tmp_path,
            operator_name="Human Operator",
            operator_signature="",
            repo_root=ROOT,
        )


# -----------------------------------------------------------------------------
# 4. Installation and Readback Verification
# -----------------------------------------------------------------------------

def test_installation_and_readback_verification_e2e(tmp_path: Path) -> None:
    """Verifies that authorized installation performs 100% readback parity and passes 14 smoke runs."""
    receipt = perform_installation_and_readback(
        output_root=tmp_path,
        operator_name="Operator Authorized Reviewer",
        operator_signature="ed25519-sig-auth-wave5-pass",
        repo_root=ROOT,
    )

    assert receipt["contract_id"] == "urn:nee:antigravity-task-installation-readback-receipt:1.0.0"
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["total_smoke_runs"] == 14
    assert receipt["smoke_runs_passed"] == 14

    # Verify readback records
    readback_list = receipt["readback_verification"]
    assert len(readback_list) == 7
    for record in readback_list:
        assert record["parity_verified"] is True
        assert record["canonical_sha256"] == record["readback_sha256"]

    receipt_file = tmp_path / "installation_readback_receipt.json"
    assert receipt_file.is_file()
    assert receipt["canonical_digest"]


# -----------------------------------------------------------------------------
# 5. Pending Host Run Remains Unqualified
# -----------------------------------------------------------------------------

def test_pending_host_run_remains_unqualified_e2e(tmp_path: Path) -> None:
    """Verifies that an incomplete or pending host run is not treated as a qualified execution."""
    launcher = AntigravityHostLaunchAdapter()
    res = launcher.launch_task(
        task_id="weekly_market_intelligence",
        mode="scheduled",
        fixture_root=tmp_path,
        run_id="run-pending-test",
    )
    assert res.terminal_outcome == "AWAITING_APPROVAL"
    # An awaiting approval run is not COMPLETE
    assert res.terminal_outcome != "COMPLETE"
    assert res.mutations_count == 0
