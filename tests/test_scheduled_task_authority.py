"""Unit tests for Track 1B execution authority, anti-spoofing, approval verification, and journal accounting."""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import pytest

from tools.scheduled_task_core.authority import (
    ApprovalRecord,
    AuthorityContext,
    DirectCancellationBlockedError,
    ExpiredApprovalError,
    ItemSetDriftError,
    PlanDriftError,
    ReplayNonceError,
    RestoreWithoutAuthorityError,
    ScheduledMutationBlockedError,
    UnauthenticatedApprovalError,
    authorize_cancellation,
    authorize_restore,
    validate_execution_authority,
    verify_approval_record,
)
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.scheduled_task_core.journal import OperationalCategory, OperationalChangeJournal
from tools.storage_hygiene_core.apply import apply_quarantine, restore_quarantine
from tools.storage_hygiene_core.receipt import create_hygiene_report


def _make_scheduled_context(task_id: str = "knowledge_graph_hygiene", run_id: str = "run-20261006-01") -> AuthorityContext:
    return AuthorityContext(
        task_id=task_id,
        run_id=run_id,
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="system_scheduler",
        allowed_capabilities={"read_only_graph_inspection"},
        is_scheduled=True,
    )


def _make_interactive_context(task_id: str = "storage_repository_hygiene", run_id: str = "run-20261006-02") -> AuthorityContext:
    return AuthorityContext(
        task_id=task_id,
        run_id=run_id,
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.AUTHORIZED_APPLY,
        actor="Amit Ayer",
        allowed_capabilities={"read_only_storage_inspection", "quarantine_planning"},
        is_scheduled=False,
    )


def test_timer_launch_plus_old_human_message_rejected():
    """Acceptance criterion: Timer launch plus an old human message from prior run is rejected."""
    ctx = _make_scheduled_context(run_id="run-current-002")

    # Approval was issued for an earlier run ID
    old_approval = {
        "approval_id": "app-old-001",
        "task_id": "knowledge_graph_hygiene",
        "run_id": "run-earlier-001",  # Old human message run
        "operator": "Amit Ayer",
        "signature": "sig-ed25519-valid",
        "status": "APPROVED",
        "approved_items": ["item1"],
        "replay_nonce": "nonce-111",
    }

    with pytest.raises(UnauthenticatedApprovalError, match="Approval run_id mismatch"):
        verify_approval_record(old_approval, ctx, expected_items=["item1"])


def test_interactive_text_in_document_rejected():
    """Acceptance criterion: Interactive text in a document cannot alter launcher authority."""
    ctx = _make_scheduled_context()
    assert ctx.can_mutate is False

    with pytest.raises(ScheduledMutationBlockedError, match="Unauthorized .* blocked"):
        ctx.assert_can_mutate("apply_graph_repair")


def test_omitted_mode_fails_closed():
    """Acceptance criterion: Omitted mode fails closed."""
    ctx = AuthorityContext(
        task_id="knowledge_graph_hygiene",
        run_id="run-1",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=None,  # type: ignore[arg-type]
        actor="test",
        is_scheduled=True,
    )
    with pytest.raises(ScheduledMutationBlockedError, match="Execution mode is omitted"):
        validate_execution_authority(ctx)


def test_caller_supplied_scheduled_false_rejected():
    """Acceptance criterion: Caller-supplied scheduled=False cannot bypass scheduled authority."""
    ctx = _make_scheduled_context(task_id="storage_repository_hygiene")

    # Directly on context check
    with pytest.raises(ScheduledMutationBlockedError, match="cannot override trusted scheduled authority"):
        ctx.check_caller_scheduled_bypass(caller_scheduled=False)

    # In apply_quarantine
    plan = create_hygiene_report(
        mode="plan",
        scheduled=False,
        policy_digest="0" * 64,
        pins={"active_generations": [], "rollback_generations": [], "preserved_generations": [], "invalid_sources": []},
        summary={"total_candidates": 0, "total_pinned": 0, "reclaimable_bytes": 0},
    )

    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as app_f:
        app_f.write('{"status": "APPROVED", "operator": "Amit Ayer", "signature": "valid"}')
        app_f.flush()

        with pytest.raises(ScheduledMutationBlockedError, match="cannot override trusted scheduled authority"):
            apply_quarantine(plan, Path(app_f.name), scheduled=False, authority_context=ctx)


def test_forged_and_empty_operator_fields_rejected():
    """Acceptance criterion: Forged or empty operator fields and self-authored hashes are rejected."""
    ctx = _make_interactive_context()

    # Empty operator
    with pytest.raises(UnauthenticatedApprovalError, match="missing operator identity"):
        verify_approval_record(
            {"status": "APPROVED", "operator": "", "signature": "sig", "replay_nonce": "n1"},
            ctx,
        )

    # Synthetic / non-human operator
    with pytest.raises(UnauthenticatedApprovalError, match="not an authentic human operator"):
        verify_approval_record(
            {"status": "APPROVED", "operator": "scheduler", "signature": "sig", "replay_nonce": "n2"},
            ctx,
        )

    with pytest.raises(UnauthenticatedApprovalError, match="not an authentic human operator"):
        verify_approval_record(
            {"status": "APPROVED", "operator": "auto", "signature": "sig", "replay_nonce": "n3"},
            ctx,
        )

    # Self-authored JSON hash without signature / ALR evidence
    with pytest.raises(UnauthenticatedApprovalError, match="Self-authored JSON hash without human authorization"):
        verify_approval_record(
            {"status": "APPROVED", "operator": "Amit Ayer", "signature": "", "replay_nonce": "n4"},
            ctx,
        )


def test_expired_and_replayed_approval_rejected():
    """Acceptance criterion: Expired or replayed approvals fail closed."""
    ctx = _make_interactive_context(run_id="run-exp-01")

    # Expired approval
    expired_approval = {
        "task_id": ctx.task_id,
        "run_id": ctx.run_id,
        "operator": "Amit Ayer",
        "signature": "sig-valid",
        "status": "APPROVED",
        "expires_at": "2026-10-01T00:00:00Z",  # In the past
        "replay_nonce": "nonce-expired",
        "approved_items": ["itemA"],
    }
    with pytest.raises(ExpiredApprovalError, match="Approval expired"):
        verify_approval_record(
            expired_approval,
            ctx,
            expected_items=["itemA"],
            now_utc=dt.datetime(2026, 10, 6, 12, 0, 0, tzinfo=dt.timezone.utc),
        )

    # Replayed nonce
    valid_time_approval = {
        "task_id": ctx.task_id,
        "run_id": ctx.run_id,
        "operator": "Amit Ayer",
        "signature": "sig-valid",
        "status": "APPROVED",
        "expires_at": "2026-10-07T00:00:00Z",
        "replay_nonce": "nonce-consumed-123",
        "approved_items": ["itemA"],
    }
    consumed = {"nonce-consumed-123"}
    with pytest.raises(ReplayNonceError, match="already been consumed"):
        verify_approval_record(
            valid_time_approval,
            ctx,
            expected_items=["itemA"],
            consumed_nonces=consumed,
            now_utc=dt.datetime(2026, 10, 6, 12, 0, 0, tzinfo=dt.timezone.utc),
        )


def test_baseline_and_item_drift_rejected():
    """Acceptance criterion: Baseline commit drift and candidate item drift fail closed."""
    ctx = _make_interactive_context(run_id="run-drift-01")

    base_approval = {
        "task_id": ctx.task_id,
        "run_id": ctx.run_id,
        "operator": "Amit Ayer",
        "signature": "sig-valid",
        "status": "APPROVED",
        "replay_nonce": "nonce-drift-01",
        "approved_items": ["cand1", "cand2"],
        "plan_digest": "digest_abc",
        "baseline_sha": "commit_sha_111",
    }

    # Item set drift (candidate cand3 appeared after approval)
    with pytest.raises(ItemSetDriftError, match="Approved items do not match candidate item scope"):
        verify_approval_record(
            base_approval,
            ctx,
            expected_items=["cand1", "cand2", "cand3"],
            expected_plan_digest="digest_abc",
            current_baseline_sha="commit_sha_111",
        )

    # Plan digest drift
    with pytest.raises(PlanDriftError, match="Plan digest mismatch"):
        verify_approval_record(
            base_approval,
            ctx,
            expected_items=["cand1", "cand2"],
            expected_plan_digest="digest_xyz_different",
            current_baseline_sha="commit_sha_111",
        )

    # Baseline commit SHA drift
    with pytest.raises(PlanDriftError, match="Baseline git commit drift"):
        verify_approval_record(
            base_approval,
            ctx,
            expected_items=["cand1", "cand2"],
            expected_plan_digest="digest_abc",
            current_baseline_sha="commit_sha_222_different",
        )


def test_direct_cancellation_without_authority_fails():
    """Acceptance criterion: Direct cancellation fails before side effects."""
    scheduled_ctx = _make_scheduled_context()
    journal = OperationalChangeJournal(run_id=scheduled_ctx.run_id)

    with pytest.raises(DirectCancellationBlockedError, match="prohibited under scheduled authority"):
        authorize_cancellation(scheduled_ctx, "directive-101", journal)

    # Recorded as blocked action
    assert journal.unauthorized_attempts_count == 1
    assert journal.total_operational_changes == 0


def test_restore_without_authority_fails():
    """Acceptance criterion: Quarantine restore without authority fails before side effects."""
    scheduled_ctx = _make_scheduled_context()
    journal = OperationalChangeJournal(run_id=scheduled_ctx.run_id)

    with pytest.raises(RestoreWithoutAuthorityError, match="strictly prohibited in scheduled execution"):
        authorize_restore(scheduled_ctx, "batch-001", journal)

    assert journal.unauthorized_attempts_count == 1

    # In restore_quarantine
    with pytest.raises(ScheduledMutationBlockedError, match="quarantine restore"):
        restore_quarantine(Path("/fake/receipt.json"), authority_context=scheduled_ctx)


def test_fixture_zero_graph_changes_and_six_cancellations_reports_six_changes_zero_repairs():
    """Acceptance criterion:

    A fixture with zero graph changes and six authorized cancellations reports
    six operational changes and zero verified defect repairs.
    """
    interactive_ctx = _make_interactive_context(task_id="knowledge_graph_hygiene", run_id="run-cancellations-06")
    journal = OperationalChangeJournal(run_id=interactive_ctx.run_id)

    # 6 authorized cancellations
    for i in range(1, 7):
        dir_id = f"directive-2026-00{i}"
        approval = {
            "task_id": interactive_ctx.task_id,
            "run_id": interactive_ctx.run_id,
            "operator": "Amit Ayer",
            "signature": f"sig-dir-{i}",
            "status": "APPROVED",
            "replay_nonce": f"nonce-dir-{i}",
            "approved_items": [dir_id],
        }
        authorize_cancellation(interactive_ctx, dir_id, journal, approval_source=approval)

    summary = journal.to_summary_dict()
    assert summary["graph_delta"] == 0, "Graph delta must be exactly 0"
    assert summary["directive_delta"] == 6, "Directive delta must be exactly 6"
    assert summary["verified_defect_repairs"] == 0, "Cancellations must NEVER count as defect repairs"
    assert summary["unauthorized_attempts"] == 0

    assert journal.total_operational_changes == 6
    assert journal.total_verified_defect_repairs == 0


def test_unauthorized_cancellation_attempt_produces_failure_not_clean_audit():
    """Acceptance criterion: Unauthorized cancellation attempts produce failure, not a clean audit."""
    scheduled_ctx = _make_scheduled_context()
    journal = OperationalChangeJournal(run_id=scheduled_ctx.run_id)

    with pytest.raises(DirectCancellationBlockedError):
        authorize_cancellation(scheduled_ctx, "directive-unauthorized", journal)

    summary = journal.to_summary_dict()
    assert summary["unauthorized_attempts"] == 1
    assert summary["verified_defect_repairs"] == 0


def test_concurrent_external_changes_attributed_or_unresolved():
    """Acceptance criterion: Concurrent external changes are attributed or marked unresolved."""
    ctx = _make_scheduled_context()
    journal = OperationalChangeJournal(run_id=ctx.run_id)

    # External git push and external file edit detected
    journal.record_external_change(
        action_id="ext-git-001",
        category=OperationalCategory.GIT,
        target_id="origin/main",
        details={"attribution": "EXTERNAL_CONCURRENT_PUSH", "sha": "abc999"},
    )
    journal.record_external_change(
        action_id="ext-fs-002",
        category=OperationalCategory.FILESYSTEM,
        target_id="output/other_project/01_transcript.md",
        details={"attribution": "EXTERNAL_CONCURRENT_WRITE"},
    )

    assert journal.external_changes_count == 2
    # External changes are NOT attributed to the task's operational changes or defect repairs
    assert journal.total_operational_changes == 0
    assert journal.total_verified_defect_repairs == 0
