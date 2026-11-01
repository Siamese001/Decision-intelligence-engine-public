"""
dual_pipeline/hitl_governance_core/surfacing.py

Surfacing gates, entropy calculation, option confidence standardization, and presentation validation.
"""
from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, Mapping, Sequence

from config.hitl_tool_name import ASK_USER_QUESTION, HOST_RUNTIME_TOOL_NAME
from dual_pipeline.hitl_criticality import (
    CriticalityTier, calculate_epistemic_entropy, evaluate_criticality_score,
)
from dual_pipeline.hitl_governance_core.layman_clarity import format_layman_display_label
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold, get_hitl_divergence_margin_threshold,
    get_hitl_entropy_ambiguity_threshold, get_hitl_min_autonomous_confidence_floor,
)

REQUIRED_HITL_PHRASES = (
    "## HITL decisions are atomic, optionated, and confidence-scored",
    "Decisions must be presented strictly one at a time",
    "Never bundle, batch, or aggregate multiple HITL decisions",
    "Every option must be accompanied by an explicit, calibrated confidence level",
    "Operator answers to HITL decisions must be persistently stored",
    "Mandatory operator review is required whenever `needs_operator_decision(c_top, c_second)`",
)

class HitlConfidenceMissingError(ValueError):
    """Raised when an option lacks a mandatory calibrated confidence score or percentage."""


class HitlSurfacingMode(str, Enum):
    """Governs whether interactive UI clicked options are triggered on divergence or ambiguity."""
    AMBIGUITY_LE_THRESHOLD = "ambiguity_le_threshold"    # Surface UI when margin <= threshold (e.g. <= 0.20)
    DIVERGENCE_GT_THRESHOLD = "divergence_gt_threshold"  # Surface UI when margin > threshold (e.g. > 0.20)
    CHAT_HINT_CONFIRMATION = "chat_hint_confirmation"    # Always surface operator chat hint for confirmation

DEFAULT_MARGIN_THRESHOLD = get_hitl_ambiguity_margin_threshold()
LEGACY_MARGIN_THRESHOLD = DEFAULT_MARGIN_THRESHOLD
DEFAULT_DIVERGENCE_THRESHOLD = get_hitl_divergence_margin_threshold()
DEFAULT_CONFIDENCE_FLOOR = get_hitl_min_autonomous_confidence_floor()
DEFAULT_ENTROPY_THRESHOLD = get_hitl_entropy_ambiguity_threshold()
DEFAULT_SURFACING_MODE = HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD

CONFIDENCE_TIER_SCORES = {"HIGH": 0.90, "MEDIUM": 0.60, "LOW": 0.25}

SYNTHETIC_APPROVAL_MARKERS = (
    "stop hook blocked termination", "automatically approved the artifact", "automatically approved",
    "auto_approved", "auto-approved", "auto_approve", "auto-approve", "auto_approval", "auto-approval",
    "through their review policy", "review policy", "system_message", "synthetic_approval",
    "artifact_review_mode_turbo", "turbo mode", "harness policy", "cascade_commands_auto_execution",
    "autoexecutionpolicy", "synthetic", "mock_operator", "mock_mode", "fake_operator", "simulated_operator", "dev_test_bypass",
)


def calculate_normalized_entropy(scores: Sequence[float]) -> float:
    """Calculates normalized Shannon epistemic entropy H(p) / log2(N) in [0.0, 1.0]."""
    if not scores or len(scores) <= 1:
        return 0.0
    positive_scores = [max(0.0001, float(s)) for s in scores]
    total = sum(positive_scores)
    if total <= 0:
        return 0.0
    probs = [s / total for s in positive_scores]
    raw_entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(len(scores))
    if max_entropy <= 0:
        return 0.0
    return round(min(1.0, max(0.0, raw_entropy / max_entropy)), 4)


def _check_prior_command(cmd: str | None) -> bool:
    if not cmd:
        return False
    norm_cmd = str(cmd).lower()
    if any(marker in norm_cmd for marker in SYNTHETIC_APPROVAL_MARKERS):
        return False
    if any(term in norm_cmd for term in ("ingest-meeting", "ingest_meeting", "judge_clarification", "affirm_valid")):
        return False
    has_plan_only = any(term in norm_cmd for term in ("plan only", "no implement", "plan only stop", "do not implement"))
    has_exec_intent = any(term in norm_cmd for term in ("implement", "execute", "proceed"))
    return has_exec_intent and not has_plan_only


def _extract_approval_text_and_cmd(
    approval_source: str | Mapping[str, Any],
    prior_user_command: str | None,
) -> tuple[str, str | None]:
    if isinstance(approval_source, Mapping):
        parts: list[str] = []
        for k, v in approval_source.items():
            if k in ("prior_user_command", "user_command"):
                continue
            parts.extend((str(k).lower(), str(v).lower()))
        cmd = prior_user_command or approval_source.get("prior_user_command") or approval_source.get("user_command")
        return " ".join(parts), cmd
    return str(approval_source).lower(), prior_user_command


def validate_approval_origin(
    approval_source: str | Mapping[str, Any],
    prior_user_command: str | None = None,
) -> tuple[bool, str]:
    """Verify that an approval originates from an authentic human operator message, not a synthetic system hook."""
    if approval_source is None:
        return (
            False,
            "APPROVAL_REJECTED_EMPTY_PROVENANCE: Approval source is None; explicit human operator origin is required.",
        )

    raw_text, cmd = _extract_approval_text_and_cmd(approval_source, prior_user_command)
    cmd_low = str(cmd or "").lower()

    for marker in SYNTHETIC_APPROVAL_MARKERS:
        if marker in cmd_low:
            return (
                False,
                f"APPROVAL_REJECTED_SYNTHETIC_ORIGIN: Prior command contains synthetic marker '{marker}'.",
            )

    is_stop_hook = "stop hook blocked termination" in raw_text
    if is_stop_hook and _check_prior_command(cmd):
        return (
            True,
            "Approval verified: stop hook is bound to authentic prior human operator command.",
        )

    for marker in SYNTHETIC_APPROVAL_MARKERS:
        if marker in raw_text:
            return (
                False,
                f"APPROVAL_REJECTED_SYNTHETIC_ORIGIN: Approval contains synthetic system marker '{marker}'. "
                "Approvals must strictly originate from an explicit, manual human message (USER_INPUT).",
            )

    if isinstance(approval_source, Mapping):
        if not approval_source or not raw_text.strip():
            return (
                False,
                "APPROVAL_REJECTED_EMPTY_PROVENANCE: Empty approval mapping cannot authorize release.",
            )
        source = approval_source.get("source")
        if source and str(source).lower() not in (
            "user_explicit", "user_input", "human_operator", "operator", "interactive_operator", HOST_RUNTIME_TOOL_NAME, ASK_USER_QUESTION
        ):
            return (
                False,
                f"APPROVAL_REJECTED_INVALID_SOURCE: Source '{source}' is not an authorized human operator source.",
            )
    else:
        clean_src = str(approval_source).strip().lower()
        if not clean_src or clean_src in ("none", "null", "unknown", "system", "auto", "bot", "model"):
            return (
                False,
                f"APPROVAL_REJECTED_INVALID_SOURCE: Source '{approval_source}' is not an authorized human operator source.",
            )

    return (True, "Approval verified from authentic human operator origin.")


def _score_from_numeric(val: Any) -> float | None:
    if isinstance(val, (int, float)):
        fval = float(val)
        return min(1.0, max(0.0, fval / 100.0 if fval > 1.0 else fval))
    return None


def _score_from_text(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    pct = _extract_percentage(text)
    if pct is not None:
        return pct
    return _extract_tier(text)


def _score_from_dict(opt: dict[str, Any]) -> float | None:
    for key in ("confidence_score", "score", "confidence", "confidence_level"):
        if key in opt:
            s = _score_from_numeric(opt[key]) or _score_from_text(opt[key])
            if s is not None:
                return s
    for text_key in ("label", "text", "option", "action"):
        if text_key in opt:
            s = _score_from_text(opt[text_key])
            if s is not None:
                return s
    return None


def parse_confidence_score(opt: Any, require_confidence: bool = True) -> float:
    """Extract a calibrated float confidence score between 0.0 and 1.0.

    Args:
        opt: The option dict or string.
        require_confidence: If True (default), raises HitlConfidenceMissingError if confidence cannot be parsed.

    Raises:
        HitlConfidenceMissingError: If require_confidence is True and no confidence is found.
    """
    if isinstance(opt, dict):
        score = _score_from_dict(opt)
        if score is not None:
            return score
    elif isinstance(opt, str):
        score = _score_from_text(opt)
        if score is not None:
            return score
    if not require_confidence:
        return 0.0
    raise HitlConfidenceMissingError(
        f"Mandatory confidence level missing from option: {opt!r}. "
        "Every HITL option must specify a confidence percentage (e.g. '75%'), score (0.0-1.0), or tier (HIGH/MEDIUM/LOW)."
    )


def validate_hitl_option(opt: Any, require_evidence: bool = False) -> dict[str, Any]:
    """Validate that an individual HITL option conforms to the mandatory confidence contract.

    Raises:
        HitlConfidenceMissingError: If confidence score or percentage is missing.
        ValueError: If option is invalid or missing required fields.
    """
    if isinstance(opt, str):
        score = parse_confidence_score(opt, require_confidence=True)
        tier = "HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW")
        ci = [round(max(0.0, score - 0.05), 4), round(min(1.0, score + 0.05), 4)]
        res = {
            "label": opt,
            "display_text": opt,
            "plain_english_summary": opt,
            "impact_statement": f"Operational execution of option: {opt}",
            "confidence_score": score,
            "confidence_pct": round(score * 100.0, 2),
            "confidence_level": tier,
            "tier": tier,
            "credible_interval": ci,
            "evidence_refs": [],
            "recommended": "(recommended)" in opt.lower(),
            "prior_adjustment": 0.0,
        }
        return res
    if isinstance(opt, dict):
        score = parse_confidence_score(opt, require_confidence=True)
        tier = opt.get("confidence_level") or opt.get("tier") or ("HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW"))
        label = opt.get("label") or opt.get("text") or opt.get("option") or opt.get("key")
        if not label:
            raise ValueError(f"HITL option missing label/text: {opt!r}")
        evidence = opt.get("evidence_refs", opt.get("evidence", []))
        if require_evidence and not evidence:
            raise ValueError(f"HITL option missing required evidence_refs: {opt!r}")
        summary = opt.get("plain_english_summary") or str(label)
        impact = opt.get("impact_statement") or f"Operational execution of option: {label}"
        raw_ci = opt.get("credible_interval") or opt.get("confidence_interval")
        ci = [round(float(raw_ci[0]), 4), round(float(raw_ci[1]), 4)] if raw_ci and isinstance(raw_ci, (list, tuple)) and len(raw_ci) >= 2 else [round(max(0.0, score - 0.05), 4), round(min(1.0, score + 0.05), 4)]
        res = {
            "label": str(label),
            "display_text": str(opt.get("display_text") or ""),
            "plain_english_summary": str(summary),
            "impact_statement": str(impact),
            "confidence_score": score,
            "confidence_pct": round(score * 100.0, 2),
            "confidence_level": str(tier),
            "tier": str(tier),
            "credible_interval": ci,
            "evidence_refs": list(evidence) if isinstance(evidence, (list, tuple)) else [str(evidence)],
            "recommended": bool(opt.get("recommended", False) or "(recommended)" in str(label).lower()),
            "prior_adjustment": float(opt.get("prior_adjustment", 0.0)),
        }
        if not res["display_text"]:
            res["display_text"] = format_layman_display_label(res)
        ss = opt.get("sample_size") or opt.get("confidence_sample_count")
        if ss is not None:
            res["sample_size"] = int(ss)
        return res
    raise ValueError(f"Invalid option type: {type(opt).__name__}; expected dict or str.")


def _extract_percentage(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        val = float(match.group(1))
        return min(1.0, max(0.0, val / 100.0))
    return None


def _extract_tier(text: str) -> float | None:
    upper = text.upper()
    for tier, score in CONFIDENCE_TIER_SCORES.items():
        if tier in upper:
            return score
    return None


def _option_key(opt: Any) -> str:
    if isinstance(opt, dict):
        return str(
            opt.get("key")
            or opt.get("option_id")
            or opt.get("action")
            or opt.get("label")
            or opt.get("text")
            or ""
        )
    return str(opt)


def _standardize_option(opt: Any, require_confidence: bool = True) -> dict[str, Any]:
    score = parse_confidence_score(opt, require_confidence=require_confidence)
    explicit_tier = None
    if isinstance(opt, str):
        upper = opt.upper()
        if "HIGH" in upper:
            explicit_tier = "HIGH"
        elif "MEDIUM" in upper:
            explicit_tier = "MEDIUM"
        elif "LOW" in upper:
            explicit_tier = "LOW"
    elif isinstance(opt, dict):
        explicit_tier = opt.get("confidence_level") or opt.get("tier")

    tier = explicit_tier or ("HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW"))
    if isinstance(opt, dict):
        res = dict(opt)
        res["confidence_score"] = score
        res.setdefault("confidence_level", tier)

        lo = res.get("confidence_lower")
        hi = res.get("confidence_upper")
        ci = res.get("confidence_interval")
        n = res.get("confidence_sample_count")

        if ci and isinstance(ci, (list, tuple)) and len(ci) >= 2:
            lo = float(ci[0])
            hi = float(ci[1])
        elif lo is not None and hi is not None:
            lo = float(lo)
            hi = float(hi)
        else:
            lo = max(0.05, round(score - 0.20, 4))
            hi = min(0.99, round(score + 0.20, 4))

        res.setdefault("confidence_lower", round(lo, 4))
        res.setdefault("confidence_upper", round(hi, 4))
        res.setdefault("confidence_interval", [round(lo, 4), round(hi, 4)])
        res.setdefault("confidence_interval_width", round(hi - lo, 4))
        res.setdefault("confidence_sample_count", int(n if n is not None else 0))
        res.setdefault("confidence_is_precise", (hi - lo) < 0.20)
        return res

    opt_str = str(opt)
    match_interval = re.search(r"\[(\d+(?:\.\d+)?)\s*%\s*[–\-]\s*(\d+(?:\.\d+)?)\s*%(?:,\s*n\s*=\s*(\d+))?\]", opt_str)
    if match_interval:
        lo = float(match_interval.group(1)) / 100.0
        hi = float(match_interval.group(2)) / 100.0
        n = int(match_interval.group(3)) if match_interval.group(3) else 0
    else:
        lo = max(0.05, round(score - 0.20, 4))
        hi = min(0.99, round(score + 0.20, 4))
        n = 0

    return {
        "label": opt_str,
        "confidence_score": score,
        "confidence_level": tier,
        "confidence_lower": round(lo, 4),
        "confidence_upper": round(hi, 4),
        "confidence_interval": [round(lo, 4), round(hi, 4)],
        "confidence_interval_width": round(hi - lo, 4),
        "confidence_sample_count": n,
        "confidence_is_precise": (hi - lo) < 0.20,
    }


def _resolve_tier(criticality_tier: Any, context: Mapping[str, Any] | None) -> CriticalityTier | None:
    if criticality_tier is not None:
        try:
            return CriticalityTier(criticality_tier)
        except (ValueError, KeyError):
            return None
    if context is not None:
        tier, _, _ = evaluate_criticality_score(context)
        return tier
    return None


def _evaluate_single_option(
    opt: Any,
    score: float,
    mode_str: str,
    eff_floor: float,
    margin_threshold: float,
    resolved_tier: CriticalityTier | None,
    entropy: float,
) -> dict[str, Any]:
    is_divergence = mode_str == HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD.value
    floor_breached = (score < eff_floor) and not is_divergence
    should_surface = False if is_divergence else (score < (1.0 - margin_threshold) or floor_breached)
    if resolved_tier in (CriticalityTier.TIER_0_IRREVERSIBLE, CriticalityTier.TIER_1_ARCHITECTURAL):
        should_surface, reason = True, f"CRITICALITY_OVERRIDE (Tier {resolved_tier.value} - {resolved_tier.name}): Mandatory HITL review required regardless of confidence margin."
    elif floor_breached:
        reason = f"CONFIDENCE_FLOOR_BREACH: Single option confidence ({score * 100:.1f}%) is below the {eff_floor * 100:.0f}% conviction floor. Operator confirmation required."
    else:
        reason = "Single option evaluated."
    return {
        "should_surface": should_surface,
        "margin": 1.0,
        "margin_threshold": margin_threshold,
        "surfacing_mode": mode_str,
        "reason": reason,
        "top_option": opt,
        "runner_up_option": None,
        "top_confidence": score,
        "runner_up_confidence": 0.0,
        "criticality_tier": resolved_tier.value if resolved_tier is not None else None,
        "epistemic_entropy": entropy,
        "normalized_entropy": 0.0,
        "confidence_floor_breached": floor_breached,
        "min_confidence_floor": eff_floor,
    }


def _check_surfacing_overrides(
    resolved_tier: CriticalityTier | None,
    entropy: float,
    entropy_threshold: float | None,
    num_options: int,
    normalized_entropy: float,
    eff_norm_entropy: float,
) -> tuple[bool, bool, str, bool] | None:
    if resolved_tier in (CriticalityTier.TIER_0_IRREVERSIBLE, CriticalityTier.TIER_1_ARCHITECTURAL):
        return True, True, f"CRITICALITY_OVERRIDE (Tier {resolved_tier.value} - {resolved_tier.name}): Mandatory HITL review required regardless of confidence margin.", False
    if entropy_threshold is not None and entropy > entropy_threshold:
        return True, False, f"EPISTEMIC_ENTROPY_OVERRIDE: Candidate entropy ({entropy:.4f}) exceeds threshold ({entropy_threshold:.4f}). High disagreement requires human operator arbitration.", False
    if num_options >= 3 and normalized_entropy > eff_norm_entropy:
        return True, False, f"MULTI_OPTION_ENTROPY_OVERRIDE: Normalized entropy across {num_options} options ({normalized_entropy:.4f}) strictly exceeds threshold ({eff_norm_entropy:.4f}). Systemic multi-choice ambiguity requires operator resolution.", False
    return None


def _evaluate_standard_surfacing(
    mode_str: str,
    margin: float,
    margin_threshold: float,
    top_conf: float,
    runner_up_conf: float,
    eff_floor: float,
    eff_norm_entropy: float,
) -> tuple[bool, bool, str]:
    if mode_str == HitlSurfacingMode.CHAT_HINT_CONFIRMATION.value:
        return True, False, "Operator chat attribution hint requires explicit confirmation per AGENTS.md invariant."

    if mode_str == HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD.value:
        should_surface = margin > margin_threshold
        floor_breached = top_conf < eff_floor
        thresh_pct = int(round(margin_threshold * 100))
        if should_surface:
            return True, floor_breached, f"Margin difference ({margin * 100:.1f}%) strictly exceeds the {thresh_pct}% threshold."
        return False, floor_breached, f"Margin difference ({margin * 100:.1f}%) is within or equal to the {thresh_pct}% threshold."

    from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
    eval_res = MarginEvaluator.evaluate(
        [{"confidence": top_conf}, {"confidence": runner_up_conf}],
        threshold=margin_threshold,
        confidence_floor=eff_floor,
        max_entropy=eff_norm_entropy,
    )
    return eval_res["surface_hitl"], eval_res.get("confidence_floor_breached", False), eval_res["rationale"]


def evaluate_hitl_surfacing_gate(
    options: list[Any],
    margin_threshold: float | None = None,
    surfacing_mode: HitlSurfacingMode | str = DEFAULT_SURFACING_MODE,
    criticality_tier: CriticalityTier | int | None = None,
    context: Mapping[str, Any] | None = None,
    entropy_threshold: float | None = None,
    min_confidence_floor: float | None = None,
    normalized_entropy_threshold: float | None = None,
    decision_store: Any | None = None,
    category: str | None = None,
    pre_calibrated: bool = False,
) -> dict[str, Any]:
    """Evaluate whether a decision meets the surfacing threshold to prompt the operator or auto-resolve.
    Delegates to canonical evaluator (SSOT).
    """
    mode_str = str(surfacing_mode.value if isinstance(surfacing_mode, HitlSurfacingMode) else surfacing_mode)
    eff_margin_threshold = margin_threshold if margin_threshold is not None else get_hitl_ambiguity_margin_threshold()
    eff_floor = min_confidence_floor if min_confidence_floor is not None else get_hitl_min_autonomous_confidence_floor()
    resolved_tier = _resolve_tier(criticality_tier, context)

    if not options or not isinstance(options, list):
        return {
            "should_surface": True,
            "margin": 0.0,
            "margin_threshold": eff_margin_threshold,
            "surfacing_mode": mode_str,
            "reason": "No options provided; must surface for operator guidance.",
            "top_option": None,
            "runner_up_option": None,
            "top_confidence": 0.0,
            "runner_up_confidence": 0.0,
            "criticality_tier": resolved_tier.value if resolved_tier is not None else None,
            "epistemic_entropy": 0.0,
            "normalized_entropy": 0.0,
            "confidence_floor_breached": False,
            "min_confidence_floor": eff_floor,
            "calibration_applied": False,
            "historical_sample_count": 0,
            "calibrated_options": [],
        }

    from .canonical_evaluator import evaluate_canonical_decision

    evaluation = evaluate_canonical_decision(
        options=options,
        context=context,
        margin_threshold=margin_threshold,
        confidence_floor=min_confidence_floor,
        max_entropy=normalized_entropy_threshold,
        surfacing_mode=mode_str,
        criticality_tier=resolved_tier,
        decision_store=decision_store,
        category=category,
        pre_calibrated=pre_calibrated,
        enable_lock2=True,
    )
    return evaluation.to_surfacing_gate_dict(surfacing_mode=mode_str)
