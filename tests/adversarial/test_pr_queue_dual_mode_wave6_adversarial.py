"""Wave 6 guards against treating repository artifacts as host evidence."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "docs/plans/pr_queue_dual_mode_refactor/installation"


def test_repository_baseline_is_not_mislabeled_as_installed_host_body() -> None:
    package = json.loads((INSTALL / "rollback_package.json").read_text(encoding="utf-8"))
    host = package["host_registration"]
    assert host["status"] == "UNVERIFIED"
    assert host["installed_body_sha256"] is None
    assert host["readback_required_before_update"] is True
    instruction = package["rollback"]["instructions"]
    assert "exact installed body" in instruction
    assert "reference, not proof" in instruction


def test_no_duplicate_job_or_implicit_state_migration_is_proposed() -> None:
    package = json.loads((INSTALL / "rollback_package.json").read_text(encoding="utf-8"))
    assert package["status"] == "PREPARED_NOT_INSTALLED"
    assert package["state_migration"]["automatic_migration"] is False
    assert package["state_migration"]["legacy_state_preserved"] is True
    registry = json.loads((ROOT / "config/scheduled_tasks/task_registry.json").read_text(encoding="utf-8"))
    task = registry["tasks"]["pr_queue_observer"]
    assert task["registration_status"]["registration_id"] is None
    assert task["cadence_specification"]["declared_cron"] == "0 9 * * 1-5"
