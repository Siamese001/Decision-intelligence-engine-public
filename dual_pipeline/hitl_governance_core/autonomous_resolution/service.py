"""
dual_pipeline/hitl_governance_core/autonomous_resolution/service.py

Governed Autonomous Decision Resolution Service.
Orchestrates eligibility evaluation, cryptographic signing, append-only SQLite persistence,
monotonic JSON projection, and readback verification under bounded retry and time budgets.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..decision_clearance_core.models import (
    ClearanceVerificationRequest,
    ClearanceVerificationResult,
)
from ..decision_clearance_core.verifier import verify_decision_clearance
from ..runtime_tool_adapter import (
    discover_runtime_capabilities,
    dispatch_interactive_question,
    validate_approval_response_signature,
)
from .eligibility import evaluate_decision_request
from .models import (
    AutonomousResolutionRecord,
    DecisionOption,
    DecisionRequest,
    EligibilityEvaluationResult,
    PolicySnapshot,
    ResolutionTerminalResult,
)
from .policy import load_active_policy_snapshot
from .projection import export_projection
from .rollout import (
    RolloutConfiguration,
    RolloutMode,
    evaluate_rollout_authorization,
)
from .signing import generate_autonomous_keypair
from .storage import AutonomousResolutionStore, ConflictResolutionError


@dataclass
class AutonomousDecisionResult:
    """Outcome of resolving a decision through the governed decision lifecycle."""

    decision_id: str
    status: str
    selected_option: Optional[str] = None
    terminal_result: ResolutionTerminalResult = ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
    evaluation: Optional[EligibilityEvaluationResult] = None
    record: Optional[AutonomousResolutionRecord] = None
    verification_result: Optional[ClearanceVerificationResult] = None
    human_response: Optional[Dict[str, Any]] = None
    display_receipt: Optional[Dict[str, Any]] = None
    transport_call_count: int = 0
    error_message: Optional[str] = None
    retry_count: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "status": self.status,
            "selected_option": self.selected_option,
            "terminal_result": self.terminal_result.value,
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
            "record": self.record.to_dict() if self.record else None,
            "verification_result": (
                {
                    "cleared": self.verification_result.cleared,
                    "status_code": self.verification_result.status_code,
                    "reason": self.verification_result.reason,
                }
                if self.verification_result
                else None
            ),
            "human_response": self.human_response,
            "display_receipt": self.display_receipt,
            "transport_call_count": self.transport_call_count,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "elapsed_seconds": self.elapsed_seconds,
        }


def _validate_request(request: DecisionRequest) -> Optional[str]:
    """Validate request boundaries, types, and confidence values."""
    if not request.decision_id or not str(request.decision_id).strip():
        return "Decision ID cannot be empty."
    if len(request.options) < 2:
        return "Decision requires at least two distinct options."
    for opt in request.options:
        conf = opt.confidence
        if not isinstance(conf, (int, float)) or math.isnan(conf) or math.isinf(conf):
            return f"Option '{opt.option_id}' has non-finite confidence: {conf}"
        if conf < 0.0 or conf > 1.0:
            return f"Option '{opt.option_id}' confidence {conf} out of range [0.0, 1.0]"
    return None


def _extract_norm_selection(resp: Dict[str, Any]) -> str:
    """Extract normalized selection text from a human response payload."""
    inner = resp.get("result")
    if isinstance(inner, dict):
        raw_sel = inner.get("selected_option") or inner.get("selected_key") or inner.get("result") or ""
    elif isinstance(inner, str):
        raw_sel = inner
    else:
        raw_sel = resp.get("selected_option") or resp.get("selected_key") or ""
    return str(raw_sel).strip().lower()


def _resolve_option_id(request: DecisionRequest, norm_sel: str) -> str:
    """Match normalized selection string to an exact option ID."""
    for o in request.options:
        if norm_sel == o.option_id.lower() or norm_sel in o.description.lower():
            return o.option_id
    return norm_sel


def _process_human_response(
    dispatch_res: Dict[str, Any],
    request: DecisionRequest,
    eval_res: EligibilityEvaluationResult,
    start_time: float,
) -> AutonomousDecisionResult:
    """Validate and process an operator response from transport."""
    allowed = [o.option_id for o in request.options] + [o.description for o in request.options]
    valid_origin, origin_reason = validate_approval_response_signature(
        dispatch_res.get("response"), allowed_options=allowed
    )
    if not valid_origin:
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="AUTHENTICATION_ERROR",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.AUTHENTICATION_ERROR,
            evaluation=eval_res,
            human_response=dispatch_res.get("response"),
            display_receipt=dispatch_res.get("display_receipt"),
            error_message=origin_reason,
            transport_call_count=1,
            elapsed_seconds=time.monotonic() - start_time,
        )

    resp = dispatch_res.get("response") or {}
    norm_sel = _extract_norm_selection(resp)

    if norm_sel in ("decline", "declined", "cancel", "cancelled", "reject", "rejected"):
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="DECLINED" if "decline" in norm_sel else "CANCELLED",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
            evaluation=eval_res,
            human_response=dispatch_res.get("response"),
            display_receipt=dispatch_res.get("display_receipt"),
            transport_call_count=1,
            elapsed_seconds=time.monotonic() - start_time,
        )

    resolved_opt_id = _resolve_option_id(request, norm_sel)
    return AutonomousDecisionResult(
        decision_id=request.decision_id,
        status="RESOLVED_BY_HUMAN",
        selected_option=resolved_opt_id,
        terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
        evaluation=eval_res,
        human_response=dispatch_res.get("response"),
        display_receipt=dispatch_res.get("display_receipt"),
        transport_call_count=1,
        elapsed_seconds=time.monotonic() - start_time,
    )


def _handle_human_review_dispatch(
    request: DecisionRequest,
    eval_res: EligibilityEvaluationResult,
    transport: Optional[Callable[[Dict[str, Any]], Any]],
    target_mode: str,
    host_qualification_evidence: Optional[Dict[str, Any]],
    start_time: float,
) -> AutonomousDecisionResult:
    """Dispatches human review if transport is provided and host is qualified."""
    if transport is None:
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="AWAITING_HUMAN_REVIEW",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
            evaluation=eval_res,
            transport_call_count=0,
            elapsed_seconds=time.monotonic() - start_time,
        )

    caps = discover_runtime_capabilities(
        target_mode=target_mode,
        host_qualification_evidence=host_qualification_evidence,
    )
    if not caps.get("supported"):
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="UNSUPPORTED_HOST",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.UNSUPPORTED_HOST,
            evaluation=eval_res,
            error_message="Host environment is unqualified or unsupported.",
            transport_call_count=0,
            elapsed_seconds=time.monotonic() - start_time,
        )

    formatted_opts = [{"display_text": o.description, "option_id": o.option_id} for o in request.options]
    question_text = request.action_description or request.decision_id
    dispatch_res = dispatch_interactive_question(
        question=question_text,
        options=formatted_opts,
        transport=transport,
        target_mode=target_mode,
        request_id=f"req-{request.decision_id}",
    )

    if dispatch_res.get("status") == "COMPLETED":
        return _process_human_response(dispatch_res, request, eval_res, start_time)

    return AutonomousDecisionResult(
        decision_id=request.decision_id,
        status="DISPATCH_FAILED",
        selected_option=None,
        terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
        evaluation=eval_res,
        error_message=dispatch_res.get("error", "Dispatch failed"),
        display_receipt=dispatch_res.get("display_receipt"),
        transport_call_count=1,
        elapsed_seconds=time.monotonic() - start_time,
    )


def _commit_and_project_resolution(
    request: DecisionRequest,
    eval_res: EligibilityEvaluationResult,
    store: AutonomousResolutionStore,
    effective_ledger: Path,
    signing_key: Ed25519PrivateKey,
    signer_id: str,
    max_retries: int,
    timeout_seconds: float,
    start_time: float,
) -> Tuple[Optional[AutonomousResolutionRecord], Optional[AutonomousDecisionResult], int]:
    """Execute retryable persistence and projection export powered by tenacity."""
    import tenacity

    retry_count = 0
    rec: Optional[AutonomousResolutionRecord] = None

    def _attempt_commit() -> AutonomousResolutionRecord:
        nonlocal retry_count
        if time.monotonic() - start_time > timeout_seconds:
            raise TimeoutError(f"Timeout ({timeout_seconds}s) exceeded during storage commit.")
        try:
            return store.record_resolution(
                request=request,
                eval_result=eval_res,
                private_key=signing_key,
                signer_id=signer_id,
            )
        except ConflictResolutionError:
            raise
        except Exception:
            retry_count += 1
            raise

    try:
        retrying = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(max_retries + 1),
            wait=tenacity.wait_exponential(multiplier=0.05, exp_base=2.0),
            retry=tenacity.retry_if_not_exception_type((ConflictResolutionError, TimeoutError)),
            reraise=True,
        )
        rec = retrying(_attempt_commit)
    except TimeoutError as exc:
        err = AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="TIMEOUT",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.STORAGE_ERROR,
            evaluation=eval_res,
            error_message=str(exc),
            retry_count=retry_count,
            elapsed_seconds=time.monotonic() - start_time,
        )
        return None, err, retry_count
    except ConflictResolutionError as exc:
        err = AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="POLICY_CONFLICT",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.POLICY_CONFLICT,
            evaluation=eval_res,
            error_message=f"Conflict detected for decision: {exc}",
            retry_count=retry_count,
            elapsed_seconds=time.monotonic() - start_time,
        )
        return None, err, retry_count
    except Exception as exc:
        err = AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="STORAGE_ERROR",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.STORAGE_ERROR,
            evaluation=eval_res,
            error_message=f"Storage commit failed after {max_retries} retries: {exc}",
            retry_count=retry_count,
            elapsed_seconds=time.monotonic() - start_time,
        )
        return None, err, retry_count

    try:
        export_projection(store, effective_ledger)
    except Exception as exc:
        err = AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="STORAGE_ERROR",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.STORAGE_ERROR,
            evaluation=eval_res,
            error_message=f"Projection export failed: {exc}",
            retry_count=retry_count,
            elapsed_seconds=time.monotonic() - start_time,
        )
        return None, err, retry_count

    return rec, None, retry_count


def _verify_clearance_readback(
    request: DecisionRequest,
    eval_res: EligibilityEvaluationResult,
    effective_db: Path,
    effective_ledger: Path,
    rec: AutonomousResolutionRecord,
    retry_count: int,
    start_time: float,
) -> AutonomousDecisionResult:
    """Verifies that the committed resolution passes readback clearance."""
    verif_req = ClearanceVerificationRequest(
        decision_id=request.decision_id,
        wave=request.scope.wave,
        run_id=request.scope.run_id,
        worktree=request.scope.worktree,
        expected_action_target=request.scope.action_target,
        require_human_only=False,
    )
    verif_res = verify_decision_clearance(
        verif_req,
        sqlite_path=effective_db,
        json_path=effective_ledger,
    )

    if not verif_res.cleared:
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="STORAGE_ERROR",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.STORAGE_ERROR,
            evaluation=eval_res,
            verification_result=verif_res,
            error_message=f"Readback clearance verification failed: {verif_res.reason}",
            transport_call_count=0,
            retry_count=retry_count,
            elapsed_seconds=time.monotonic() - start_time,
        )

    return AutonomousDecisionResult(
        decision_id=request.decision_id,
        status="RESOLVED_AUTONOMOUSLY",
        selected_option=eval_res.selected_option_id,
        terminal_result=ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY,
        evaluation=eval_res,
        record=rec,
        verification_result=verif_res,
        transport_call_count=0,
        retry_count=retry_count,
        elapsed_seconds=time.monotonic() - start_time,
    )


def resolve_governed_decision(
    request: DecisionRequest,
    runtime_root: Optional[Union[str, Path]] = None,
    db_path: Optional[Union[str, Path]] = None,
    ledger_path: Optional[Union[str, Path]] = None,
    policy_snapshot: Optional[PolicySnapshot] = None,
    private_key: Optional[Ed25519PrivateKey] = None,
    signer_id: str = "engine_autonomous_authority_01",
    transport: Optional[Callable[[Dict[str, Any]], Any]] = None,
    target_mode: str = "ide",
    host_qualification_evidence: Optional[Dict[str, Any]] = None,
    require_human_only: bool = False,
    max_retries: int = 2,
    timeout_seconds: float = 30.0,
    rollout_config: Optional[RolloutConfiguration] = None,
) -> AutonomousDecisionResult:
    """Orchestrates governed decision resolution under bounded budgets.

    Evaluates eligibility, signs and commits autonomous resolutions to Layer-0 store,
    verifies clearance readback, or dispatches atomic human review when required.
    """
    start_time = time.monotonic()

    # 1. Validate request boundaries
    val_err = _validate_request(request)
    if val_err:
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="INVALID_INPUT",
            terminal_result=ResolutionTerminalResult.INVALID_INPUT,
            error_message=val_err,
            elapsed_seconds=time.monotonic() - start_time,
        )

    # 2. Resolve policy snapshot
    if policy_snapshot is None:
        try:
            policy_snapshot = load_active_policy_snapshot()
        except Exception as exc:
            return AutonomousDecisionResult(
                decision_id=request.decision_id,
                status="POLICY_CONFLICT",
                terminal_result=ResolutionTerminalResult.POLICY_CONFLICT,
                error_message=f"Failed to load active policy snapshot: {exc}",
                elapsed_seconds=time.monotonic() - start_time,
            )

    # 3. Evaluate eligibility
    eval_res = evaluate_decision_request(request, policy_snapshot)

    # 4. Evaluate rollout authorization & boundaries
    effective_rt = Path(runtime_root).resolve() if runtime_root else None
    auth_ok, auth_reason, eff_mode = evaluate_rollout_authorization(
        request=request,
        policy_snapshot=policy_snapshot,
        runtime_root=effective_rt,
        config=rollout_config,
    )

    if eff_mode == RolloutMode.OBSERVE_ONLY:
        dispatch_res = _handle_human_review_dispatch(
            request=request,
            eval_res=eval_res,
            transport=transport,
            target_mode=target_mode,
            host_qualification_evidence=host_qualification_evidence,
            start_time=start_time,
        )
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status=f"SHADOW_OBSERVE_ONLY:{dispatch_res.status}",
            selected_option=dispatch_res.selected_option,
            terminal_result=dispatch_res.terminal_result,
            evaluation=eval_res,
            record=None,
            verification_result=None,
            human_response=dispatch_res.human_response,
            display_receipt=dispatch_res.display_receipt,
            transport_call_count=dispatch_res.transport_call_count,
            error_message=f"OBSERVE_ONLY: Evaluated {eval_res.terminal_result.value} (margin={eval_res.margin:.2f}), human dispatch: {dispatch_res.status}",
            elapsed_seconds=time.monotonic() - start_time,
        )

    if not auth_ok:
        return _handle_human_review_dispatch(
            request=request,
            eval_res=eval_res,
            transport=transport,
            target_mode=target_mode,
            host_qualification_evidence=host_qualification_evidence,
            start_time=start_time,
        )

    if (
        require_human_only
        or not eval_res.is_eligible
        or eval_res.terminal_result != ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY
    ):
        return _handle_human_review_dispatch(
            request=request,
            eval_res=eval_res,
            transport=transport,
            target_mode=target_mode,
            host_qualification_evidence=host_qualification_evidence,
            start_time=start_time,
        )

    # In-flight recheck (Kill-switch / disablement recheck right before store/commit)
    recheck_ok, recheck_reason, _ = evaluate_rollout_authorization(
        request=request,
        policy_snapshot=policy_snapshot,
        runtime_root=effective_rt,
        config=rollout_config,
    )
    if not recheck_ok:
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="POLICY_CONFLICT",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.POLICY_CONFLICT,
            evaluation=eval_res,
            error_message=f"In-flight disablement: {recheck_reason}",
            transport_call_count=0,
            elapsed_seconds=time.monotonic() - start_time,
        )

    # 5. Autonomous path initialization
    effective_db = Path(db_path) if db_path else Path(runtime_root or ".") / "autonomous_resolutions.db"
    effective_ledger = Path(ledger_path) if ledger_path else Path(runtime_root or ".") / "hitl_decision_ledger.json"

    signing_key = private_key if private_key is not None else generate_autonomous_keypair()[0]

    try:
        store = AutonomousResolutionStore(effective_db)
    except Exception as exc:
        return AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="STORAGE_ERROR",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.STORAGE_ERROR,
            evaluation=eval_res,
            error_message=f"Storage initialization failed: {exc}",
            transport_call_count=0,
            elapsed_seconds=time.monotonic() - start_time,
        )

    # 6. Commit and project
    rec, err_result, retry_count = _commit_and_project_resolution(
        request=request,
        eval_res=eval_res,
        store=store,
        effective_ledger=effective_ledger,
        signing_key=signing_key,
        signer_id=signer_id,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        start_time=start_time,
    )
    if err_result is not None or rec is None:
        return err_result or AutonomousDecisionResult(
            decision_id=request.decision_id,
            status="STORAGE_ERROR",
            selected_option=None,
            terminal_result=ResolutionTerminalResult.STORAGE_ERROR,
            error_message="Unknown storage error",
            elapsed_seconds=time.monotonic() - start_time,
        )

    # 7. Readback clearance verification
    return _verify_clearance_readback(
        request=request,
        eval_res=eval_res,
        effective_db=effective_db,
        effective_ledger=effective_ledger,
        rec=rec,
        retry_count=retry_count,
        start_time=start_time,
    )
