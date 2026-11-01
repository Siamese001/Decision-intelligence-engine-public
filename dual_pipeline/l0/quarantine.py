"""Dual-Pipeline Layer 0: Multi-Dimensional Soft-Quarantine Governance.

Implements Law 5: Multi-dimensional soft quarantine.
Replaces destructive deletion with non-destructive, independent multi-axis scoring:
1. strategic_value (0.0 to 1.0)
2. evidence_quality (0.0 to 1.0)
3. confidence (0.0 to 1.0)
4. tacticality (0.0 to 1.0)
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from dual_pipeline.l0.contracts import QuarantineProfile, QuarantineReason
from dual_pipeline.parameters import get_float_parameter

# Governed baseline default thresholds (bound to config/runtime/parameters.json)
DEFAULT_CONFIDENCE_THRESHOLD: float = get_float_parameter("l0.quarantine_thresholds.confidence_min", 0.60)
DEFAULT_STRATEGIC_THRESHOLD: float = get_float_parameter("l0.quarantine_thresholds.strategic_value_min", 0.40)
DEFAULT_EVIDENCE_QUALITY_THRESHOLD: float = get_float_parameter("l0.quarantine_thresholds.evidence_quality_min", 0.50)
DEFAULT_TACTICALITY_CEILING: float = get_float_parameter("l0.quarantine_thresholds.tacticality_max", 0.85)


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamps a floating-point score to [min_val, max_val]."""
    return max(min_val, min(max_val, float(value)))


def evaluate_quarantine_profile(
    strategic_value: float,
    evidence_quality: float,
    confidence: float,
    tacticality: float,
    confidence_threshold: Optional[float] = None,
    strategic_threshold: Optional[float] = None,
    evidence_quality_threshold: Optional[float] = None,
    tacticality_ceiling: Optional[float] = None,
) -> QuarantineProfile:
    """
    Evaluates independent 4-dimensional scores to assign soft-quarantine status and reason.
    Does NOT destroy the entity or fact; marks it for reversible gating.
    """
    s_val, eq_val, c_val, t_val = clamp(strategic_value), clamp(evidence_quality), clamp(confidence), clamp(tacticality)
    c_thresh = float(confidence_threshold) if confidence_threshold is not None else get_float_parameter("l0.quarantine_thresholds.confidence_min", DEFAULT_CONFIDENCE_THRESHOLD)
    s_thresh = float(strategic_threshold) if strategic_threshold is not None else get_float_parameter("l0.quarantine_thresholds.strategic_value_min", DEFAULT_STRATEGIC_THRESHOLD)
    eq_thresh = float(evidence_quality_threshold) if evidence_quality_threshold is not None else get_float_parameter("l0.quarantine_thresholds.evidence_quality_min", DEFAULT_EVIDENCE_QUALITY_THRESHOLD)
    t_ceil = float(tacticality_ceiling) if tacticality_ceiling is not None else get_float_parameter("l0.quarantine_thresholds.tacticality_max", DEFAULT_TACTICALITY_CEILING)

    is_quarantined = False
    reason = QuarantineReason.NONE.value

    # Evaluate individual dimensions in priority order
    if c_val < c_thresh:
        is_quarantined = True
        reason = QuarantineReason.LOW_CONFIDENCE.value
    elif eq_val < eq_thresh:
        is_quarantined = True
        reason = QuarantineReason.UNVERIFIED_SOURCE.value
    elif t_val > t_ceil:
        is_quarantined = True
        reason = QuarantineReason.HIGH_TACTICALITY.value
    elif s_val < s_thresh:
        is_quarantined = True
        reason = QuarantineReason.LOW_STRATEGIC_VALUE.value

    return QuarantineProfile(
        strategic_value=s_val,
        evidence_quality=eq_val,
        confidence=c_val,
        tacticality=t_val,
        is_quarantined=is_quarantined,
        quarantine_reason=reason,
    )


def apply_quarantine_to_dict(
    entity_dict: Dict[str, Any],
    profile: QuarantineProfile,
) -> Dict[str, Any]:
    """Applies a QuarantineProfile to an entity dictionary in-place and returns it."""
    entity_dict["strategic_value"] = profile.strategic_value
    entity_dict["evidence_quality"] = profile.evidence_quality
    entity_dict["confidence"] = profile.confidence
    entity_dict["tacticality"] = profile.tacticality
    entity_dict["is_quarantined"] = profile.is_quarantined
    entity_dict["quarantine_reason"] = profile.quarantine_reason
    return entity_dict
