"""Dual-Pipeline Layer 0: Budget Reservation Manager & Reconciliation.

Coordinates token and cost reservations, multi-tenant run isolation, and unacknowledged effect
reconciliation across primary, judge, and healer actions.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dual_pipeline.l0.request_context import (
    ActionRole,
    BudgetExhaustionError,
    DeadlineExceededError,
    OptionalWorkDisabledError,
    RequestContext,
    ReservationError,
    ReservationLease,
    ReservationState,
    get_clock_epoch_id,
    verify_clock_continuity,
)


class BudgetReservationManager:
    """Thread-safe manager enforcing reservation and reconciliation across request actions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _check_validity(self, context: RequestContext, action_role: str) -> None:
        if context.is_cancelled:
            raise ReservationError(f"Context is cancelled: {context.cancel_reason}")
        if context.is_expired():
            context.status = "DEADLINE_EXCEEDED"
            raise DeadlineExceededError("Request monotonic deadline exceeded")
        if context.disable_optional_work and action_role in (
            ActionRole.HEALER.value,
            ActionRole.OPTIONAL_REASONING.value,
        ):
            raise OptionalWorkDisabledError(
                f"Optional {action_role} work disabled due to clock discontinuity or budget clamp"
            )

    def reserve(
        self,
        context: RequestContext,
        *,
        estimated_cost_usd: float,
        estimated_tokens: int = 0,
        action_role: str = ActionRole.PRIMARY.value,
        reservation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> ReservationLease:
        """Reserves budget headroom prior to action dispatch."""
        with self._lock:
            self._check_validity(context, action_role)

            cost = max(0.0, float(estimated_cost_usd))
            tokens = max(0, int(estimated_tokens))

            if context.available_cost_usd() < cost:
                context.status = "BUDGET_EXHAUSTED"
                raise BudgetExhaustionError(
                    f"Cost reservation breach: requested ${cost:.4f} > available ${context.available_cost_usd():.4f}"
                )

            avail_tok = context.available_tokens()
            if avail_tok is not None and avail_tok < tokens:
                context.status = "BUDGET_EXHAUSTED"
                raise BudgetExhaustionError(
                    f"Token reservation breach: requested {tokens} > available {avail_tok}"
                )

            if action_role == ActionRole.TOOL.value:
                if context.tool_calls_count >= context.tool_calls_max:
                    raise BudgetExhaustionError("Tool call budget ceiling reached")
                context.tool_calls_count += 1
            elif action_role in (ActionRole.JUDGE.value, ActionRole.HEALER.value, ActionRole.OPTIONAL_REASONING.value):
                if context.model_calls_incremental_count >= context.model_calls_incremental_max:
                    raise BudgetExhaustionError("Incremental model call ceiling reached")
                context.model_calls_incremental_count += 1

            r_id = reservation_id or f"res_{uuid.uuid4().hex[:12]}"
            lease = ReservationLease(
                reservation_id=r_id,
                run_id=context.run_id,
                estimated_cost_usd=cost,
                estimated_tokens=tokens,
                action_role=action_role,
                created_monotonic=time.monotonic(),
                created_at_iso=datetime.now(timezone.utc).isoformat(),
                idempotency_key=idempotency_key,
            )

            context.reserved_cost_usd = round(context.reserved_cost_usd + cost, 6)
            context.reserved_tokens += tokens
            context.reservations[r_id] = lease
            return lease

    def reconcile(
        self,
        context: RequestContext,
        reservation_id: str,
        *,
        actual_cost_usd: float,
        actual_tokens: int = 0,
        terminal_status: str = "COMPLETE",
        unacknowledged_effect: bool = False,
    ) -> ReservationLease:
        """Reconciles actual expenditures against an existing reservation lease."""
        with self._lock:
            lease = context.reservations.get(reservation_id)
            if lease is None:
                raise KeyError(f"Reservation {reservation_id} not found in context {context.run_id}")

            if lease.state == ReservationState.RECONCILED.value:
                return lease

            act_cost = max(0.0, float(actual_cost_usd))
            act_tok = max(0, int(actual_tokens))

            context.reserved_cost_usd = max(0.0, round(context.reserved_cost_usd - lease.estimated_cost_usd, 6))
            context.reserved_tokens = max(0, context.reserved_tokens - lease.estimated_tokens)
            context.consumed_cost_usd = round(context.consumed_cost_usd + act_cost, 6)
            context.consumed_tokens += act_tok

            lease.actual_cost_usd = act_cost
            lease.actual_tokens = act_tok
            lease.reconciled_at_iso = datetime.now(timezone.utc).isoformat()

            if unacknowledged_effect:
                lease.state = ReservationState.PENDING_RECONCILIATION.value
                lease.unacknowledged_effect = True
            else:
                lease.state = ReservationState.RECONCILED.value

            if lease.action_role == ActionRole.PRIMARY.value:
                context.primary_action_count += 1
            elif lease.action_role == ActionRole.JUDGE.value:
                context.judge_action_count += 1
            elif lease.action_role == ActionRole.HEALER.value:
                context.healer_action_count += 1

            if context.consumed_cost_usd > context.max_cost_usd:
                context.status = "BUDGET_EXHAUSTED"

            return lease

    def reconcile_crash_recovery(
        self,
        context: RequestContext,
        reservation_id: str,
        *,
        independent_verified_cost: Optional[float] = None,
        independent_verified_tokens: Optional[int] = None,
        acknowledged: bool = True,
    ) -> ReservationLease:
        """Resolves unacknowledged effects after crash recovery through independent verification."""
        with self._lock:
            lease = context.reservations.get(reservation_id)
            if lease is None:
                raise KeyError(f"Reservation {reservation_id} not found")

            if not lease.unacknowledged_effect and lease.state != ReservationState.PENDING_RECONCILIATION.value:
                return lease

            if not acknowledged:
                raise ReservationError(
                    f"Lease {reservation_id} has unacknowledged external effects; requires independent verification"
                )

            if independent_verified_cost is not None:
                diff_cost = max(0.0, independent_verified_cost) - lease.actual_cost_usd
                context.consumed_cost_usd = round(context.consumed_cost_usd + diff_cost, 6)
                lease.actual_cost_usd = max(0.0, independent_verified_cost)

            if independent_verified_tokens is not None:
                diff_tok = max(0, independent_verified_tokens) - lease.actual_tokens
                context.consumed_tokens += diff_tok
                lease.actual_tokens = max(0, independent_verified_tokens)

            lease.unacknowledged_effect = False
            lease.state = ReservationState.RECONCILED.value
            return lease

    @classmethod
    def restore_context_after_crash(cls, data: Dict[str, Any]) -> RequestContext:
        """Restores context from persistent storage and enforces clock continuity."""
        ctx = RequestContext.from_dict(data)
        is_cont, eff_deadline, is_exp = verify_clock_continuity(
            ctx.clock_epoch_id,
            ctx.deadline_monotonic,
            ctx.expiry_iso,
        )

        ctx.deadline_monotonic = eff_deadline
        if is_exp:
            ctx.status = "DEADLINE_EXCEEDED"

        if not is_cont:
            ctx.disable_optional_work = True
            ctx.clock_epoch_id = get_clock_epoch_id()

        return ctx


_GLOBAL_RESERVATION_MANAGER = BudgetReservationManager()


def get_reservation_manager() -> BudgetReservationManager:
    """Returns the singleton reservation manager."""
    return _GLOBAL_RESERVATION_MANAGER


__all__ = [
    "BudgetReservationManager",
    "get_reservation_manager",
]
