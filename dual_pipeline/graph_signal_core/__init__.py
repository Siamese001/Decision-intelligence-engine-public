"""dual_pipeline.graph_signal_core: Quantitative Graph Signal Integrity and Pre/Post Evaluation."""

from __future__ import annotations

from .constants import (
    GRAPH_SIGNAL_SCHEMA_VERSION,
    MIN_APPROVED_LANE_PURITY,
    MIN_COMPOSITE_SCORE,
    MIN_CONFIDENCE_MEAN,
    MIN_DENSITY_RHO,
    MIN_GROUNDING_DEPTH,
    MIN_TEMPORAL_FRESHNESS,
    MIN_TYPEDNESS_TAU,
    MUTEX_CARDINALITY_PREDICATES,
    SIGNAL_WEIGHTS,
)
from .evaluator import evaluate_differential_signal, evaluate_graph_signal
from .freshness import GraphFreshnessError, calculate_graph_freshness
from .mutex_invalidation import (
    find_conflicting_assertions,
    is_functional_mutex_target,
    reconcile_all_graph_mutex,
    supersede_conflicting_assertions,
)

__all__ = [
    "GRAPH_SIGNAL_SCHEMA_VERSION",
    "GraphFreshnessError",
    "MIN_APPROVED_LANE_PURITY",
    "MIN_COMPOSITE_SCORE",
    "MIN_CONFIDENCE_MEAN",
    "MIN_DENSITY_RHO",
    "MIN_GROUNDING_DEPTH",
    "MIN_TEMPORAL_FRESHNESS",
    "MIN_TYPEDNESS_TAU",
    "MUTEX_CARDINALITY_PREDICATES",
    "SIGNAL_WEIGHTS",
    "calculate_graph_freshness",
    "evaluate_differential_signal",
    "evaluate_graph_signal",
    "find_conflicting_assertions",
    "is_functional_mutex_target",
    "reconcile_all_graph_mutex",
    "supersede_conflicting_assertions",
]
