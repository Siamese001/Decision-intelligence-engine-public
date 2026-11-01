# ruff: noqa: F401
"""Query core subpackage re-exports."""

from __future__ import annotations

import json
import os
import re
import shutil
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from dual_pipeline.assurance import (
    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    INTERNAL_OPERATIONAL,
    load_internal_operational_policy,
    pair_assurance_tier,
    require_internal_operational_use,
)
from dual_pipeline.assurance import (
    policy_digest as internal_operational_policy_digest,
)
from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.card_views import resolve_authorized_card_scope
from dual_pipeline.contracts import create_artifact, create_versioned_artifact, validate_artifact
from dual_pipeline.core import (
    assert_safe_mutable_path,
    connect_sqlite,
    digest,
    is_link_or_reparse,
    normalize_space,
    read_json,
    sha256_bytes,
    sha256_file,
    stable_id,
    trace_id,
    utc_now,
    write_json,
)
from dual_pipeline.delivery_quality import evaluate_delivery_quality
from dual_pipeline.drift_guard import (
    DriftError,
    assert_replay_binding,
    assert_runtime_binding_unchanged,
    capture_runtime_binding,
)
from dual_pipeline.evidence_contract import create_final_evidence_contract
from dual_pipeline.graph_retrieval import bm25_query_tokens, expand_connected_retrieval
from dual_pipeline.index import (
    CLASSIFICATION_RANK,
    HYBRID_RETRIEVAL_POLICY_DIGEST,
    EmbeddingBackend,
    HybridRetriever,
    resolve_retrieval_mode,
    retrieval_metadata_for_record,
    retrieval_metadata_from_artifact,
    retrieval_record_type_for_assertion,
)
from dual_pipeline.l1_control import BudgetState, L1ControlPolicy, create_action, load_policy
from dual_pipeline.l2 import (
    DECISION_OUTPUT_CONTRACT_DIGEST,
    CallPurpose,
    L2Config,
    L2Disposition,
    L2Policy,
    ReasoningProviderFactory,
    analyze_request_intent,
    canonical_query_type,
    decide_l2_execution,
    determine_materiality,
    execute_conditional_l2,
    execute_content_repair,
    external_provider_offline,
    load_l2_config,
    provider_quality_requirements,
    query_retrieval_policy,
    query_type_policy,
    retrieval_lanes_for_mode,
)
from dual_pipeline.l2_output_contract import (
    material_citation_obligations,
    record_evidence_binding_map,
)
from dual_pipeline.label_quality import APPROVED_VALIDATION_STATES
from dual_pipeline.prompt_assembly import assemble_prompt as _assemble_prompt_impl
from dual_pipeline.prompt_assembly import compile_prompt_artifact
from dual_pipeline.query_assurance import (
    create_l2_decision_artifact,
    select_assurance_profile,
)
from dual_pipeline.query_eligibility import assess_query_graph_fit
from dual_pipeline.query_l1_actions import execute_l1_action, write_controlled_retrieval_artifact
from dual_pipeline.query_l1_controller import (
    bounded_telemetry_payload,
    build_bounded_terminal,
    build_complete_first_pass_terminal,
    build_observation_one,
    feedback_eligible,
    merge_retrieval_results,
)
from dual_pipeline.query_l1_shadow import (
    ShadowReceiptSet,
    build_attempt_zero,
    build_observation_zero,
    build_shadow_receipts,
    shadow_telemetry_payload,
    write_shadow_receipts,
)
from dual_pipeline.query_planning import compile_query_plan_candidate, compile_requirement_query
from dual_pipeline.query_result import QueryResult
from dual_pipeline.query_router import (
    ARTIFACT_TYPES,
    DELIVERY_PROFILES,
    MAX_QUESTION_LENGTH,
    DestinationDecision,
    QueryDestination,
    QueryRoutingPolicy,
    load_query_routing_policy,
    route_query,
)
from dual_pipeline.query_u0 import QueryU0Validator, ValidatedQueryRequest
from dual_pipeline.release import ReleaseManager
from dual_pipeline.sealed_l2 import seal_l2_output
from dual_pipeline.semantic_cache import SemanticCache, SemanticCacheLookup
from dual_pipeline.strategic_reasoning import (
    build_strategic_channels,
    create_strategic_evidence_artifact,
    create_strategic_reasoning_output,
    explicit_strategic_profile,
)
from dual_pipeline.telemetry import RunRecorder
from dual_pipeline.x1_quality import (
    QualityProviderFactory,
    QualityReviewStatus,
    QualityRole,
    QualityVerdict,
    X1QualityConfig,
    X1QualityMode,
    load_x1_quality_config,
    run_quality_review,
)

from .cache import (
    _cached_query_output,
    _correlation,
    _delivery_evidence_ready,
    _emit_l1_shadow_receipts,
    _query_cache_context,
    _rebind_cached_connected_evidence_map,
    _rebind_cached_retrieval_artifacts,
)
from .constants import (
    _COVERAGE_CONCLUSION,
    _COVERAGE_OUTPUT_ONLY,
    CITATION_ONLY_SOURCE_EVIDENCE_RETRIEVAL_MODES,
    GRAPH_HOPS_BY_QUERY_TYPE,
    MAX_L2_INPUT_RECORDS_PER_LANE,
    MAX_QUERY_COVERAGE_REQUIREMENTS,
    MAX_REQUIRED_MATERIAL_RECORDS,
    MAX_RETRIEVAL_EVIDENCE_REPORT_BYTES,
    QUERY_STAGES,
    _claim_new_directory,
    _entities,
)
from .coverage import (
    _explicit_coverage_items,
    _named_coverage_subjects,
    _semantic_coverage_facets,
    build_claim_evidence_map,
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
    coverage_complete,
    coverage_supplemental_retrieval,
    filter_packet_for_l2,
    infer_query_type,
    render_retrieval_evidence_markdown,
)
from .engine import (
    QueryEngine,
    _finish_pre_l2_blocked,
    _finish_routed_request,
    _QueryRunState,
)
from .evidence_locator import (
    EvidenceLocatorResult,
    EvidenceLocatorService,
    ForensicDialogueTurn,
)
from .firewall import (
    GraphDBRequiredError,
    ProhibitedCommandGuard,
    ViolationMatch,
    check_command_firewall,
)
from .graph_first_engine import (
    GraphFirstQueryEngine,
    GraphQueryResult,
    TierExecutionMetrics,
)
from .hydration import (
    build_evidence_packet,
    exact_retrieval,
    hydrate_entity_facet_results,
    hydrate_results,
)
from .planning import (
    build_query_plan,
    build_query_route,
)
from .reasoning import (
    _record_tokens,
    assemble_prompt,
    build_x1_gate,
    evidence_only_over_packet,
    reason_over_packet,
    render_markdown,
    validate_answer,
)
from .hybrid_reranker import (
    HybridGraphVectorReranker,
    RankedCandidate,
    evaluate_retrieval_benchmark,
    precision_at_k,
    rank_by_topological_distance,
    reciprocal_rank,
    reciprocal_rank_fusion,
)
from .lineage_formatter import (
    CitationItem,
    SeamlessQueryResponse,
    format_seamless_query_response,
    validate_seamless_prose,
)


