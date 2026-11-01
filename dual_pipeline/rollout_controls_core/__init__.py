"""dual_pipeline.rollout_controls_core

Public API for Wave 12 cross-pipeline qualification, staged rollout controls,
and reversible tier gates.
"""
from __future__ import annotations

from .cross_pipeline_evaluator import (
    CrossPipelineEvaluationReport,
    CrossPipelineEvaluator,
)
from .rollout_gate import (
    RolloutDecision,
    RolloutGate,
    RolloutTier,
)

__all__ = [
    "CrossPipelineEvaluationReport",
    "CrossPipelineEvaluator",
    "RolloutDecision",
    "RolloutGate",
    "RolloutTier",
]
