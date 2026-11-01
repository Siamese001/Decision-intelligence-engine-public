"""
dual_pipeline/graph_consolidation_core

Governed on-demand semantic concept deduplication, triple consolidation,
and evidence fragmentation remediation engine.
"""
from __future__ import annotations

from .apply import apply_consolidation_plan
from .constants import (
    CONSOLIDATION_PLAN_SCHEMA_VERSION,
    CONSOLIDATION_POLICY_VERSION,
    HITL_AMBIGUITY_MARGIN_THRESHOLD,
)
from .detect.assertions import detect_assertion_overlaps
from .detect.evidence import detect_evidence_fragmentation
from .detect.nodes import detect_node_clusters
from .plan import build_consolidation_plan
from .policy import (
    DEFAULT_SUPERSEDION_POLICY,
    MAX_CONFIDENCE_MARGIN,
    MIN_CONFIDENCE_MARGIN,
    SupersessionPolicy,
    validate_supersession_policy,
)
from .release import (
    activate_consolidation_release,
    prepare_consolidation_release,
    record_consolidation_release_events,
)
from .resolver import (
    SupersessionDecision,
    find_governed_supersessions,
    resolve_graph_supersessions,
    resolve_pairwise_supersession,
)

__all__ = [
    "CONSOLIDATION_PLAN_SCHEMA_VERSION",
    "CONSOLIDATION_POLICY_VERSION",
    "DEFAULT_SUPERSEDION_POLICY",
    "HITL_AMBIGUITY_MARGIN_THRESHOLD",
    "MAX_CONFIDENCE_MARGIN",
    "MIN_CONFIDENCE_MARGIN",
    "SupersessionDecision",
    "SupersessionPolicy",
    "activate_consolidation_release",
    "apply_consolidation_plan",
    "build_consolidation_plan",
    "detect_assertion_overlaps",
    "detect_evidence_fragmentation",
    "detect_node_clusters",
    "find_governed_supersessions",
    "prepare_consolidation_release",
    "record_consolidation_release_events",
    "resolve_graph_supersessions",
    "resolve_pairwise_supersession",
    "validate_supersession_policy",
]

