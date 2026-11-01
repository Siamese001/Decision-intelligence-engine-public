"""Core package for governed intake planning, routing, HITL batching, and candidate disposition."""

from __future__ import annotations

from .change_set import build_graph_change_set
from .comparison import (
    _cached_comparison_output,
    _intake_cache_context,
    _valid_cached_comparison,
    compare_candidates,
    hydrate_comparison_context,
)
from .constants import (
    _A2A_FINALIZATION_BINDING_FIELDS,
    A2A_GUIDANCE_ANCHOR_KEY,
    A2A_SOURCE_SPAN_REVIEW_BINDING_KEY,
    EXTERNAL_CONCEPT_OUTCOME,
    EXTERNAL_CONCEPT_PROPOSAL_KEY,
    FACT_PROMOTION_READY_STATUSES,
    GRAPH_ADMISSION_EXTENSION_KEY,
    IDENTITY_REVIEW_EXTENSION_KEY,
    INGESTION_DECISION_LEDGER_ENGLISH_JSON,
    INGESTION_DECISION_LEDGER_JSON,
    INGESTION_DECISION_LEDGER_MARKDOWN,
    INTAKE_STAGES,
    NEW_INTERNAL_ENTITY_DIGEST_KEY,
    NEW_INTERNAL_ENTITY_KEY,
    RETIRE_ASSERTION_KEY,
    SEMANTIC_CORRECTION_CANDIDATE_KEY,
    SUPERSEDES_ASSERTION_KEY,
)
from .decision_ledger import (
    _decision_gate_status,
    _markdown_table_cell,
    build_ingestion_decision_explanation,
    build_ingestion_decision_ledger,
    render_ingestion_decision_ledger_markdown,
)
from .dispositions import build_dispositions
from .graph_delta_distiller import (
    DocumentGraphDeltaReport,
    distill_document_graph_delta,
    is_portfolio_allocation_relationship,
)
from .hitl import (
    _candidate_governance,
    _decision_map,
    _forbid_a2a_approved_fact_promotion,
    _validate_a2a_source_span_review_binding,
    build_hitl_batches,
)
from .planning import build_ingestion_plan
from .proposals import (
    _a2a_guidance_anchor,
    _candidate_resolution_map,
    _correlation,
    _external_concept_proposal,
    _is_a2a_source_bundle,
    _new_internal_entity_proposal,
    _semantic_correction_control,
    _validated_a2a_finalization_binding,
)
from .routing import (
    _route_for_candidate,
    build_routes,
)

__all__ = [
    "A2A_GUIDANCE_ANCHOR_KEY",
    "A2A_SOURCE_SPAN_REVIEW_BINDING_KEY",
    "EXTERNAL_CONCEPT_OUTCOME",
    "EXTERNAL_CONCEPT_PROPOSAL_KEY",
    "FACT_PROMOTION_READY_STATUSES",
    "GRAPH_ADMISSION_EXTENSION_KEY",
    "IDENTITY_REVIEW_EXTENSION_KEY",
    "INGESTION_DECISION_LEDGER_ENGLISH_JSON",
    "INGESTION_DECISION_LEDGER_JSON",
    "INGESTION_DECISION_LEDGER_MARKDOWN",
    "INTAKE_STAGES",
    "NEW_INTERNAL_ENTITY_DIGEST_KEY",
    "NEW_INTERNAL_ENTITY_KEY",
    "RETIRE_ASSERTION_KEY",
    "SEMANTIC_CORRECTION_CANDIDATE_KEY",
    "SUPERSEDES_ASSERTION_KEY",
    "_A2A_FINALIZATION_BINDING_FIELDS",
    "_a2a_guidance_anchor",
    "_cached_comparison_output",
    "_candidate_governance",
    "_candidate_resolution_map",
    "_correlation",
    "_decision_gate_status",
    "_decision_map",
    "_external_concept_proposal",
    "_forbid_a2a_approved_fact_promotion",
    "_intake_cache_context",
    "_is_a2a_source_bundle",
    "_markdown_table_cell",
    "_new_internal_entity_proposal",
    "_route_for_candidate",
    "_semantic_correction_control",
    "_valid_cached_comparison",
    "_validate_a2a_source_span_review_binding",
    "_validated_a2a_finalization_binding",
    "build_dispositions",
    "build_graph_change_set",
    "build_hitl_batches",
    "build_ingestion_decision_explanation",
    "build_ingestion_decision_ledger",
    "build_ingestion_plan",
    "build_routes",
    "compare_candidates",
    "distill_document_graph_delta",
    "DocumentGraphDeltaReport",
    "hydrate_comparison_context",
    "is_portfolio_allocation_relationship",
    "render_ingestion_decision_ledger_markdown",
]
