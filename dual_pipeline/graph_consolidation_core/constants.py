"""
dual_pipeline/graph_consolidation_core/constants.py

SSOT constants, schemas, and parameter bounds for Graph Consolidation.
Compatible with learning-change-proposal:1.0.0 and ParameterRegistry.
"""
from __future__ import annotations

from typing import Dict, Set

from dual_pipeline.parameters import (
    get_graph_consolidation_parameters,
    get_hitl_ambiguity_margin_threshold,
)

CONSOLIDATION_POLICY_VERSION = "1.0.0"
CONSOLIDATION_PLAN_SCHEMA_VERSION = "1.0.0"

# HITL decision ambiguity threshold (AGENTS.md invariant)
HITL_AMBIGUITY_MARGIN_THRESHOLD = get_hitl_ambiguity_margin_threshold()

# Default similarity thresholds (SSOT config/runtime/parameters.json)
_CONSOLIDATION_PARAMS = get_graph_consolidation_parameters()
DEFAULT_NODE_SIMILARITY_THRESHOLD = float(_CONSOLIDATION_PARAMS["default_node_similarity_threshold"])
DEFAULT_LITERAL_SIMILARITY_THRESHOLD = float(_CONSOLIDATION_PARAMS["default_literal_similarity_threshold"])
DEFAULT_EVIDENCE_SIMILARITY_THRESHOLD = float(_CONSOLIDATION_PARAMS["default_evidence_similarity_threshold"])

# Precedence for survivor selection across cross-type duplicate entities
# Higher rank wins survivor role
NODE_TYPE_PRECEDENCE: Dict[str, int] = {
    "workstream": 100,
    "program": 95,
    "project": 95,
    "product_lane": 90,
    "sow": 80,
    "stakeholder": 75,
    "person": 70,
    "org": 65,
    "technical_anchor": 60,
    "system": 55,
    "tool": 50,
    "platform": 45,
}

# Compatible concept node types eligible for cross-type multi-token containment clustering
COMPATIBLE_CONCEPT_TYPES: Set[str] = frozenset({
    "workstream",
    "program",
    "project",
    "product_lane",
    "deliverable",
    "pattern",
    "technical_anchor",
    "system",
    "tool",
    "platform",
})

# Incompatible cross-type pairings that must NEVER be auto-merged
INCOMPATIBLE_TYPE_PAIRS: Set[frozenset[str]] = {
    frozenset({"person", "system"}),
    frozenset({"org", "person"}),
    frozenset({"policy", "person"}),
    frozenset({"org", "program"}),
    frozenset({"org", "technical_anchor"}),
}

# Stopwords for normalized entity tokenization
STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to", "by", "with",
    "project", "initiative", "program", "team", "workstream", "phase", "stage",
    "application", "app", "system", "platform", "tool", "solution",
})
