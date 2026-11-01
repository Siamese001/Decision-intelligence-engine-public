"""
dual_pipeline/hitl_governance_core/autonomous_resolution/eligibility.py

Pure, deterministic eligibility service for autonomous decision resolution.
Implements the fail-closed Dual-Lock Governance Matrix:
    AutonomousResolutionPermitted = Lock_1 (Mathematical) AND Lock_2 (Criticality)
Zero I/O, zero ledger mutations, zero external dispatches.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.hitl_governance_core.classifier import (
    MANDATORY_HITL_CATEGORIES,
    classify_decision_criticality,
)

from .models import (
    AuthorityType,
    DecisionMode,
    DecisionOption,
    DecisionRequest,
    EligibilityEvaluationResult,
    PolicySnapshot,
    ResolutionTerminalResult,
)
from .policy import validate_policy_snapshot


def _validate_option_payload(opt: DecisionOption, idx: int) -> Tuple[bool, Optional[str]]:
    """Validates types, bounds, and substance of a discrete option."""
    if not opt.option_id or not opt.option_id.strip():
        return False, f"Option at index {idx} has an empty option_id."
    if not opt.description or not opt.description.strip():
        return False, f"Option '{opt.option_id}' has an empty description."

    conf = opt.confidence
    if isinstance(conf, bool):
        return False, f"Option '{opt.option_id}' confidence is a boolean, which is strictly prohibited."
    if not isinstance(conf, (int, float)):
        return False, f"Option '{opt.option_id}' confidence must be numeric, got {type(conf).__name__}."
    if math.isnan(conf) or math.isinf(conf):
        return False, f"Option '{opt.option_id}' confidence is NaN or Infinite."
    if conf < 0.0 or conf > 1.0:
        return False, f"Option '{opt.option_id}' confidence ({conf}) is outside valid interval [0.0, 1.0]."

    # Reject UI control labels masquerading as substantive options
    desc_clean = opt.description.strip().lower()
    if desc_clean in ("other", "write-in", "skip", "none of the above"):
        return False, f"Option '{opt.option_id}' is a UI meta-control, not a substantive option."

    return True, None


def _validate_request_inputs(request: DecisionRequest) -> Tuple[bool, List[str]]:
    """Checks structural validity of the decision request."""
    defects: List[str] = []
    if not request.decision_id or not request.decision_id.strip():
        defects.append("Missing or empty decision_id.")
    if request.revision < 1:
        defects.append(f"Invalid revision number: {request.revision}. Must be >= 1.")

    scope = request.scope
    if not scope.worktree or not scope.worktree.strip():
        defects.append("Missing execution worktree in scope.")
    if not scope.run_id or not scope.run_id.strip():
        defects.append("Missing run_id in scope.")
    if not scope.action_target or not scope.action_target.strip():
        defects.append("Missing action_target in scope.")
    if scope.wave < 0:
        defects.append(f"Invalid wave number in scope: {scope.wave}.")

    if not request.options or len(request.options) < 2:
        defects.append(f"At least 2 distinct substantive options required; got {len(request.options or [])}.")
        return False, defects

    seen_ids = set()
    for i, opt in enumerate(request.options):
        is_valid, err = _validate_option_payload(opt, i)
        if not is_valid:
            defects.append(str(err))
        if opt.option_id in seen_ids:
            defects.append(f"Duplicate option ID detected: '{opt.option_id}'.")
        seen_ids.add(opt.option_id)

    return len(defects) == 0, defects


def _compute_normalized_entropy(confs: List[float]) -> float:
    """Computes normalized Shannon entropy H_n in [0.0, 1.0]."""
    k = len(confs)
    if k < 2:
        return 0.0
    total = sum(confs)
    if total <= 1e-12:
        return 1.0
    probs = [c / total for c in confs]
    raw_entropy = -sum(p * math.log(p) for p in probs if p > 1e-12)
    max_entropy = math.log(k)
    return round(raw_entropy / max_entropy, 4) if max_entropy > 0 else 0.0


def _evaluate_criticality_lock(request: DecisionRequest) -> Tuple[bool, List[str], List[str]]:
    """Evaluates Lock 2 (Deterministic Criticality Classification)."""
    reasons: List[str] = []
    reason_codes: List[str] = []

    # Check explicit mode
    if request.mode in {
        DecisionMode.ARCHITECTURAL,
        DecisionMode.DESTRUCTIVE,
        DecisionMode.POLICY,
        DecisionMode.GOVERNANCE,
    }:
        reasons.append(f"Decision mode '{request.mode.value}' is a mandatory human-review domain.")
        reason_codes.append("MANDATORY_CRITICAL_MODE")
        return False, reasons, reason_codes

    # Check explicit request category against mandatory human-review domains
    req_cat = str(request.category).strip().lower()
    if req_cat in (
        "architectural",
        "destructive",
        "policy",
        "governance",
        "attribution",
        "attribution_remediation",
        "system_learning",
        "learning",
        "release",
        "graph_mutation",
        "tier_0",
        "tier_1",
    ):
        reasons.append(f"Decision category '{request.category}' is a mandatory human-review domain.")
        reason_codes.append("MANDATORY_CRITICAL_CATEGORY")
        return False, reasons, reason_codes

    # Check classifier triggers
    classification = classify_decision_criticality(
        context=request.context,
        target_files=request.target_files,
        action_description=request.action_description,
        options=[opt.to_dict() for opt in request.options],
    )
    if classification.requires_mandatory_hitl or classification.category in MANDATORY_HITL_CATEGORIES:
        reasons.append(
            f"LOCK_2_BREACH ({classification.category.value.upper()}): {classification.rationale}"
        )
        reason_codes.append("MANDATORY_CRITICAL_CATEGORY")
        return False, reasons, reason_codes

    return True, reasons, reason_codes


def _evaluate_mathematical_lock(
    sorted_opts: List[DecisionOption],
    policy: PolicySnapshot,
) -> Tuple[bool, float, float, List[str], List[str]]:
    """Evaluates Lock 1 (Confidence Spread, Conviction Floor, and Entropy)."""
    c_top = float(sorted_opts[0].confidence)
    c_second = float(sorted_opts[1].confidence)
    margin = round(c_top - c_second, 4)
    confs = [float(o.confidence) for o in sorted_opts]
    entropy = _compute_normalized_entropy(confs)

    reasons: List[str] = []
    reason_codes: List[str] = []
    k = len(sorted_opts)

    # 1. Ambiguity Margin Lock: margin must STRICTLY exceed threshold
    if margin <= policy.ambiguity_threshold:
        reasons.append(
            f"Confidence margin ({margin:.1%}) is within SSOT ambiguity threshold ({policy.ambiguity_threshold:.1%}); operator decision required."
        )
        reason_codes.append("MARGIN_BELOW_OR_EQUAL_THRESHOLD")

    # 2. Conviction Floor Lock: c_top must meet or exceed floor
    if c_top < policy.conviction_floor:
        reasons.append(
            f"CONFIDENCE_FLOOR_BREACH: Top confidence ({c_top:.1%}) is below conviction floor ({policy.conviction_floor:.1%})."
        )
        reason_codes.append("CONFIDENCE_FLOOR_BREACH")

    # 3. Normalized Entropy Lock: multi-option uncertainty veto for K >= 3
    if k >= 3 and entropy > policy.max_entropy:
        reasons.append(
            f"ENTROPY_OVERRIDE: Multi-option normalized entropy ({entropy:.3f}) exceeds threshold ({policy.max_entropy:.3f})."
        )
        reason_codes.append("ENTROPY_OVERRIDE")

    passed = len(reasons) == 0
    return passed, margin, entropy, reasons, reason_codes


def evaluate_decision_eligibility(
    request: DecisionRequest,
    policy: PolicySnapshot,
) -> EligibilityEvaluationResult:
    """
    Pure deterministic eligibility evaluator enforcing the Dual-Lock Decision Invariant.
    Returns immutable EligibilityEvaluationResult with explicit terminal_result.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # Step 1: Input Validation
    req_ok, req_defects = _validate_request_inputs(request)
    if not req_ok:
        return EligibilityEvaluationResult(
            terminal_result=ResolutionTerminalResult.INVALID_INPUT,
            is_eligible=False,
            selected_option_id=None,
            selected_option=None,
            margin=0.0,
            entropy=0.0,
            top_confidence=0.0,
            second_confidence=0.0,
            reason_codes=["INVALID_REQUEST_PAYLOAD"],
            reasons=req_defects,
            policy_snapshot=policy,
            evaluated_at_iso=now_iso,
        )

    # Step 2: Policy Snapshot Validation
    policy_ok, policy_defects = validate_policy_snapshot(policy)
    if not policy_ok:
        return EligibilityEvaluationResult(
            terminal_result=ResolutionTerminalResult.POLICY_CONFLICT,
            is_eligible=False,
            selected_option_id=None,
            selected_option=None,
            margin=0.0,
            entropy=0.0,
            top_confidence=0.0,
            second_confidence=0.0,
            reason_codes=["POLICY_VALIDATION_FAILURE"],
            reasons=policy_defects,
            policy_snapshot=policy,
            evaluated_at_iso=now_iso,
        )

    # Step 3: Authority and Plan-Only Boundary Checks
    if request.authority_type != AuthorityType.AUTONOMOUS:
        return EligibilityEvaluationResult(
            terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
            is_eligible=False,
            selected_option_id=None,
            selected_option=None,
            margin=0.0,
            entropy=0.0,
            top_confidence=0.0,
            second_confidence=0.0,
            reason_codes=["UNAUTHORIZED_AUTHORITY_TYPE"],
            reasons=[f"Authority type '{request.authority_type.value}' is not authorized for autonomous resolution."],
            policy_snapshot=policy,
            evaluated_at_iso=now_iso,
        )

    if request.context.get("plan_only") or request.context.get("is_plan_only"):
        return EligibilityEvaluationResult(
            terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
            is_eligible=False,
            selected_option_id=None,
            selected_option=None,
            margin=0.0,
            entropy=0.0,
            top_confidence=0.0,
            second_confidence=0.0,
            reason_codes=["PLAN_ONLY_BOUNDARY"],
            reasons=["Plan-only boundary active: mutations and continuation require operator confirmation."],
            policy_snapshot=policy,
            evaluated_at_iso=now_iso,
        )

    # Check agent divergence arbitration
    divergence = request.context.get("agent_divergence")
    if divergence is not None:
        try:
            div_val = float(divergence)
            if div_val > policy.divergence_threshold:
                return EligibilityEvaluationResult(
                    terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
                    is_eligible=False,
                    selected_option_id=None,
                    selected_option=None,
                    margin=0.0,
                    entropy=0.0,
                    top_confidence=0.0,
                    second_confidence=0.0,
                    reason_codes=["AGENT_DIVERGENCE_BREACH"],
                    reasons=[f"Agent divergence ({div_val:.1%}) exceeds arbitration threshold ({policy.divergence_threshold:.1%})."],
                    policy_snapshot=policy,
                    evaluated_at_iso=now_iso,
                )
        except (ValueError, TypeError):
            pass

    # Step 4: Lock 2 (Deterministic Criticality Classification)
    lock2_ok, lock2_reasons, lock2_codes = _evaluate_criticality_lock(request)
    if not lock2_ok:
        return EligibilityEvaluationResult(
            terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
            is_eligible=False,
            selected_option_id=None,
            selected_option=None,
            margin=0.0,
            entropy=0.0,
            top_confidence=0.0,
            second_confidence=0.0,
            reason_codes=lock2_codes,
            reasons=lock2_reasons,
            policy_snapshot=policy,
            evaluated_at_iso=now_iso,
        )

    # Step 5: Lock 1 (Mathematical Confidence Margin Lock)
    sorted_opts = sorted(
        request.options,
        key=lambda o: (float(o.confidence), str(o.option_id)),
        reverse=True,
    )
    c_top = float(sorted_opts[0].confidence)
    c_second = float(sorted_opts[1].confidence)
    lock1_ok, margin, entropy, lock1_reasons, lock1_codes = _evaluate_mathematical_lock(
        sorted_opts, policy
    )

    if not lock1_ok:
        return EligibilityEvaluationResult(
            terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
            is_eligible=False,
            selected_option_id=None,
            selected_option=None,
            margin=margin,
            entropy=entropy,
            top_confidence=c_top,
            second_confidence=c_second,
            reason_codes=lock1_codes,
            reasons=lock1_reasons,
            policy_snapshot=policy,
            evaluated_at_iso=now_iso,
        )

    # Step 6: Clearance Granted
    return EligibilityEvaluationResult(
        terminal_result=ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY,
        is_eligible=True,
        selected_option_id=sorted_opts[0].option_id,
        selected_option=sorted_opts[0],
        margin=margin,
        entropy=entropy,
        top_confidence=c_top,
        second_confidence=c_second,
        reason_codes=["DUAL_LOCK_CLEARANCE"],
        reasons=[
            f"Dual-lock clearance: margin ({margin:.1%}) > {policy.ambiguity_threshold:.1%}, "
            f"conviction ({c_top:.1%}) >= {policy.conviction_floor:.1%}, Lock 2 satisfied."
        ],
        policy_snapshot=policy,
        evaluated_at_iso=now_iso,
    )


evaluate_decision_request = evaluate_decision_eligibility

