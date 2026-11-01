"""End-to-end lifecycle verification for bounded runtime, request context, and DLQ.

Verifies:
1. Full primary, judge, and healer reservation and reconciliation lifecycle.
2. Multi-tenant and concurrent run budget isolation.
3. Checkpoint memoization requiring sealed passing decisions and matching digests.
4. Query replan manifest binding and novel action gating.
5. Strongly-typed DLQ routing and non-retryable policy enforcement.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Set

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import memoize_substage_checkpoints
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.cost_tracker import (
    ActionRole,
    BudgetExhaustionError,
    BudgetReservationManager,
    RequestContext,
    ReservationState,
    get_reservation_manager,
)
from dual_pipeline.l1_feedback_loop import L1FeedbackStateMachine
from pipeline_b_extraction.dlq import (
    DeadLetterQueueManager,
    ExtractionFailureType,
    classify_extraction_failure,
)
from storage.database import WorkItemState


class MockStateMachine:
    def __init__(self) -> None:
        self.frozen_artifacts: Dict[str, str] = {}
        self.permanently_frozen: Set[str] = set()
        self.passing_stages: Set[str] = set()

    def is_frozen(self, stage: str) -> bool:
        return stage in self.frozen_artifacts

    def freeze_artifact(self, stage: str, content: str) -> None:
        self.frozen_artifacts[stage] = content


def test_request_context_lifecycle_primary_judge_healer_reconciliation():
    mgr = get_reservation_manager()
    ctx = RequestContext.create(
        run_id="run_e2e_001",
        request_id="req_e2e_001",
        timeout_seconds=30.0,
        max_cost_usd=5.0,
        max_tokens=10000,
        tool_calls_max=8,
        model_calls_incremental_max=2,
    )

    assert ctx.available_cost_usd() == 5.0
    assert ctx.available_tokens() == 10000

    # 1. Primary action reservation & reconciliation
    lease_p = mgr.reserve(
        ctx,
        estimated_cost_usd=1.20,
        estimated_tokens=2500,
        action_role=ActionRole.PRIMARY.value,
    )
    assert lease_p.state == ReservationState.ACTIVE.value
    assert ctx.reserved_cost_usd == 1.20
    assert ctx.reserved_tokens == 2500
    assert ctx.available_cost_usd() == 3.80

    mgr.reconcile(
        ctx,
        lease_p.reservation_id,
        actual_cost_usd=0.95,
        actual_tokens=2100,
    )
    assert lease_p.state == ReservationState.RECONCILED.value
    assert ctx.consumed_cost_usd == 0.95
    assert ctx.consumed_tokens == 2100
    assert ctx.reserved_cost_usd == 0.0
    assert ctx.reserved_tokens == 0
    assert ctx.primary_action_count == 1

    # 2. Judge action reservation & reconciliation
    lease_j = mgr.reserve(
        ctx,
        estimated_cost_usd=0.50,
        estimated_tokens=1000,
        action_role=ActionRole.JUDGE.value,
    )
    assert ctx.model_calls_incremental_count == 1
    mgr.reconcile(
        ctx,
        lease_j.reservation_id,
        actual_cost_usd=0.45,
        actual_tokens=900,
    )
    assert ctx.judge_action_count == 1
    assert ctx.consumed_cost_usd == 1.40

    # 3. Healer action reservation & reconciliation
    lease_h = mgr.reserve(
        ctx,
        estimated_cost_usd=0.80,
        estimated_tokens=1500,
        action_role=ActionRole.HEALER.value,
    )
    assert ctx.model_calls_incremental_count == 2
    mgr.reconcile(
        ctx,
        lease_h.reservation_id,
        actual_cost_usd=0.75,
        actual_tokens=1400,
    )
    assert ctx.healer_action_count == 1
    assert ctx.consumed_cost_usd == 2.15
    assert ctx.consumed_tokens == 4400

    # Incremental model call limit reached (max 2 for judge + healer)
    with pytest.raises(BudgetExhaustionError, match="Incremental model call ceiling reached"):
        mgr.reserve(
            ctx,
            estimated_cost_usd=0.20,
            estimated_tokens=500,
            action_role=ActionRole.OPTIONAL_REASONING.value,
        )


def test_multi_tenant_and_concurrent_run_isolation():
    mgr = get_reservation_manager()
    ctx_a = RequestContext.create(
        run_id="run_tenant_alpha",
        engagement_id="tenant_alpha",
        max_cost_usd=4.0,
    )
    ctx_b = RequestContext.create(
        run_id="run_tenant_beta",
        engagement_id="tenant_beta",
        max_cost_usd=3.0,
    )

    # Reserve heavily in tenant alpha
    lease_a = mgr.reserve(ctx_a, estimated_cost_usd=3.5, action_role="primary")
    assert ctx_a.available_cost_usd() == 0.5
    # Tenant beta budget must remain completely unaffected
    assert ctx_b.available_cost_usd() == 3.0

    # Tenant beta reserves within its budget
    lease_b = mgr.reserve(ctx_b, estimated_cost_usd=2.5, action_role="primary")
    assert ctx_b.available_cost_usd() == 0.5

    # Tenant beta cannot over-reserve
    with pytest.raises(BudgetExhaustionError):
        mgr.reserve(ctx_b, estimated_cost_usd=1.0, action_role="primary")

    # Tenant alpha reconciles
    mgr.reconcile(ctx_a, lease_a.reservation_id, actual_cost_usd=3.0)
    assert ctx_a.consumed_cost_usd == 3.0
    assert ctx_b.consumed_cost_usd == 0.0

    mgr.reconcile(ctx_b, lease_b.reservation_id, actual_cost_usd=2.2)
    assert ctx_b.consumed_cost_usd == 2.2


def test_checkpoint_memoization_lifecycle_sealed_pass(tmp_path: Path):
    out_dir = tmp_path / "meeting_output"
    att_dir = out_dir / "attempts" / "attempt_1"
    att_dir.mkdir(parents=True)

    ckpt_file = att_dir / "output_1.ckpt.md"
    ckpt_content = "# Output 01 Resolved Transcript\n\nSpeaker 1: Verified deliverable content." + (" A" * 150)
    ckpt_file.write_text(ckpt_content, encoding="utf-8")

    dec_file = att_dir / "final_release_decision.json"
    dec_data = {
        "status": "PASS",
        "output_1_pass": True,
        "sealed": True,
        "source_hash": "src_hash_999",
        "config_digest": "cfg_digest_888",
    }
    dec_file.write_text(json.dumps(dec_data), encoding="utf-8")

    sm = MockStateMachine()
    metadata = {
        "source_hash": "src_hash_999",
        "config_digest": "cfg_digest_888",
    }

    memoized = memoize_substage_checkpoints(
        out_p=out_dir,
        state_machine=sm,
        metadata=metadata,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
    )

    assert memoized["output_1"] is True
    assert sm.is_frozen("output_1")
    assert "Verified deliverable content" in sm.frozen_artifacts["output_1"]


def test_query_replan_binds_new_manifest_and_makes_novel_action():
    base_plan = {
        "contract_id": "query_plan_001",
        "evidence_requirements": ["budget_burn_rate", "headcount_forecast"],
        "extensions": {},
    }

    directive_novel = {
        "directive_type": "EXPAND_CRITERIA",
        "unanswered_subquestions": ["What is Q4 marketing allocation?"],
        "suggested_query_expansion": ["marketing_spend_q4"],
    }

    new_plan = L1FeedbackStateMachine.apply_replan_to_plan(base_plan, directive_novel)

    assert new_plan["replan_status"] == "REPLAN_APPLIED"
    assert new_plan["has_novel_action"] is True
    assert new_plan["manifest_id"] == "manifest_replan_iter_1"
    assert "marketing_spend_q4" in new_plan["evidence_requirements"]

    # Applying the same directive with zero new subquestions or expansions must STOP
    directive_duplicate = {
        "directive_type": "EXPAND_CRITERIA",
        "unanswered_subquestions": ["What is Q4 marketing allocation?"],
        "suggested_query_expansion": ["marketing_spend_q4"],
    }

    second_plan = L1FeedbackStateMachine.apply_replan_to_plan(new_plan, directive_duplicate)
    assert second_plan["replan_status"] == "STOPPED_NO_NOVEL_ACTION"
    assert second_plan["has_novel_action"] is False


def test_typed_dlq_lifecycle_and_non_retryable_quarantine():
    dlq = DeadLetterQueueManager(default_max_retries=3)

    # 1. Quota classification
    assert classify_extraction_failure("HTTP 429: Insufficient quota remaining") == ExtractionFailureType.QUOTA_EXCEEDED
    # 2. Transient rate limit
    assert classify_extraction_failure("Rate limit reached. Too many requests") == ExtractionFailureType.TRANSIENT_RATE_LIMIT
    # 3. Auth failure
    assert classify_extraction_failure("401 Unauthorized access to model") == ExtractionFailureType.AUTH_FAILURE
    # 4. Schema failure
    assert classify_extraction_failure("JSONDecodeError: Malformed schema") == ExtractionFailureType.SCHEMA_VALIDATION_FAILURE
    # 5. Timeout
    assert classify_extraction_failure("DeadlineExceededError: Request timed out") == ExtractionFailureType.TIMEOUT
    # 6. L2 zero retry block
    assert classify_extraction_failure("External L2 zero_retry policy violated") == ExtractionFailureType.L2_ZERO_RETRY_BLOCKED

    # L2 zero-retry failure immediately quarantines to DEAD_LETTER on attempt 1
    state = dlq.route_failure(
        event_id="evt_l2_fail",
        evidence_id="ev_001",
        error="External L2 zero_retry violation",
        retry_count=1,
    )
    assert state == WorkItemState.DEAD_LETTER
    assert dlq._in_memory_dlq["evt_l2_fail"].failure_type == ExtractionFailureType.L2_ZERO_RETRY_BLOCKED.value

    # Transient rate limit permits retry on attempt 1
    state_retry = dlq.route_failure(
        event_id="evt_rate_limit",
        evidence_id="ev_002",
        error="429 Rate limit retry later",
        retry_count=1,
    )
    assert state_retry == WorkItemState.PENDING
    assert dlq._in_memory_dlq["evt_rate_limit"].failure_type == ExtractionFailureType.TRANSIENT_RATE_LIMIT.value
