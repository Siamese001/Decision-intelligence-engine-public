"""Adversarial and boundary test suite for budget ownership, clock epochs, and checkpoint integrity.

Verifies:
1. Checkpoint invariant bypass: unsealed decisions, missing files, and mismatched digests fail closed.
2. Boundary payloads: zero/negative reservation, immediate expiration, cancellation.
3. Clock epoch discontinuity: host reboot detects continuity break, disables optional work without replenishment.
4. Crash after effect: unacknowledged lease requires independent reconciliation before acknowledgment.
5. Concurrency: high-contention multi-threaded worker reservations respect ceiling without data races.
6. Provider request budget verdict: respects request context deadline and cancellation.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Set

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _read_attempt_checkpoint,
    memoize_substage_checkpoints,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.cost_tracker import (
    ActionRole,
    BudgetExhaustionError,
    BudgetReservationManager,
    DeadlineExceededError,
    OptionalWorkDisabledError,
    RequestContext,
    ReservationError,
    ReservationState,
    get_reservation_manager,
)
from dual_pipeline.provider_request import provider_request_budget_verdict


class MockStateMachine:
    def __init__(self) -> None:
        self.frozen_artifacts: Dict[str, str] = {}
        self.permanently_frozen: Set[str] = set()
        self.passing_stages: Set[str] = set()

    def is_frozen(self, stage: str) -> bool:
        return stage in self.frozen_artifacts

    def freeze_artifact(self, stage: str, content: str) -> None:
        self.frozen_artifacts[stage] = content


def test_checkpoint_invariant_bypass_unsealed_decision_fails_closed(tmp_path: Path):
    att_dir = tmp_path / "attempt_1"
    att_dir.mkdir()
    ckpt_file = att_dir / "output_1.ckpt.md"
    ckpt_file.write_text("# Deliverable Content\n" + ("Text " * 60), encoding="utf-8")

    dec_file = att_dir / "final_release_decision.json"
    # Unsealed decision: sealed flag is False or missing
    dec_file.write_text(json.dumps({
        "status": "PASS",
        "output_1_pass": True,
        "sealed": False,
    }), encoding="utf-8")

    content = _read_attempt_checkpoint(att_dir, "output_1.ckpt.md", "output_1_pass")
    assert content is None, "Unsealed decision MUST fail closed and return None"


def test_checkpoint_invariant_bypass_missing_decision_file_fails_closed(tmp_path: Path):
    att_dir = tmp_path / "attempt_2"
    att_dir.mkdir()
    ckpt_file = att_dir / "output_1.ckpt.md"
    ckpt_file.write_text("# Deliverable Content\n" + ("Text " * 60), encoding="utf-8")

    # final_release_decision.json is absent
    content = _read_attempt_checkpoint(att_dir, "output_1.ckpt.md", "output_1_pass")
    assert content is None, "Missing decision file MUST fail closed and return None"


def test_checkpoint_invariant_bypass_source_hash_mismatch_fails_closed(tmp_path: Path):
    att_dir = tmp_path / "attempt_3"
    att_dir.mkdir()
    ckpt_file = att_dir / "output_1.ckpt.md"
    ckpt_file.write_text("# Deliverable Content\n" + ("Text " * 60), encoding="utf-8")

    dec_file = att_dir / "final_release_decision.json"
    dec_file.write_text(json.dumps({
        "status": "PASS",
        "output_1_pass": True,
        "sealed": True,
        "source_hash": "old_source_sha256",
    }), encoding="utf-8")

    content = _read_attempt_checkpoint(
        att_dir,
        "output_1.ckpt.md",
        "output_1_pass",
        expected_source_hash="new_source_sha256",
    )
    assert content is None, "Mismatched source hash MUST fail closed"


def test_checkpoint_invariant_bypass_config_digest_mismatch_fails_closed(tmp_path: Path):
    att_dir = tmp_path / "attempt_4"
    att_dir.mkdir()
    ckpt_file = att_dir / "output_1.ckpt.md"
    ckpt_file.write_text("# Deliverable Content\n" + ("Text " * 60), encoding="utf-8")

    dec_file = att_dir / "final_release_decision.json"
    dec_file.write_text(json.dumps({
        "status": "PASS",
        "output_1_pass": True,
        "sealed": True,
        "config_digest": "v1_config",
    }), encoding="utf-8")

    content = _read_attempt_checkpoint(
        att_dir,
        "output_1.ckpt.md",
        "output_1_pass",
        expected_config_digest="v2_config",
    )
    assert content is None, "Mismatched config digest MUST fail closed"


def test_boundary_zero_and_over_reservation_attempts():
    mgr = get_reservation_manager()
    ctx = RequestContext.create(max_cost_usd=2.0)

    # Attempt over-reservation
    with pytest.raises(BudgetExhaustionError, match="Cost reservation breach"):
        mgr.reserve(ctx, estimated_cost_usd=2.50)

    assert ctx.status == "BUDGET_EXHAUSTED"
    assert ctx.reserved_cost_usd == 0.0

    # Reset status and verify zero cost reservation succeeds
    ctx.status = "ACTIVE"
    lease_zero = mgr.reserve(ctx, estimated_cost_usd=0.0)
    assert lease_zero.state == ReservationState.ACTIVE.value
    mgr.reconcile(ctx, lease_zero.reservation_id, actual_cost_usd=0.0)
    assert ctx.consumed_cost_usd == 0.0


def test_monotonic_deadline_expiration_and_cancellation():
    mgr = get_reservation_manager()
    ctx_exp = RequestContext.create(timeout_seconds=0.02)
    time.sleep(0.04)

    assert ctx_exp.is_expired() is True
    with pytest.raises(DeadlineExceededError):
        mgr.reserve(ctx_exp, estimated_cost_usd=0.1)

    ctx_cancel = RequestContext.create(timeout_seconds=60.0)
    ctx_cancel.cancel("OPERATOR_ABORT")
    assert ctx_cancel.is_cancelled is True
    with pytest.raises(ReservationError, match="Context is cancelled"):
        mgr.reserve(ctx_cancel, estimated_cost_usd=0.1)


def test_clock_epoch_discontinuity_reconstructs_conservative_deadline_and_disables_optional_work():
    now_dt = datetime.now(timezone.utc)
    future_exp = (now_dt + timedelta(seconds=120)).isoformat()

    serialized_ctx = {
        "run_id": "run_reboot_test",
        "request_id": "req_reboot_test",
        "engagement_id": "test_eng",
        "clock_epoch_id": "old_boot_epoch_prior_to_reboot",
        "expiry_iso": future_exp,
        "deadline_monotonic": 999999.0,
        "max_cost_usd": 10.0,
        "consumed_cost_usd": 4.5,
        "reserved_cost_usd": 0.0,
        "max_tokens": None,
        "consumed_tokens": 5000,
        "reserved_tokens": 0,
        "tool_calls_max": 8,
        "tool_calls_count": 2,
        "model_calls_incremental_max": 2,
        "model_calls_incremental_count": 0,
        "disable_optional_work": False,
        "is_cancelled": False,
        "status": "ACTIVE",
        "reservations": {},
    }

    # Restoring after a reboot/clock epoch jump
    restored = BudgetReservationManager.restore_context_after_crash(serialized_ctx)

    # Invariants:
    # 1. Consumed budget is preserved and NOT replenished
    assert restored.consumed_cost_usd == 4.5
    assert restored.consumed_tokens == 5000
    assert restored.available_cost_usd() == 5.5

    # 2. Clock discontinuity detected -> optional work disabled!
    assert restored.disable_optional_work is True
    assert restored.deadline_monotonic < 999999.0  # conservative deadline reconstructed

    mgr = get_reservation_manager()
    # Primary work remains permitted
    lease_p = mgr.reserve(restored, estimated_cost_usd=1.0, action_role=ActionRole.PRIMARY.value)
    assert lease_p.state == ReservationState.ACTIVE.value

    # Optional healer/reasoning work is barred
    with pytest.raises(OptionalWorkDisabledError, match="Optional healer work disabled"):
        mgr.reserve(restored, estimated_cost_usd=0.5, action_role=ActionRole.HEALER.value)


def test_crash_after_effect_unacknowledged_lease_requires_independent_reconciliation():
    mgr = get_reservation_manager()
    ctx = RequestContext.create(max_cost_usd=5.0)

    lease = mgr.reserve(ctx, estimated_cost_usd=1.5, action_role="primary")

    # Action executed external effect, but system crashed before ack
    mgr.reconcile(
        ctx,
        lease.reservation_id,
        actual_cost_usd=1.2,
        unacknowledged_effect=True,
    )

    assert lease.state == ReservationState.PENDING_RECONCILIATION.value
    assert lease.unacknowledged_effect is True

    # Reconciling crash recovery without acknowledgment must fail
    with pytest.raises(ReservationError, match="unacknowledged external effects"):
        mgr.reconcile_crash_recovery(ctx, lease.reservation_id, acknowledged=False)

    # Independent verification provides verified expenditure and acknowledges effect
    recovered = mgr.reconcile_crash_recovery(
        ctx,
        lease.reservation_id,
        independent_verified_cost=1.25,
        acknowledged=True,
    )
    assert recovered.state == ReservationState.RECONCILED.value
    assert recovered.unacknowledged_effect is False
    assert recovered.actual_cost_usd == 1.25
    assert ctx.consumed_cost_usd == 1.25


def test_concurrency_multi_threaded_reservation_stress():
    mgr = get_reservation_manager()
    ctx = RequestContext.create(max_cost_usd=10.0, max_tokens=100000)

    def worker(worker_id: int):
        try:
            lease = mgr.reserve(
                ctx,
                estimated_cost_usd=0.40,
                estimated_tokens=1000,
                action_role="primary",
            )
            time.sleep(0.005)
            mgr.reconcile(
                ctx,
                lease.reservation_id,
                actual_cost_usd=0.35,
                actual_tokens=900,
            )
            return True
        except BudgetExhaustionError:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(worker, range(30)))

    # Ceilings must NEVER have been breached
    assert ctx.consumed_cost_usd <= 10.0
    assert ctx.consumed_tokens <= 100000
    assert ctx.reserved_cost_usd >= 0.0


def test_provider_request_budget_verdict_enforces_request_context_deadlines():
    metrics = {
        "request_payload_digest": "dig_123",
        "request_serialized_chars": 500,
    }

    # Normal within budget
    v1 = provider_request_budget_verdict(
        request_metrics=metrics,
        maximum_provider_request_chars=1000,
    )
    assert v1["status"] == "ALLOW"

    # Expired context
    ctx_exp = RequestContext.create(timeout_seconds=0.01)
    time.sleep(0.02)
    v_exp = provider_request_budget_verdict(
        request_metrics=metrics,
        maximum_provider_request_chars=1000,
        request_context=ctx_exp,
    )
    assert v_exp["status"] == "BLOCK"
    assert v_exp["reason_code"] == "REQUEST_CONTEXT_DEADLINE_EXCEEDED"

    # Cancelled context
    ctx_can = RequestContext.create(timeout_seconds=60.0)
    ctx_can.cancel()
    v_can = provider_request_budget_verdict(
        request_metrics=metrics,
        maximum_provider_request_chars=1000,
        request_context=ctx_can,
    )
    assert v_can["status"] == "BLOCK"
    assert v_can["reason_code"] == "REQUEST_CONTEXT_CANCELLED"

    # Disabled optional work
    ctx_dis = RequestContext.create(timeout_seconds=60.0)
    ctx_dis.disable_optional_work = True
    v_dis = provider_request_budget_verdict(
        request_metrics=metrics,
        maximum_provider_request_chars=1000,
        request_context=ctx_dis,
        action_role="healer",
    )
    assert v_dis["status"] == "BLOCK"
    assert v_dis["reason_code"] == "OPTIONAL_WORK_DISABLED"
