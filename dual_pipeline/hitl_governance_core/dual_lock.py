"""
dual_pipeline/hitl_governance_core/dual_lock.py

Dual-Lock Decision Engine implementing the Fail-Closed Governance Matrix.
Eliminates the self-scoring autonomous evasion loophole by decoupling decision authority
from numerical confidence spreads:
    AutonomousResolutionPermitted = Lock_1 (Mathematical) AND Lock_2 (Criticality)
When Lock 2 fails (Architectural, Destructive, Policy, or Governance), Lock 1 is voided,
strictly mandating human operator review via ask_user_question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_entropy_ambiguity_threshold,
    get_hitl_min_autonomous_confidence_floor,
    needs_operator_decision,
)

from .classifier import (
    DecisionCategory,
    DecisionClassificationResult,
    classify_decision_criticality,
)
from .layman_clarity import (
    format_layman_display_label,
)
from .surfacing import (
    calculate_normalized_entropy,
    parse_confidence_score,
)


@dataclass(frozen=True)
class DualLockEvaluationResult:
    """Immutable result of Dual-Lock decision evaluation."""

    autonomous_permitted: bool
    surface_hitl: bool
    lock1_passed: bool
    lock2_passed: bool
    category: DecisionCategory
    classification: DecisionClassificationResult
    margin: float
    margin_threshold: float
    top_confidence: float
    confidence_floor: float
    normalized_entropy: float
    top_option: Optional[Any]
    second_option: Optional[Any]
    reasons: List[str] = field(default_factory=list)
    ask_user_question_payload: Optional[Dict[str, Any]] = None
    policy_snapshot: Optional[Any] = None

    @property
    def ask_question_payload(self) -> Optional[Dict[str, Any]]:
        """Deprecated backward compatibility alias for ask_user_question_payload."""
        return self.ask_user_question_payload

    @property
    def lock1_cleared(self) -> bool:
        return self.lock1_passed

    @property
    def lock2_cleared(self) -> bool:
        return self.lock2_passed

    @property
    def autonomous_eligible(self) -> bool:
        return self.autonomous_permitted

    def to_dict(self) -> Dict[str, Any]:
        return {
            "autonomous_permitted": self.autonomous_permitted,
            "surface_hitl": self.surface_hitl,
            "lock1_passed": self.lock1_passed,
            "lock2_passed": self.lock2_passed,
            "category": self.category.value,
            "margin": self.margin,
            "margin_threshold": self.margin_threshold,
            "top_confidence": self.top_confidence,
            "confidence_floor": self.confidence_floor,
            "normalized_entropy": self.normalized_entropy,
            "reasons": self.reasons,
            "classification": self.classification.to_dict(),
            "policy_snapshot": self.policy_snapshot.to_dict() if hasattr(self.policy_snapshot, "to_dict") else None,
            "ask_user_question_payload": self.ask_user_question_payload,
            "ask_question_payload": self.ask_user_question_payload,  # Deprecated compatibility alias
        }


def _extract_score(opt: Any) -> float:
    try:
        return parse_confidence_score(opt, require_confidence=False)
    except Exception:
        return 0.0


def _resolve_effective_policy(
    margin_threshold: Optional[float],
    confidence_floor: Optional[float],
    max_entropy: Optional[float],
    policy_snapshot: Optional[Any],
) -> Tuple[float, float, float]:
    eff_threshold = (
        margin_threshold if margin_threshold is not None else get_hitl_ambiguity_margin_threshold()
    )
    eff_floor = (
        confidence_floor
        if confidence_floor is not None
        else get_hitl_min_autonomous_confidence_floor()
    )
    eff_entropy = max_entropy if max_entropy is not None else get_hitl_entropy_ambiguity_threshold()

    if policy_snapshot is not None:
        eff_threshold = float(getattr(policy_snapshot, "ambiguity_threshold", eff_threshold))
        eff_floor = float(getattr(policy_snapshot, "conviction_floor", eff_floor))
        eff_entropy = float(getattr(policy_snapshot, "max_entropy", eff_entropy))

    return eff_threshold, eff_floor, eff_entropy


def _build_hitl_surfacing_payload(
    sorted_opts: Sequence[Any],
    context: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    formatted_options: List[str] = []
    for i, opt in enumerate(sorted_opts):
        rec_prefix = "(Recommended) " if i == 0 else ""
        if isinstance(opt, Mapping) and "plain_english_summary" in opt:
            lbl = format_layman_display_label(opt)
        elif isinstance(opt, Mapping):
            score_val = _extract_score(opt)
            pct = int(round(score_val * 100))
            tier = str(opt.get("confidence_level") or opt.get("tier") or "MEDIUM")
            desc = str(opt.get("label") or opt.get("text") or "Option")
            lbl = f"{rec_prefix}{desc} (Confidence: {tier} ({pct}%))"
        else:
            score_val = _extract_score(opt)
            pct = int(round(score_val * 100))
            lbl = f"{rec_prefix}{str(opt)} (Confidence: {pct}%)"
        formatted_options.append(lbl)

    q_text = str((context or {}).get("question") or "Which option should be authorized?")
    return {
        "questions": [
            {
                "question": q_text,
                "options": formatted_options,
                "is_multi_select": False,
            }
        ]
    }


def evaluate_dual_lock(
    options: Sequence[Any],
    context: Optional[Mapping[str, Any]] = None,
    target_files: Optional[Sequence[str]] = None,
    action_description: Optional[str] = None,
    margin_threshold: Optional[float] = None,
    confidence_floor: Optional[float] = None,
    max_entropy: Optional[float] = None,
    policy_snapshot: Optional[Any] = None,
) -> DualLockEvaluationResult:
    """
    Evaluates whether a decision may be resolved autonomously or must halt for HITL review.
    Enforces the Dual-Lock Invariant:
        Lock 1: Margin >= threshold, Top >= floor, Multi-option Entropy <= max_entropy
        Lock 2: Criticality category NOT in {ARCHITECTURAL, DESTRUCTIVE, POLICY, GOVERNANCE}
    Delegates to canonical evaluator (SSOT).
    """
    from .canonical_evaluator import evaluate_canonical_decision

    evaluation = evaluate_canonical_decision(
        options=options,
        context=context,
        target_files=target_files,
        action_description=action_description,
        margin_threshold=margin_threshold,
        confidence_floor=confidence_floor,
        max_entropy=max_entropy,
        policy_snapshot=policy_snapshot,
        enable_lock2=True,
    )
    return evaluation.to_dual_lock_result()
