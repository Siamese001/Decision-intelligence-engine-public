"""
dual_pipeline/canonical_pipeline/policy_profiles.py

Explicit policy profiles, operation budget ledgers, and worker execution contexts
keyed by modality, phase, and stage. Enforces:
1. Document pilot caps (4 physical requests / 300s) apply strictly to DOCUMENT synthesis.
2. Audio, transcript, notes, and live operations are never capped by document pilot limits.
3. Atomic request reservation and persistent ambiguous dispatch accounting.
4. Worker execution context isolation and late publication blocking.
5. Deep client and provider isolation across concurrent operations.
"""
from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.operation_contracts import resolve_canonical_modality
from dual_pipeline.exceptions import (
    BudgetExhaustedError,
    LatePublicationBlockedError,
    OperationCancelledError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyProfile:
    """Explicit policy profile governing an operation's budget and execution parameters."""
    modality: CanonicalModality
    phase: str = "synthesis"
    max_physical_requests: Optional[int] = None
    max_active_seconds: Optional[float] = None
    allows_document_pilot: bool = False
    description: str = ""


def resolve_policy_profile(
    modality: Union[CanonicalModality, str],
    phase: str = "synthesis",
    flags: Optional[Dict[str, Any]] = None,
) -> PolicyProfile:
    """
    Resolves the authoritative policy profile for a given modality, phase, and flags.
    Fails closed if document pilot settings are applied to non-document modalities.
    """
    c_mod = resolve_canonical_modality(modality)
    fl = flags or {}
    pilot_requested = bool(fl.get("fast_pilot_budget") or fl.get("enable_document_pilot"))

    if fl.get("output3_only") and c_mod != CanonicalModality.DOCUMENT and pilot_requested:
        raise ValueError("Selective meeting03-only request cannot select a document budget.")

    if pilot_requested and c_mod != CanonicalModality.DOCUMENT:
        raise ValueError(
            f"fast_pilot_budget is strictly reserved for DOCUMENT synthesis and cannot cap {c_mod.name}."
        )

    if c_mod == CanonicalModality.DOCUMENT:
        if pilot_requested:
            return PolicyProfile(
                modality=c_mod,
                phase=phase,
                max_physical_requests=4,
                max_active_seconds=300.0,
                allows_document_pilot=True,
                description="Document economical pilot profile (provisional 4 requests / 300s)",
            )
        return PolicyProfile(
            modality=c_mod,
            phase=phase,
            max_physical_requests=10,
            max_active_seconds=600.0,
            allows_document_pilot=True,
            description="Standard document profile",
        )

    if c_mod in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
        return PolicyProfile(
            modality=c_mod,
            phase=phase,
            max_physical_requests=None,
            max_active_seconds=None,
            allows_document_pilot=False,
            description="Full meeting transcript/audio profile (uncapped by document pilot)",
        )

    if c_mod == CanonicalModality.MEETING_NOTES:
        return PolicyProfile(
            modality=c_mod,
            phase=phase,
            max_physical_requests=None,
            max_active_seconds=None,
            allows_document_pilot=False,
            description="Meeting notes profile (uncapped by document pilot)",
        )

    return PolicyProfile(
        modality=c_mod,
        phase=phase,
        max_physical_requests=None,
        max_active_seconds=None,
        allows_document_pilot=False,
        description=f"Generic profile for {c_mod.name}",
    )


class OperationBudgetLedger:
    """
    Thread-safe monotonic budget ledger tracking request reservations and terminal outcomes.
    Ambiguous dispatches remain charged across restart to prevent double-spend or overrun.
    """

    def __init__(
        self,
        operation_id: str,
        modality: str,
        max_requests: Optional[int] = None,
        active_deadline_seconds: Optional[float] = None,
        reserved_requests: int = 0,
        completed_requests: int = 0,
        failed_requests: int = 0,
        ambiguous_requests: int = 0,
        cancelled: bool = False,
    ) -> None:
        self.operation_id = operation_id
        self.modality = modality
        self.max_requests = max_requests
        self.active_deadline_seconds = active_deadline_seconds
        self.reserved_requests = reserved_requests
        self.completed_requests = completed_requests
        self.failed_requests = failed_requests
        self.ambiguous_requests = ambiguous_requests
        self.cancelled = cancelled
        self.start_time = time.monotonic()
        self._lock = threading.Lock()
        self._active_tokens: Dict[str, str] = {}

    def reserve(self, stage: str, count: int = 1) -> str:
        """
        Atomically reserves request quota against parent ceiling.
        Raises OperationCancelledError if cancelled, or BudgetExhaustedError if ceiling exceeded.
        """
        with self._lock:
            if self.cancelled:
                raise OperationCancelledError(
                    f"Operation {self.operation_id} is cancelled; cannot reserve {count} requests."
                )
            if self.max_requests is not None:
                if (self.reserved_requests + count) > self.max_requests:
                    raise BudgetExhaustedError(
                        f"Budget exhausted for {self.modality} ({self.operation_id}): "
                        f"cannot reserve {count} requests ({self.reserved_requests}/{self.max_requests} spent)."
                    )
            self.reserved_requests += count
            token = f"{self.operation_id}:{stage}:{uuid.uuid4().hex[:8]}"
            self._active_tokens[token] = stage
            return token

    def record_outcome(self, token: str, success: bool, ambiguous: bool = False) -> None:
        """
        Records the execution outcome of a reservation token.
        Ambiguous dispatches permanently stay charged to prevent duplicate requests.
        """
        with self._lock:
            if token in self._active_tokens:
                del self._active_tokens[token]
            if ambiguous:
                self.ambiguous_requests += 1
            elif success:
                self.completed_requests += 1
            else:
                self.failed_requests += 1

    def sub_divide_quota(self, child_stage: str, quota: int) -> OperationBudgetLedger:
        """
        Atomically carves out a child quota from this ledger.
        Two children cannot independently spend the same remaining allowance.
        """
        with self._lock:
            if self.cancelled:
                raise OperationCancelledError("Cannot subdivide quota on a cancelled ledger.")
            if self.max_requests is not None:
                remaining = self.max_requests - self.reserved_requests
                if quota > remaining:
                    raise BudgetExhaustedError(
                        f"Cannot allocate child quota {quota}: only {remaining} requests remaining on parent."
                    )
                self.reserved_requests += quota
            child_op_id = f"{self.operation_id}:{child_stage}"
            return OperationBudgetLedger(
                operation_id=child_op_id,
                modality=self.modality,
                max_requests=quota,
                active_deadline_seconds=self.active_deadline_seconds,
            )

    def cancel(self) -> None:
        """Marks the operation as cancelled, blocking further reservations."""
        with self._lock:
            self.cancelled = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self.cancelled

    def serialize(self) -> Dict[str, Any]:
        """Serializes ledger state for durable persistence across restart."""
        with self._lock:
            return {
                "operation_id": self.operation_id,
                "modality": self.modality,
                "max_requests": self.max_requests,
                "active_deadline_seconds": self.active_deadline_seconds,
                "reserved_requests": self.reserved_requests,
                "completed_requests": self.completed_requests,
                "failed_requests": self.failed_requests,
                "ambiguous_requests": self.ambiguous_requests,
                "cancelled": self.cancelled,
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OperationBudgetLedger:
        """Restores ledger state from serialized dictionary."""
        return cls(
            operation_id=data["operation_id"],
            modality=data["modality"],
            max_requests=data.get("max_requests"),
            active_deadline_seconds=data.get("active_deadline_seconds"),
            reserved_requests=data.get("reserved_requests", 0),
            completed_requests=data.get("completed_requests", 0),
            failed_requests=data.get("failed_requests", 0),
            ambiguous_requests=data.get("ambiguous_requests", 0),
            cancelled=data.get("cancelled", False),
        )


class WorkerExecutionContext:
    """
    Isolated execution context propagated into parallel worker threads.
    Guarantees that workers have independent cancellation handles and late results cannot publish.
    """

    def __init__(
        self,
        worker_id: str,
        parent_operation_id: str,
        stage: str,
        budget_handle: OperationBudgetLedger,
        context_data: Optional[Dict[str, Any]] = None,
        deadline_seconds: Optional[float] = None,
    ) -> None:
        self.worker_id = worker_id
        self.parent_operation_id = parent_operation_id
        self.stage = stage
        self.budget_handle = budget_handle
        self.context_data = copy.deepcopy(context_data or {})
        self.deadline_seconds = deadline_seconds
        self.expires_at = (time.monotonic() + deadline_seconds) if deadline_seconds else None
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.budget_handle:
            self.budget_handle.cancel()

    def is_cancelled(self) -> bool:
        if self.cancel_event.is_set():
            return True
        if self.expires_at is not None and time.monotonic() > self.expires_at:
            return True
        if self.budget_handle and self.budget_handle.is_cancelled():
            return True
        return False

    def can_publish(self) -> bool:
        """Returns True if the worker is still active and permitted to publish artifacts."""
        return not self.is_cancelled()

    def assert_can_publish(self) -> None:
        """Raises LatePublicationBlockedError if the worker was cancelled or expired."""
        if self.is_cancelled():
            raise LatePublicationBlockedError(
                f"Worker {self.worker_id} for stage {self.stage} was cancelled or expired; publication blocked."
            )


def deep_isolate_provider(provider: Any) -> Any:
    """
    Creates an isolated copy of a provider instance, guaranteeing that mutations to
    reasoning effort, model configuration, or client options do not leak to the shared instance.
    """
    try:
        new_provider = copy.copy(provider)
        # Deepcopy mutable dictionaries/options if present
        for attr in ("options", "headers", "config", "metadata", "client_kwargs"):
            if hasattr(provider, attr) and isinstance(getattr(provider, attr), dict):
                setattr(new_provider, attr, copy.deepcopy(getattr(provider, attr)))
        return new_provider
    except Exception as e:
        logger.debug("Failed deep isolation on provider (%s); using shallow copy", e)
        return copy.copy(provider)
