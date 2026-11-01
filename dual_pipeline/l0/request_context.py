"""Dual-Pipeline Layer 0: Request Context, Monotonic Deadlines, & Budget Contracts.

Governs request-scoped resource accounting, clock epoch continuity, and multi-tenant isolation.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class ReservationState(str, Enum):
    """Lifecycle state of an allocated budget reservation."""
    ACTIVE = "ACTIVE"
    RECONCILED = "RECONCILED"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ActionRole(str, Enum):
    """Categorization of actions competing for shared request budgets."""
    PRIMARY = "primary"
    JUDGE = "judge"
    HEALER = "healer"
    TOOL = "tool"
    OPTIONAL_REASONING = "optional_reasoning"


class BudgetExhaustionError(RuntimeError):
    """Raised when an operation would exceed the request budget ceiling."""


class OptionalWorkDisabledError(RuntimeError):
    """Raised when optional reasoning or healer work is attempted after clock warp or budget clamp."""


class DeadlineExceededError(TimeoutError):
    """Raised when the request deadline monotonic time has passed."""


class ReservationError(RuntimeError):
    """Raised on invalid reservation state transitions or unacknowledged leases."""


def get_clock_epoch_id() -> str:
    """Returns a stable machine/session boot identifier to detect clock discontinuities."""
    override = os.environ.get("NEE_CLOCK_EPOCH_ID")
    if override:
        return override.strip()

    if sys.platform == "darwin":
        try:
            res = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        boot_id_path = "/proc/sys/kernel/random/boot_id"
        if os.path.isfile(boot_id_path):
            try:
                with open(boot_id_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass

    return f"epoch_fallback_{os.getpid()}"


def verify_clock_continuity(
    recorded_epoch_id: str,
    recorded_monotonic_deadline: float,
    recorded_expiry_iso: str,
) -> Tuple[bool, float, bool]:
    """Verifies monotonic continuity against host clock epoch."""
    now_mono = time.monotonic()
    current_epoch = get_clock_epoch_id()

    if recorded_epoch_id and current_epoch == recorded_epoch_id:
        is_expired = now_mono >= recorded_monotonic_deadline
        return True, recorded_monotonic_deadline, is_expired

    try:
        exp_dt = datetime.fromisoformat(recorded_expiry_iso.replace("Z", "+00:00"))
        now_dt = datetime.now(timezone.utc)
        remaining_sec = (exp_dt - now_dt).total_seconds()
        if remaining_sec <= 0:
            return False, now_mono, True
        return False, now_mono + remaining_sec, False
    except Exception:
        return False, now_mono, True


@dataclass
class ReservationLease:
    """A bounded lease reserving token and cost headroom for a single action."""
    reservation_id: str
    run_id: str
    estimated_cost_usd: float
    estimated_tokens: int
    action_role: str
    created_monotonic: float
    created_at_iso: str
    state: str = ReservationState.ACTIVE.value
    actual_cost_usd: float = 0.0
    actual_tokens: int = 0
    reconciled_at_iso: Optional[str] = None
    unacknowledged_effect: bool = False
    idempotency_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReservationLease:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class RequestContext:
    """Thread-safe and multi-tenant isolated context tracking request budgets and lifecycle."""
    run_id: str
    request_id: str
    engagement_id: str = "default"
    clock_epoch_id: str = field(default_factory=get_clock_epoch_id)
    expiry_iso: str = ""
    deadline_monotonic: float = 0.0
    max_cost_usd: float = 15.0
    consumed_cost_usd: float = 0.0
    reserved_cost_usd: float = 0.0
    max_tokens: Optional[int] = None
    consumed_tokens: int = 0
    reserved_tokens: int = 0
    tool_calls_max: int = 8
    tool_calls_count: int = 0
    model_calls_incremental_max: int = 2
    model_calls_incremental_count: int = 0
    primary_action_count: int = 0
    judge_action_count: int = 0
    healer_action_count: int = 0
    disable_optional_work: bool = False
    is_cancelled: bool = False
    cancel_reason: Optional[str] = None
    status: str = "ACTIVE"
    reservations: Dict[str, ReservationLease] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def time_remaining(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def is_expired(self) -> bool:
        return time.monotonic() >= self.deadline_monotonic

    def available_cost_usd(self) -> float:
        headroom = self.max_cost_usd - (self.consumed_cost_usd + self.reserved_cost_usd)
        return max(0.0, round(headroom, 6))

    def available_tokens(self) -> Optional[int]:
        if self.max_tokens is None:
            return None
        headroom = self.max_tokens - (self.consumed_tokens + self.reserved_tokens)
        return max(0, headroom)

    def cancel(self, reason: str = "USER_CANCELLED") -> None:
        self.is_cancelled = True
        self.cancel_reason = reason
        self.status = "CANCELLED"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["reservations"] = {k: v.to_dict() for k, v in self.reservations.items()}
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RequestContext:
        clean = dict(data)
        raw_res = clean.pop("reservations", {})
        reservations = {
            r_id: ReservationLease.from_dict(r_data)
            for r_id, r_data in raw_res.items()
            if isinstance(r_data, dict)
        }
        ctx = cls(**{k: v for k, v in clean.items() if k in cls.__dataclass_fields__})
        ctx.reservations = reservations
        return ctx

    @classmethod
    def create(
        cls,
        *,
        run_id: Optional[str] = None,
        request_id: Optional[str] = None,
        engagement_id: str = "default",
        timeout_seconds: float = 60.0,
        max_cost_usd: float = 15.0,
        max_tokens: Optional[int] = None,
        tool_calls_max: int = 8,
        model_calls_incremental_max: int = 2,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RequestContext:
        r_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        req_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
        now_dt = datetime.now(timezone.utc)
        exp_dt = datetime.fromtimestamp(now_dt.timestamp() + timeout_seconds, tz=timezone.utc)
        deadline_mono = time.monotonic() + max(0.001, timeout_seconds)

        return cls(
            run_id=r_id,
            request_id=req_id,
            engagement_id=engagement_id,
            clock_epoch_id=get_clock_epoch_id(),
            expiry_iso=exp_dt.isoformat(),
            deadline_monotonic=deadline_mono,
            max_cost_usd=float(max_cost_usd),
            max_tokens=max_tokens,
            tool_calls_max=tool_calls_max,
            model_calls_incremental_max=model_calls_incremental_max,
            metadata=dict(metadata or {}),
        )


__all__ = [
    "ActionRole",
    "BudgetExhaustionError",
    "DeadlineExceededError",
    "OptionalWorkDisabledError",
    "RequestContext",
    "ReservationError",
    "ReservationLease",
    "ReservationState",
    "get_clock_epoch_id",
    "verify_clock_continuity",
]
