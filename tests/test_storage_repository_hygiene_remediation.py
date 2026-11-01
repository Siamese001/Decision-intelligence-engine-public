"""tests/test_storage_repository_hygiene_remediation.py

Wave 1 test suite for Storage & Clutter Hygiene Remediation.
Verifies:
1. Scheduled audit read-only discovery & manifest emission without mutation.
2. Interactive mode awaiting operator approval gate.
3. Interactive execution with valid operator approval record.
4. Zero graph mutation invariant preservation.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.scheduled_task_core.authority import ApprovalRecord, AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.scheduled_task_core.adapters import (
    TaskExecutionResult,
    run_storage_repository_hygiene,
)


def _setup_fixture_tree(root: Path) -> tuple[Path, Path]:
    """Helper to set up mock fixture directory tree for storage hygiene testing."""
    gens_dir = root / "generations"
    gens_dir.mkdir(parents=True)
    active_gen = gens_dir / "gen-active"
    active_gen.mkdir()
    (active_gen / "graph.db").write_text("active_db", encoding="utf-8")

    inactive_gen = gens_dir / "gen-old"
    inactive_gen.mkdir()
    (inactive_gen / "graph.db").write_text("old_db", encoding="utf-8")

    pair_file = root / "active_pair.json"
    pair_file.write_text(json.dumps({"generation_dir": "gen-active"}), encoding="utf-8")

    cache_dir = root / "output" / "transcript_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "audio_01.raw").write_text("raw_audio_data", encoding="utf-8")

    return root, pair_file


def test_storage_repository_hygiene_scheduled_read_only(tmp_path: Path) -> None:
    """Verifies scheduled execution performs read-only discovery and emits a sealed manifest."""
    fixture_root, _ = _setup_fixture_tree(tmp_path / "sample_repo")
    run_dir = tmp_path / "reports" / "run-001"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="storage_repository_hygiene",
        run_id="run-001",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron-scheduler",
        allowed_capabilities={"audit_execution", "remediation_planning"},
        is_scheduled=True,
    )

    result = run_storage_repository_hygiene(ctx, fixture_root, run_dir)

    assert result.is_read_only is True
    assert result.mutations_count == 0
    assert result.findings_count > 0
    assert result.terminal_outcome in ("HITL_REQUIRED", "COMPLETE")
    assert result.report_path is not None and result.report_path.is_file()


def test_storage_repository_hygiene_awaiting_approval(tmp_path: Path) -> None:
    """Verifies interactive execution without approval record halts at AWAITING_APPROVAL."""
    fixture_root, _ = _setup_fixture_tree(tmp_path / "sample_repo")
    run_dir = tmp_path / "reports" / "run-002"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="storage_repository_hygiene",
        run_id="run-002",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="operator",
        allowed_capabilities={"audit_execution", "remediation_planning", "storage_quarantine"},
        is_scheduled=False,
    )

    result = run_storage_repository_hygiene(ctx, fixture_root, run_dir, approval=None)

    assert result.is_read_only is True
    assert result.mutations_count == 0
    assert result.terminal_outcome == "AWAITING_APPROVAL"
    assert result.findings_count > 0


def test_storage_repository_hygiene_interactive_approval(tmp_path: Path) -> None:
    """Verifies interactive execution with valid approval moves candidates to quarantine."""
    fixture_root, _ = _setup_fixture_tree(tmp_path / "sample_repo")
    run_dir = tmp_path / "reports" / "run-003"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        task_id="storage_repository_hygiene",
        run_id="run-003",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="operator",
        allowed_capabilities={"audit_execution", "remediation_planning", "storage_quarantine"},
        is_scheduled=False,
    )

    # First discover items to construct valid approval
    dry_run = run_storage_repository_hygiene(ctx, fixture_root, run_dir, approval=None)
    candidate_paths = [f["path"] for f in dry_run.findings]

    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    approval = ApprovalRecord(
        approval_id="app-001",
        task_id="storage_repository_hygiene",
        run_id="run-003",
        operator="human_operator",
        signature="ed25519:valid-human-signature",
        status="APPROVED",
        approved_items=candidate_paths,
        plan_digest="a" * 64,
        baseline_sha="b" * 40,
        issued_at=now.isoformat(),
        expires_at=(now + dt.timedelta(hours=2)).isoformat(),
        replay_nonce="nonce-srh-run-003",
        is_authentic_human=True,
    )

    result = run_storage_repository_hygiene(ctx, fixture_root, run_dir, approval=approval)

    assert result.is_read_only is False
    assert result.mutations_count > 0
    assert result.terminal_outcome == "COMPLETE"
    assert (fixture_root / ".quarantine" / "run-003").is_dir()


def test_storage_hygiene_manifest_and_quarantine_receipt_sealed() -> None:
    from tools.storage_hygiene_core.receipt import verify_report_digest

    wt_root = Path(__file__).resolve().parent.parent
    run_dir = wt_root / "reports" / "storage_hygiene" / "run-11-01-2026"
    manifest_path = run_dir / "manifest.json"
    receipt_path = run_dir / "quarantine_receipt.json"
    filtered_path = run_dir / "filtered_plan.json"

    assert manifest_path.is_file(), "Sealed planning manifest must exist"
    assert receipt_path.is_file(), "Quarantine execution receipt must exist"
    assert filtered_path.is_file(), "Filtered plan must exist"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    filtered = json.loads(filtered_path.read_text(encoding="utf-8"))

    assert verify_report_digest(manifest) is True
    assert verify_report_digest(receipt) is True
    assert verify_report_digest(filtered) is True
    assert receipt["zero_graph_mutation_verified"] is True
    assert receipt["active_pair_sha256_before"] == receipt["active_pair_sha256_after"]
    assert receipt["graph_sha256_before"] == receipt["graph_sha256_after"]
    assert receipt["pragma_integrity_check"] == "ok"
    assert receipt["moved_items_count"] == 159
    assert receipt["total_bytes_reclaimed"] == 755143
