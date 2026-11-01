"""
dual_pipeline/graph_consolidation_core/policy.py

Governed policy and parameter bounds for knowledge graph supersession and deduplication.
Complies with system-learning-and-feedback rules and SSOT parameter bounding.
Strictly provider-free and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set

from dual_pipeline.parameters import (
    get_graph_consolidation_parameters,
    get_hitl_ambiguity_margin_threshold,
)

# SSOT Parameter Bounds for Supersession and Consolidation
_CONSOLIDATION_BOUNDS = get_graph_consolidation_parameters()
MIN_CONFIDENCE_MARGIN: float = float(_CONSOLIDATION_BOUNDS["min_confidence_margin"])
MAX_CONFIDENCE_MARGIN: float = float(_CONSOLIDATION_BOUNDS["max_confidence_margin"])
DEFAULT_CONFIDENCE_MARGIN: float = get_hitl_ambiguity_margin_threshold()

EXCLUSIVE_CARDINALITIES: Set[str] = frozenset({
    "many_to_one",
    "one_to_one",
    "temporal_many_to_one",
})


@dataclass(frozen=True)
class SupersessionPolicy:
    """Immutable configuration governing assertion supersession resolution."""

    confidence_margin_threshold: float = DEFAULT_CONFIDENCE_MARGIN
    require_hitl_below_margin: bool = True
    evidence_count_precedence: bool = True
    authority_rank_precedence: bool = True
    recency_precedence: bool = True
    preserve_multi_valued_predicates: bool = True
    consolidate_evidence_on_supersede: bool = True
    exclusive_cardinalities: Set[str] = field(default_factory=lambda: EXCLUSIVE_CARDINALITIES)

    def __post_init__(self) -> None:
        if not (MIN_CONFIDENCE_MARGIN <= self.confidence_margin_threshold <= MAX_CONFIDENCE_MARGIN):
            raise ValueError(
                f"confidence_margin_threshold {self.confidence_margin_threshold} out of bounds "
                f"[{MIN_CONFIDENCE_MARGIN}, {MAX_CONFIDENCE_MARGIN}]"
            )


DEFAULT_SUPERSEDION_POLICY = SupersessionPolicy()


def validate_supersession_policy(policy: SupersessionPolicy) -> None:
    """Validates that a given policy satisfies SSOT parameter bounds."""
    if not isinstance(policy, SupersessionPolicy):
        raise TypeError(f"Expected SupersessionPolicy, got {type(policy).__name__}")
    if not (MIN_CONFIDENCE_MARGIN <= policy.confidence_margin_threshold <= MAX_CONFIDENCE_MARGIN):
        raise ValueError(
            f"confidence_margin_threshold {policy.confidence_margin_threshold} out of bounds "
            f"[{MIN_CONFIDENCE_MARGIN}, {MAX_CONFIDENCE_MARGIN}]"
        )
