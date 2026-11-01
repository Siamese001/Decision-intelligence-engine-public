"""dual_pipeline.operational_outcomes_core.adapter

Operational outcome adapter for UI and tooling:
1. UI completion/stage/activation labels derive strictly from actual run
   and independent state readback.
2. Fixture markers never count as real audits.
3. Errors, pending, and partial states remain visible and are never masked.
4. Explicit operator feedback remains cryptographically bound to delivered results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, Optional, Sequence

from dual_pipeline.core import utc_now

logger = logging.getLogger(__name__)


class AdapterState(str, Enum):
    """Observable lifecycle and operational states."""
    COMPLETED = "COMPLETED"
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class AdapterStateResult:
    """Immutable result of independent state derivation."""
    state: AdapterState
    is_authentic_audit: bool
    rationale: str
    verified_stages: Sequence[str] = field(default_factory=tuple)
    missing_stages: Sequence[str] = field(default_factory=tuple)
    details: Dict[str, Any] = field(default_factory=dict)


class OperationalOutcomeAdapter:
    """Translates execution evidence and independent state readback into observable UI states."""

    def __init__(self, run_id: Optional[str] = None) -> None:
        self.run_id = run_id or "run-operational-adapter"

    def derive_lifecycle_state(
        self,
        run_context: Dict[str, Any],
        independent_readback: Optional[Dict[str, Any]] = None,
    ) -> AdapterStateResult:
        """Derives UI completion, stage, or activation labels from observed evidence.
        
        Enforces that fixture markers (mock_audit, synthetic_fixture) are never accepted
        as real audits, and independent state readback is required for COMPLETED.
        """
        if not run_context:
            return AdapterStateResult(
                state=AdapterState.UNVERIFIED,
                is_authentic_audit=False,
                rationale="Empty run context cannot establish lifecycle state.",
            )

        # Invariant 1: Fixture markers never count as real audits
        if (
            run_context.get("mock_audit")
            or run_context.get("is_fixture")
            or run_context.get("synthetic_fixture")
        ):
            return AdapterStateResult(
                state=AdapterState.UNVERIFIED,
                is_authentic_audit=False,
                rationale="REJECTED_FIXTURE_MARKER: Synthetic or mock fixtures cannot qualify real audits.",
            )

        # Invariant 2: Independent state readback is required to verify real state
        if not independent_readback:
            declared_status = run_context.get("status", "PENDING").upper()
            state = AdapterState.PENDING if declared_status == "PENDING" else AdapterState.UNVERIFIED
            return AdapterStateResult(
                state=state,
                is_authentic_audit=False,
                rationale="Missing independent state readback; state cannot be confirmed.",
            )

        # Invariant 3: Errors and partial states must remain visible
        if independent_readback.get("has_errors") or independent_readback.get("terminal_error"):
            err_msg = independent_readback.get("error_message", "Unknown runtime error observed.")
            return AdapterStateResult(
                state=AdapterState.ERROR,
                is_authentic_audit=True,
                rationale=f"Observed runtime error: {err_msg}",
                details={"error": err_msg},
            )

        required_stages = run_context.get("expected_stages", ["U0", "L1", "L0", "C0", "L2"])
        completed_stages = independent_readback.get("completed_stages", [])
        missing = [s for s in required_stages if s not in completed_stages]

        if missing:
            return AdapterStateResult(
                state=AdapterState.PARTIAL,
                is_authentic_audit=True,
                rationale=f"Execution partial: {len(missing)} expected stage(s) incomplete.",
                verified_stages=tuple(completed_stages),
                missing_stages=tuple(missing),
            )

        # Invariant 4: Verified real completion
        if independent_readback.get("verified_on_disk", False):
            return AdapterStateResult(
                state=AdapterState.COMPLETED,
                is_authentic_audit=True,
                rationale="All lifecycle stages and state independently verified on disk.",
                verified_stages=tuple(completed_stages),
            )

        return AdapterStateResult(
            state=AdapterState.UNVERIFIED,
            is_authentic_audit=False,
            rationale="Independent readback did not confirm persistent on-disk state.",
        )

    def bind_delivered_feedback(
        self,
        result_id: str,
        receipt_digest: str,
        feedback_payload: Dict[str, Any],
        verified_receipts_index: Dict[str, str],
    ) -> Dict[str, Any]:
        """Binds explicit operator feedback strictly to the authentic delivered result."""
        clean_res_id = str(result_id or "").strip()
        clean_digest = str(receipt_digest or "").strip()

        if not clean_res_id or not clean_digest:
            raise ValueError("BIND_FEEDBACK_ERROR: result_id and receipt_digest must be non-empty.")

        expected_digest = verified_receipts_index.get(clean_res_id)
        if not expected_digest or expected_digest != clean_digest:
            raise ValueError(
                f"RECEIPT_DIGEST_MISMATCH: Feedback digest '{clean_digest}' does not match "
                f"verified receipt digest '{expected_digest}' for result '{clean_res_id}'."
            )

        return {
            "status": "BOUND",
            "result_id": clean_res_id,
            "receipt_digest": clean_digest,
            "feedback": dict(feedback_payload),
            "bound_at": utc_now(),
        }
