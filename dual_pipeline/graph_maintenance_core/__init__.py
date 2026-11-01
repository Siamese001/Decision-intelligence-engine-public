"""
dual_pipeline/graph_maintenance_core

Automated graph maintenance, semantic assertion deduplication,
retrieval index pruning, and graph health auditing.
"""
from __future__ import annotations

from .candidate_replay import (
    CohortEvaluationReport,
    IsolatedCandidateReplay,
)
from .dedup import (
    apply_supersessions,
    find_semantic_duplicates,
)
from .harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    HygieneFinding,
    MaintenancePlanReport,
    QueryImpactRecord,
    RankedMaintenanceProposal,
)
from .health import (
    calculate_graph_health_metrics,
    render_health_report_markdown,
)
from .pruning import (
    prune_superseded_search_documents,
)
from .supersession_classifier import (
    MULTI_VALUED_PREDICATES,
    MaintenanceOutcomeResult,
    MaintenanceOutcomeType,
    are_intervals_disjoint,
    classify_assertion_relationship,
    is_multi_valued_predicate,
)

__all__ = [
    "find_semantic_duplicates",
    "apply_supersessions",
    "prune_superseded_search_documents",
    "calculate_graph_health_metrics",
    "render_health_report_markdown",
    "HarmPrioritizedPlanner",
    "HygieneFinding",
    "QueryImpactRecord",
    "RankedMaintenanceProposal",
    "MaintenancePlanReport",
    "IsolatedCandidateReplay",
    "CohortEvaluationReport",
    "MaintenanceOutcomeType",
    "MaintenanceOutcomeResult",
    "classify_assertion_relationship",
    "are_intervals_disjoint",
    "is_multi_valued_predicate",
    "MULTI_VALUED_PREDICATES",
]
