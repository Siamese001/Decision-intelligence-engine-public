"""
dual_pipeline/decision_engine_core/__init__.py

Modularized core packages for evidence-bound decision intelligence.
"""

from .assessment import assess_options
from .evaluation import evaluate_decision
from .evidence_pack import build_decision_evidence_pack
from .frame import build_decision_frame
from .options import build_viable_options

__all__ = [
    "assess_options",
    "build_decision_evidence_pack",
    "build_decision_frame",
    "build_viable_options",
    "evaluate_decision",
]
