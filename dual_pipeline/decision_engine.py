"""Evidence-bound Decision Intelligence stages (Facade).

Retrieval rank is deliberately absent from option evaluation. The engine
accepts a typed decision frame plus the existing query evidence artifacts,
constructs options separately from evidence, evaluates only evidence-visible
criteria, and recommends only when one option has a unique supported position.

Modularized into dual_pipeline.decision_engine_core under Master Invariant 1 (400-line budget).
"""

from __future__ import annotations

from .decision_engine_core import (
    assess_options,
    build_decision_evidence_pack,
    build_decision_frame,
    build_viable_options,
    evaluate_decision,
)
from .decision_engine_core.constants import sanitize_criterion
from .decision_models import (
    AssessmentState,
    ConfidenceBand,
    DecisionTerminalState,
    validate_decision_frame,
    validate_decision_result,
)

_sanitize_criterion = sanitize_criterion

__all__ = [
    "assess_options",
    "build_decision_evidence_pack",
    "build_decision_frame",
    "build_viable_options",
    "evaluate_decision",
    "validate_decision_frame",
    "validate_decision_result",
    "sanitize_criterion",
    "_sanitize_criterion",
    "AssessmentState",
    "ConfidenceBand",
    "DecisionTerminalState",
]
