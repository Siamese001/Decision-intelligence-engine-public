"""Canonical Decision Evaluator & Unified Lifecycle Engine (SSOT).

Single Source of Truth for Human-in-the-Loop (HITL) ambiguity gating,
conviction floors, entropy thresholds, and criticality locks.
Unifies MarginEvaluator, evaluate_dual_lock, and evaluate_hitl_surfacing_gate
under a single deterministic evaluation pipeline.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from dual_pipeline.hitl_criticality import (
    CriticalityTier,
    calculate_epistemic_entropy,
    evaluate_criticality_score,
)
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_entropy_ambiguity_threshold,
    get_hitl_min_autonomous_confidence_floor,
    needs_operator_decision,
)

from .classifier import (
    DecisionCategory,
    DecisionClassificationResult,
    MANDATORY_HITL_CATEGORIES,
    classify_decision_criticality,
)
from .layman_clarity import format_layman_display_label, should_render_credible_interval
from .surfacing import (
    HitlConfidenceMissingError,
    calculate_normalized_entropy,
    parse_confidence_score,
)

CONFIDENCE_TIER_HIGH_FLOOR = 0.85
CONFIDENCE_TIER_MEDIUM_FLOOR = 0.50


def resolve_confidence_tier_name(score: float, explicit: Optional[str] = None) -> str:
    """Return canonical tier name: HIGH (>=85%), MEDIUM (50%-84%), LOW (<50%)."""
    if explicit and str(explicit).upper() in ("HIGH", "MEDIUM", "LOW"):
        return str(explicit).upper()
    if score >= CONFIDENCE_TIER_HIGH_FLOOR:
        return "HIGH"
    if score >= CONFIDENCE_TIER_MEDIUM_FLOOR:
        return "MEDIUM"
    return "LOW"


def decision_category_to_criticality_tier(cat: Any) -> CriticalityTier:
    """Map DecisionCategory (or string) to canonical CriticalityTier."""
    if isinstance(cat, DecisionCategory):
        cat_val = cat.value
    else:
        cat_val = str(cat).lower()

    if cat_val == "destructive":
        return CriticalityTier.TIER_0_IRREVERSIBLE
    if cat_val in ("architectural", "governance"):
        return CriticalityTier.TIER_1_ARCHITECTURAL
    if cat_val == "policy":
        return CriticalityTier.TIER_1_ARCHITECTURAL
    if cat_val == "tactical":
        return CriticalityTier.TIER_3_AMBIGUOUS
    if cat_val == "routine":
        return CriticalityTier.TIER_4_AUTONOMOUS
    return CriticalityTier.TIER_3_AMBIGUOUS


def criticality_tier_to_decision_category(tier: Any) -> DecisionCategory:
    """Map CriticalityTier (or int) to canonical DecisionCategory."""
    if isinstance(tier, CriticalityTier):
        tier_val = tier
    else:
        try:
            tier_val = CriticalityTier(int(tier))
        except (ValueError, TypeError):
            return DecisionCategory.ROUTINE

    if tier_val == CriticalityTier.TIER_0_IRREVERSIBLE:
        return DecisionCategory.DESTRUCTIVE
    if tier_val == CriticalityTier.TIER_1_ARCHITECTURAL:
        return DecisionCategory.ARCHITECTURAL
    if tier_val == CriticalityTier.TIER_2_DIVERGENT:
        return DecisionCategory.POLICY
    if tier_val == CriticalityTier.TIER_3_AMBIGUOUS:
        return DecisionCategory.TACTICAL
    return DecisionCategory.ROUTINE


def extract_canonical_option_confidence(
    opt: Any, require_confidence: bool = True
) -> float:
    """Extract numeric confidence score in [0.0, 1.0] without fabricating values."""
    if isinstance(opt, Mapping):
        for k in ("confidence_score", "score", "confidence"):
            val = opt.get(k)
            if val is not None:
                with contextlib.suppress(ValueError, TypeError):
                    f = float(val)
                    return min(1.0, max(0.0, f / 100.0 if f > 1.0 else f))
    return parse_confidence_score(opt, require_confidence=require_confidence)


def _format_option_credible_interval(opt: Mapping[str, Any]) -> str:
    ci = opt.get("credible_interval") or opt.get("confidence_interval")
    n = opt.get("sample_size") or opt.get("confidence_sample_count")
    if not (ci and isinstance(ci, (list, tuple)) and len(ci) >= 2):
        return ""
    lo, hi = float(ci[0]), float(ci[1])
    if not should_render_credible_interval(n, lo, hi):
        return ""
    lo_p, hi_p = int(round(lo * 100)), int(round(hi * 100))
    n_p = f", n={n}" if n is not None else ""
    return f" [{lo_p}%–{hi_p}%{n_p}]"


def _format_mapping_option_suffix(opt: Mapping[str, Any], desc: str) -> str:
    summary = opt.get("plain_english_summary")
    sum_p = f" - {summary}" if summary and summary != desc else ""
    imp = opt.get("impact_statement")
    imp_p = f". Impact: {imp}" if imp else ""
    return f"{sum_p}{imp_p}"


def format_canonical_option_label(
    idx: int, opt: Any, total_options: int = 2
) -> str:
    """Format single option adhering to Layman Clarity and HITL Option format."""
    if isinstance(opt, Mapping):
        opt_copy = dict(opt)
        opt_copy["recommended"] = bool(idx == 0 or opt_copy.get("recommended")) if idx == 0 else False
        score = extract_canonical_option_confidence(opt, require_confidence=False)
        opt_copy.setdefault("confidence_pct", round(score * 100, 2))
        if "confidence_level" not in opt_copy and "tier" not in opt_copy:
            opt_copy["confidence_level"] = resolve_confidence_tier_name(score)
        if "label" not in opt_copy:
            opt_copy["label"] = str(opt.get("text") or opt.get("description") or f"Option {idx+1}")
        return format_layman_display_label(opt_copy)

    score = extract_canonical_option_confidence(opt, require_confidence=False)
    return format_layman_display_label(
        {
            "label": str(opt),
            "recommended": (idx == 0),
            "confidence_pct": round(score * 100, 2),
            "confidence_level": resolve_confidence_tier_name(score),
        }
    )


def build_canonical_ask_user_question_payload(
    sorted_options: Sequence[Any],
    question_text: str = "Which option should be authorized?",
) -> Dict[str, Any]:
    """Construct dual-compatible ask_user_question payload."""
    formatted = [
        format_canonical_option_label(i, o, len(sorted_options))
        for i, o in enumerate(sorted_options)
    ]
    return {
        "questions": [
            {
                "question": question_text,
                "options": formatted,
                "is_multi_select": False,
            }
        ],
        "options": formatted,
        "is_multi_select": False,
        "question": question_text,
    }


@dataclass(frozen=True)
class CanonicalDecisionEvaluation:
    """Unified outcome of Lock 1 and Lock 2 decision evaluation."""

    autonomous_permitted: bool
    surface_hitl: bool
    lock1_passed: bool
    lock2_passed: bool
    category: DecisionCategory
    criticality_tier: Optional[CriticalityTier]
    classification: DecisionClassificationResult
    margin: float
    margin_threshold: float
    top_confidence: float
    runner_up_confidence: float
    confidence_floor: float
    confidence_floor_breached: bool
    normalized_entropy: float
    epistemic_entropy: float
    top_option: Optional[Any]
    second_option: Optional[Any]
    reasons: List[str]
    reason_codes: List[str]
    ask_user_question_payload: Optional[Dict[str, Any]]
    policy_snapshot: Optional[Any] = None
    calibrated_options: List[Dict[str, Any]] = field(default_factory=list)
    historical_sample_count: int = 0
    calibration_applied: bool = False

    @property
    def options(self) -> List[Dict[str, Any]]:
        return self.calibrated_options

    @property
    def ask_question_payload(self) -> Optional[Dict[str, Any]]:
        return self.ask_user_question_payload

    @property
    def autonomous_eligible(self) -> bool:
        return self.autonomous_permitted

    @property
    def rationale(self) -> str:
        return " | ".join(self.reasons) if self.reasons else ""

    def to_dual_lock_result(self) -> Any:
        """Adapter format expected by callers of evaluate_dual_lock()."""
        from .dual_lock import DualLockEvaluationResult

        reasons = list(self.reasons)
        if self.autonomous_permitted and not reasons:
            reasons.append(
                f"DUAL_LOCK_CLEARANCE: Lock 1 (spread {self.margin:.1%} > {self.margin_threshold:.1%}, score {self.top_confidence:.1%} >= {self.confidence_floor:.1%}) "
                f"and Lock 2 (category: {self.category.value}) both satisfied. Autonomous resolution permitted."
            )

        return DualLockEvaluationResult(
            autonomous_permitted=self.autonomous_permitted,
            surface_hitl=self.surface_hitl,
            lock1_passed=self.lock1_passed,
            lock2_passed=self.lock2_passed,
            category=self.category,
            classification=self.classification,
            margin=self.margin,
            margin_threshold=self.margin_threshold,
            top_confidence=self.top_confidence,
            confidence_floor=self.confidence_floor,
            normalized_entropy=self.normalized_entropy,
            top_option=self.top_option,
            second_option=self.second_option,
            reasons=reasons,
            ask_user_question_payload=self.ask_user_question_payload,
            policy_snapshot=self.policy_snapshot,
        )

    def to_margin_evaluator_dict(self) -> Dict[str, Any]:
        """Adapter format expected by callers of MarginEvaluator.evaluate()."""
        if self.autonomous_permitted:
            if self.second_option is None:
                rationale = f"Single option with sufficient confidence ({self.top_confidence:.1%}); resolved autonomously."
            else:
                rationale = (
                    f"Confidence margin ({self.margin:.1%}) exceeds the {self.margin_threshold:.0%} threshold "
                    f"({self.margin_threshold:.1%}) and conviction ({self.top_confidence:.1%}) meets floor "
                    f"({self.confidence_floor:.0%}); proceeding autonomously."
                )
        else:
            rationale = " | ".join(self.reasons) if self.reasons else "Operator review required."

        return {
            "decision_resolved_autonomously": self.autonomous_permitted,
            "surface_hitl": self.surface_hitl,
            "margin": self.margin,
            "entropy": self.normalized_entropy,
            "top_option": self.top_option,
            "second_option": self.second_option,
            "confidence_floor_breached": self.confidence_floor_breached,
            "reason_codes": list(self.reason_codes),
            "rationale": rationale,
            "ask_user_question_payload": self.ask_user_question_payload,
            "ask_question_payload": self.ask_user_question_payload,
        }

    def to_surfacing_gate_dict(self, surfacing_mode: str = "ambiguity_le_threshold") -> Dict[str, Any]:
        """Adapter format expected by callers of evaluate_hitl_surfacing_gate()."""
        if self.reasons:
            reason = " | ".join(self.reasons)
        elif self.second_option is None:
            reason = "Single option evaluated."
        else:
            reason = "Decision evaluated."

        crit_tier_val = self.criticality_tier.value if self.criticality_tier is not None else None

        return {
            "should_surface": self.surface_hitl,
            "margin": self.margin,
            "margin_threshold": self.margin_threshold,
            "surfacing_mode": surfacing_mode,
            "top_option": self.top_option,
            "runner_up_option": self.second_option,
            "top_confidence": self.top_confidence,
            "runner_up_confidence": self.runner_up_confidence,
            "reason": reason,
            "criticality_tier": crit_tier_val,
            "criticality_override": not self.lock2_passed,
            "epistemic_entropy": self.epistemic_entropy,
            "normalized_entropy": self.normalized_entropy,
            "confidence_floor_breached": self.confidence_floor_breached,
            "min_confidence_floor": self.confidence_floor,
            "calibration_applied": self.calibration_applied,
            "historical_sample_count": self.historical_sample_count,
            "calibrated_options": self.calibrated_options,
            "ask_user_question_payload": self.ask_user_question_payload,
            "ask_question_payload": self.ask_user_question_payload,
        }


def _resolve_effective_parameters(
    margin_threshold: Optional[float],
    confidence_floor: Optional[float],
    max_entropy: Optional[float],
    policy_snapshot: Optional[Any],
) -> Tuple[float, float, float]:
    eff_thresh = margin_threshold if margin_threshold is not None else get_hitl_ambiguity_margin_threshold()
    eff_floor = confidence_floor if confidence_floor is not None else get_hitl_min_autonomous_confidence_floor()
    eff_ent = max_entropy if max_entropy is not None else get_hitl_entropy_ambiguity_threshold()

    if policy_snapshot is not None:
        eff_thresh = float(getattr(policy_snapshot, "ambiguity_threshold", eff_thresh))
        eff_floor = float(getattr(policy_snapshot, "conviction_floor", eff_floor))
        eff_ent = float(getattr(policy_snapshot, "max_entropy", eff_ent))
    return eff_thresh, eff_floor, eff_ent


def _evaluate_lock1_and_reasons(
    num_opts: int,
    c_top: float,
    c_second: float,
    margin: float,
    entropy: float,
    eff_thresh: float,
    eff_floor: float,
    eff_ent: float,
    surfacing_mode: Optional[str] = None,
) -> Tuple[bool, bool, List[str], List[str]]:
    reasons: List[str] = []
    codes: List[str] = []

    if surfacing_mode == "chat_hint_confirmation":
        reasons.append("Operator chat attribution hint requires explicit confirmation per AGENTS.md invariant.")
        codes.append("CHAT_HINT_CONFIRMATION")
        return False, False, reasons, codes

    if num_opts == 1:
        floor_breached = c_top < eff_floor
        if floor_breached:
            reasons.append(
                f"LOCK_1_CONVICTION_FLOOR_BREACH: Single option confidence ({c_top:.1%}) "
                f"is below the {eff_floor:.0%} conviction floor. Operator confirmation required."
            )
            codes.append("CONFIDENCE_FLOOR_BREACH")
            return False, True, reasons, codes
        return True, False, reasons, codes

    if surfacing_mode == "divergence_gt_threshold":
        margin_divergent = margin > eff_thresh
        if margin_divergent:
            thresh_pct = int(round(eff_thresh * 100))
            reasons.append(f"Margin difference ({margin * 100:.1f}%) strictly exceeds the {thresh_pct}% threshold.")
            codes.append("DIVERGENCE_GT_THRESHOLD")
    else:
        margin_ambiguous = needs_operator_decision(c_top, c_second, threshold=eff_thresh)
        if margin_ambiguous:
            reasons.append(
                f"LOCK_1_SPREAD_BREACH: Confidence margin ({margin:.1%}) is within the {eff_thresh:.0%} ambiguity threshold ({eff_thresh:.1%}); mandatory operator review required."
            )
            codes.append("MARGIN_BELOW_THRESHOLD")

    floor_breached = c_top < eff_floor
    if floor_breached:
        reasons.append(
            f"LOCK_1_CONVICTION_FLOOR_BREACH: Top option confidence ({c_top:.1%}) is strictly below the {eff_floor:.0%} autonomous conviction floor. Low absolute certainty requires human operator review."
        )
        codes.append("CONFIDENCE_FLOOR_BREACH")

    if num_opts >= 3 and entropy > eff_ent and not (margin > eff_thresh and not floor_breached):
        reasons.append(
            f"LOCK_1_ENTROPY_BREACH: Entropy override (H={entropy:.3f} > {eff_ent:.3f}): high distributional uncertainty forces mandatory HITL review."
        )
        codes.append("ENTROPY_OVERRIDE")

    lock1_passed = not bool(reasons)
    return lock1_passed, floor_breached, reasons, codes


def _resolve_criticality_lock2(
    criticality_tier: Optional[Union[CriticalityTier, int]],
    context: Optional[Mapping[str, Any]],
    target_files: Optional[Sequence[str]],
    action_description: Optional[str],
    options: Sequence[Any],
    policy_snapshot: Optional[Any],
    enable_lock2: bool,
    category: Optional[Union[DecisionCategory, str]] = None,
) -> Tuple[Optional[CriticalityTier], DecisionClassificationResult, bool, Optional[str], Optional[str]]:
    crit_tier: Optional[CriticalityTier] = None
    if criticality_tier is not None:
        with contextlib.suppress(ValueError, TypeError):
            crit_tier = CriticalityTier(criticality_tier)
    elif context and "criticality_tier" in context:
        with contextlib.suppress(Exception):
            crit_tier = CriticalityTier(context["criticality_tier"])

    if not enable_lock2:
        classification = DecisionClassificationResult(
            category=DecisionCategory.ROUTINE,
            is_critical=False,
            requires_mandatory_hitl=False,
            rationale="Lock 2 disabled for pure margin evaluation",
        )
        return crit_tier, classification, True, None, None

    if category is not None:
        cat_enum = None
        with contextlib.suppress(Exception):
            cat_enum = DecisionCategory(category)
        if cat_enum and cat_enum in MANDATORY_HITL_CATEGORIES:
            classification = DecisionClassificationResult(
                category=cat_enum,
                is_critical=True,
                requires_mandatory_hitl=True,
                rationale=f"Mandatory HITL enforced for category {cat_enum.value}",
            )
            return (
                crit_tier,
                classification,
                False,
                f"LOCK_2_BREACH ({cat_enum.value.upper()}): Mandatory HITL required",
                f"LOCK_2_{cat_enum.value.upper()}",
            )

    classification = classify_decision_criticality(
        context=context,
        target_files=target_files,
        action_description=action_description,
        options=options,
        policy_snapshot=policy_snapshot,
    )
    lock2_passed = not classification.requires_mandatory_hitl
    if crit_tier in (CriticalityTier.TIER_0_IRREVERSIBLE, CriticalityTier.TIER_1_ARCHITECTURAL):
        reason = f"CRITICALITY_OVERRIDE (Tier {crit_tier.value} - {crit_tier.name}): Mandatory HITL review required regardless of confidence margin."
        code = f"LOCK_2_TIER_{crit_tier.value}"
        return crit_tier, classification, False, reason, code

    if not lock2_passed:
        reason = f"LOCK_2_BREACH ({classification.category.value.upper()}): {classification.rationale}"
        code = f"LOCK_2_{classification.category.value.upper()}"
        return crit_tier, classification, False, reason, code

    return crit_tier, classification, True, None, None


def _calibrate_options(
    options: Sequence[Any],
    decision_store: Optional[Any],
    category: Optional[str],
    pre_calibrated: bool,
    project: Optional[str] = None,
    subject_key: Optional[str] = None,
) -> Tuple[List[Any], bool, int]:
    is_calibrated = pre_calibrated or (
        bool(options)
        and isinstance(options[0], Mapping)
        and ("historical_adjustment" in options[0] or options[0].get("confidence_is_precise") is not None)
    )
    if is_calibrated or (decision_store is None and category is None):
        count = sum(int(opt.get("confidence_sample_count", 0)) for opt in options if isinstance(opt, Mapping))
        return list(options), False, count

    target_store = decision_store
    if target_store is None and category:
        with contextlib.suppress(Exception):
            from .ledger import HITLDecisionStore
            target_store = HITLDecisionStore()

    if target_store is not None and category:
        with contextlib.suppress(Exception):
            calibrated = target_store.calibrate_options(
                options, category=category, project=project, subject_key=subject_key
            )
            count = sum(int(opt.get("confidence_sample_count", 0)) for opt in calibrated if isinstance(opt, Mapping))
            return calibrated, True, count

    count = sum(int(opt.get("confidence_sample_count", 0)) for opt in options if isinstance(opt, Mapping))
    return list(options), False, count


def _rank_and_measure_options(
    options: Sequence[Any],
    require_confidence: bool = True,
) -> Tuple[List[Any], List[float], float, float, float, float, float]:
    scored = []
    for opt in options:
        s = extract_canonical_option_confidence(opt, require_confidence=require_confidence)
        scored.append((s, opt))
    scored.sort(key=lambda x: x[0], reverse=True)

    sorted_opts = [o for _, o in scored]
    scores = [s for s, _ in scored]
    c_top = scores[0]
    num_opts = len(scores)
    c_second = scores[1] if num_opts > 1 else 0.0
    margin = round(c_top - c_second, 4) if num_opts > 1 else 1.0
    norm_entropy = calculate_normalized_entropy(scores)
    epist_entropy = calculate_epistemic_entropy(scores)
    return sorted_opts, scores, c_top, c_second, margin, norm_entropy, epist_entropy


def evaluate_canonical_decision(
    options: Sequence[Any],
    context: Optional[Mapping[str, Any]] = None,
    target_files: Optional[Sequence[str]] = None,
    action_description: Optional[str] = None,
    margin_threshold: Optional[float] = None,
    confidence_floor: Optional[float] = None,
    max_entropy: Optional[float] = None,
    policy_snapshot: Optional[Any] = None,
    require_confidence: bool = True,
    question_text: Optional[str] = None,
    enable_lock2: bool = True,
    surfacing_mode: Optional[str] = None,
    criticality_tier: Optional[Union[CriticalityTier, int]] = None,
    decision_store: Optional[Any] = None,
    category: Optional[Union[DecisionCategory, str]] = None,
    pre_calibrated: bool = False,
) -> CanonicalDecisionEvaluation:
    """Unified single entry point evaluating option confidence, entropy, and criticality."""
    if not options:
        raise ValueError("CanonicalDecisionEvaluator requires at least one option")

    eff_thresh, eff_floor, eff_ent = _resolve_effective_parameters(
        margin_threshold, confidence_floor, max_entropy, policy_snapshot
    )
    crit_tier, classification, lock2_passed, lock2_reason, lock2_code = _resolve_criticality_lock2(
        criticality_tier, context, target_files, action_description, options, policy_snapshot, enable_lock2, category
    )
    proj = str(context.get("project") or context.get("project_id") or "").strip() if context else None
    subj = str(context.get("subject_key") or context.get("subject") or "").strip() if context else None
    calibrated_opts, calib_applied, sample_count = _calibrate_options(
        options, decision_store, category, pre_calibrated, project=proj or None, subject_key=subj or None
    )
    sorted_opts, scores, c_top, c_second, margin, norm_entropy, epist_entropy = _rank_and_measure_options(
        calibrated_opts, require_confidence
    )
    lock1_passed, floor_breached, reasons, codes = _evaluate_lock1_and_reasons(
        len(scores), c_top, c_second, margin, norm_entropy, eff_thresh, eff_floor, eff_ent, surfacing_mode=surfacing_mode
    )
    if lock2_reason:
        reasons.insert(0, lock2_reason)
        codes.insert(0, lock2_code or "LOCK_2_BREACH")

    autonomous_permitted = lock1_passed and lock2_passed
    surface_hitl = not autonomous_permitted

    q_text = (
        question_text
        or (context.get("question") if context else None)
        or action_description
        or "Which option should be authorized?"
    )
    ask_payload = (
        build_canonical_ask_user_question_payload(sorted_opts, q_text)
        if surface_hitl
        else None
    )

    return CanonicalDecisionEvaluation(
        autonomous_permitted=autonomous_permitted,
        surface_hitl=surface_hitl,
        lock1_passed=lock1_passed,
        lock2_passed=lock2_passed,
        category=classification.category,
        criticality_tier=crit_tier,
        classification=classification,
        margin=margin,
        margin_threshold=eff_thresh,
        top_confidence=c_top,
        runner_up_confidence=c_second,
        confidence_floor=eff_floor,
        confidence_floor_breached=floor_breached,
        normalized_entropy=norm_entropy,
        epistemic_entropy=epist_entropy,
        top_option=sorted_opts[0],
        second_option=sorted_opts[1] if len(sorted_opts) > 1 else None,
        reasons=reasons,
        reason_codes=codes,
        ask_user_question_payload=ask_payload,
        policy_snapshot=policy_snapshot,
        calibrated_options=[dict(o) if isinstance(o, Mapping) else {"label": str(o), "confidence_score": s} for s, o in zip(scores, sorted_opts)],
        historical_sample_count=sample_count,
        calibration_applied=calib_applied,
    )
