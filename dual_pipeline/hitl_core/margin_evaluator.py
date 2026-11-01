"""
dual_pipeline/hitl_core/margin_evaluator.py

Calibrated 30% Confidence Margin Evaluator, Conviction Floor Guard, and Epistemic Entropy Engine.
Single Source of Truth (SSOT) for ambiguity gating across all pipeline stages.
Enforces the Triple-Lock Decision Rule:
    AUTO_ACCEPT iff V and not L and margin > threshold and c_top >= floor and (K < 3 or H_n <= max_entropy)
When any lock fails (spread <= threshold [0.30], top confidence < 0.65, or multi-option entropy > 0.75),
mandatory HITL review is triggered.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_entropy_ambiguity_threshold,
    get_hitl_min_autonomous_confidence_floor,
    needs_operator_decision,
)

logger = logging.getLogger("dual_pipeline.hitl_core.margin_evaluator")

AMBIGUITY_MARGIN_THRESHOLD = get_hitl_ambiguity_margin_threshold()
MIN_AUTONOMOUS_CONFIDENCE_FLOOR = get_hitl_min_autonomous_confidence_floor()
ENTROPY_AMBIGUITY_THRESHOLD = get_hitl_entropy_ambiguity_threshold()


def _extract_option_confidence(o: Dict[str, Any]) -> float:
    for k in ("confidence", "confidence_score", "score"):
        if k in o and o[k] is not None:
            try:
                return float(o[k])
            except (ValueError, TypeError):
                pass
    return 0.0


def _format_interval_segment(opt: Dict[str, Any]) -> str:
    lo = opt.get("confidence_lower")
    hi = opt.get("confidence_upper")
    ci = opt.get("confidence_interval") or opt.get("credible_interval")
    n = opt.get("confidence_sample_count") or opt.get("sample_size")
    if ci and isinstance(ci, (list, tuple)) and len(ci) >= 2:
        lo, hi = ci[0], ci[1]
    if lo is None or hi is None:
        return ""
    from dual_pipeline.hitl_governance_core.layman_clarity import should_render_credible_interval
    if not should_render_credible_interval(n, float(lo), float(hi)):
        return ""
    lo_pct = int(round(float(lo) * 100))
    hi_pct = int(round(float(hi) * 100))
    n_str = f", n={n}" if n is not None else ""
    return f" [{lo_pct}%–{hi_pct}%{n_str}]"


def _format_single_option(i: int, opt: Dict[str, Any]) -> str:
    conf_val = _extract_option_confidence(opt)
    if conf_val == 0.0 and "confidence" not in opt and "confidence_score" not in opt and "score" not in opt:
        from dual_pipeline.hitl_governance_core.surfacing import (
            HitlConfidenceMissingError,
            parse_confidence_score,
        )

        conf_val = parse_confidence_score(opt, require_confidence=True)

    conf_pct = int(round(conf_val * 100))
    rec_prefix = "(Recommended) " if i == 0 else ""
    tier = "HIGH" if conf_pct >= 85 else "MEDIUM" if conf_pct >= 50 else "LOW"
    desc = opt.get("description", opt.get("label", opt.get("text", "Option")))

    ci_str = _format_interval_segment(opt)
    summary = opt.get("plain_english_summary")
    summary_part = f" - {summary}" if summary and summary != desc else ""
    impact = opt.get("impact_statement")
    impact_part = f". Impact: {impact}" if impact else ""

    return f"{rec_prefix}(Confidence: {conf_pct}% - {tier}){ci_str} {desc}{summary_part}{impact_part}".strip()


def _evaluate_single_option(
    top: Dict[str, Any],
    c_top: float,
    confidence_floor: float,
    sorted_opts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if c_top < confidence_floor:
        return {
            "decision_resolved_autonomously": False,
            "margin": 1.0,
            "entropy": 0.0,
            "top_option": top,
            "second_option": None,
            "surface_hitl": True,
            "confidence_floor_breached": True,
            "rationale": (
                f"CONFIDENCE_FLOOR_BREACH: Single option confidence ({c_top:.1%}) "
                f"is below the {confidence_floor:.0%} conviction floor. Operator confirmation required."
            ),
            "ask_user_question_payload": MarginEvaluator.build_ask_user_question_payload(sorted_opts),
            "ask_question_payload": MarginEvaluator.build_ask_user_question_payload(sorted_opts),
        }
    return {
        "decision_resolved_autonomously": True,
        "margin": 1.0,
        "entropy": 0.0,
        "top_option": top,
        "second_option": None,
        "surface_hitl": False,
        "confidence_floor_breached": False,
        "rationale": f"Single option with sufficient confidence ({c_top:.1%}); resolved autonomously.",
    }


def _collect_surfacing_reasons(
    margin: float,
    threshold: float,
    entropy: float,
    max_entropy: float,
    c_top: float,
    confidence_floor: float,
    num_opts: int,
    margin_ambiguous: bool = False,
) -> Tuple[List[str], List[str]]:
    reasons: List[str] = []
    reason_codes: List[str] = []
    if margin_ambiguous:
        reasons.append(
            f"Confidence margin ({margin:.1%}) is within the {threshold:.0%} ambiguity threshold ({threshold:.1%}); mandatory operator review required."
        )
        reason_codes.append("MARGIN_BELOW_THRESHOLD")
    if num_opts >= 3 and entropy > max_entropy:
        reasons.append(
            f"Entropy override (H={entropy:.3f} > {max_entropy:.3f}): high distributional uncertainty forces mandatory HITL review."
        )
        reason_codes.append("ENTROPY_OVERRIDE")
    if c_top < confidence_floor:
        reasons.append(
            f"CONFIDENCE_FLOOR_BREACH: Top option confidence ({c_top:.1%}) is strictly below the {confidence_floor:.0%} autonomous conviction floor. Low absolute certainty requires human operator review."
        )
        reason_codes.append("CONFIDENCE_FLOOR_BREACH")
    return reasons, reason_codes


class MarginEvaluator:
    """Evaluates option confidence spread against governed SSOT thresholds."""

    @classmethod
    def evaluate(
        cls,
        options: List[Dict[str, Any]],
        threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
        confidence_floor: float = MIN_AUTONOMOUS_CONFIDENCE_FLOOR,
        max_entropy: float = ENTROPY_AMBIGUITY_THRESHOLD,
        policy_snapshot: Any = None,
    ) -> Dict[str, Any]:
        """
        Takes options with 'confidence' or 'confidence_score' (float 0.0 - 1.0) and evaluates whether
        the decision is ambiguous (spread <= threshold, top < floor, or entropy > max_entropy)
        or decisively resolved autonomously.
        Delegates canonically to evaluate_canonical_decision (Lock 1 SSOT).
        """
        from dual_pipeline.hitl_governance_core.canonical_evaluator import (
            evaluate_canonical_decision,
        )

        evaluation = evaluate_canonical_decision(
            options=options,
            margin_threshold=threshold,
            confidence_floor=confidence_floor,
            max_entropy=max_entropy,
            policy_snapshot=policy_snapshot,
            enable_lock2=False,
        )
        return evaluation.to_margin_evaluator_dict()

    @classmethod
    def build_ask_user_question_payload(cls, sorted_options: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Builds structured interactive question payload for ask_user_question tool."""
        from dual_pipeline.hitl_governance_core.canonical_evaluator import (
            build_canonical_ask_user_question_payload,
        )

        return build_canonical_ask_user_question_payload(sorted_options)

    # Deprecated backward compatibility alias
    build_ask_question_payload = build_ask_user_question_payload
