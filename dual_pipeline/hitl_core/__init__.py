"""
dual_pipeline.hitl_core: Human-in-the-Loop decision evaluation, confidence margin analysis,
and atomic card presentation.
"""
from .envelope import HitlRequirementEnvelope, compute_normalized_entropy, create_hitl_envelope
from .margin_evaluator import AMBIGUITY_MARGIN_THRESHOLD, MarginEvaluator

__all__ = [
    "MarginEvaluator",
    "AMBIGUITY_MARGIN_THRESHOLD",
    "HitlRequirementEnvelope",
    "create_hitl_envelope",
    "compute_normalized_entropy",
]
