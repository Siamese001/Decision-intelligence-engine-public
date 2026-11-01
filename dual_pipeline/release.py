"""Transactional graph writer and atomic graph/index pair release manager.

Backward-compatible facade module delegating to dual_pipeline.release_core.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storage.database import sqlite3

from .a2a_finalization import (
    a2a_normalized_content_projection,
    reverify_ingress_finalization_normalized_bundle,
    validated_ingress_finalization_reference,
    verified_finalization_binding,
)
from .assurance import (
    INTERNAL_OPERATIONAL,
    backend_assurance_tier,
    pair_assurance_tier,
)
from .authorization import AuthorizationContext
from .batch_admission import BATCH_MANIFEST_RESOURCE, binding_from_audit_record
from .contracts import validate_artifact
from .core import (
    ROOT,
    ZERO_HASH,
    assert_safe_mutable_path,
    connect_sqlite,
    digest,
    exact_file_copy,
    file_lock,
    is_link_or_reparse,
    read_json,
    seal_artifact,
    sha256_bytes,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from .graph_consolidation_core.release import (
    activate_consolidation_release,
    prepare_consolidation_release,
)
from .graph_delta import (
    build_actual_graph_delta,
    build_graph_mutation_summary,
    validate_planned_graph_delta,
)
from .graph_writer_core import GovernedGraphWriter
from .index import (
    CLASSIFICATION_RANK,
    EmbeddingBackend,
    build_sidecar,
    validate_sidecar,
)
from .intake import (
    INGESTION_DECISION_LEDGER_ENGLISH_JSON,
    INGESTION_DECISION_LEDGER_JSON,
    INGESTION_DECISION_LEDGER_MARKDOWN,
    render_ingestion_decision_ledger_markdown,
)
from .label_quality import (
    approved_validation_state,
    assert_label_quality_no_regression,
    audit_graph_labels,
    canonical_audiences_json,
    canonicalize_audiences,
)
from .node_type_policy import require_creatable_node_type
from .release_core import (
    _A2A_FINALIZATION_BINDING_FIELDS,
    _A2A_SOURCE_ATTEST_CAPABILITY,
    _A2A_SOURCE_ATTEST_RESOURCES,
    _QUALIFICATION_TRANSITION_MARKER,
    APPROVED_BGE_M3_BACKENDS,
    FACT_PROMOTION_READY_STATUSES,
    INTAKE_RELEASE_STAGES,
    RUN_ID_PATTERN,
    ReleaseManager,
    ConcurrentMutationConflictError,
    detect_mutation_conflicts,
    rebase_component_run,
    _a2a_intake_authorization_attests_source,
    _correlation,
    _default_retrieval_governance,
    _PrepareManyState,
    _replayed_a2a_payload_governance,
    _retrieval_governance_for_payload,
    _row_digest,
    _seal_receipt,
    _valid_a2a_finalization_binding,
    _verify_activation_binding,
    _verify_receipt,
    _verify_rollback_binding,
    release_action_resources,
    require_approved_bge_m3_pair,
)
from .strategic_reasoning import (
    STRATEGIC_PROFILE_QUALIFIER,
    validate_strategic_profile,
)
from .telemetry import RunRecorder
from .tier2_release import activate_dedup_release, prepare_dedup_release

__all__ = (
    "activate_consolidation_release",
    "activate_dedup_release",
    "APPROVED_BGE_M3_BACKENDS",
    "Any",
    "AuthorizationContext",
    "BATCH_MANIFEST_RESOURCE",
    "CLASSIFICATION_RANK",
    "ConcurrentMutationConflictError",
    "detect_mutation_conflicts",
    "rebase_component_run",
    "EmbeddingBackend",
    "FACT_PROMOTION_READY_STATUSES",
    "GovernedGraphWriter",
    "INGESTION_DECISION_LEDGER_ENGLISH_JSON",
    "INGESTION_DECISION_LEDGER_JSON",
    "INGESTION_DECISION_LEDGER_MARKDOWN",
    "INTAKE_RELEASE_STAGES",
    "INTERNAL_OPERATIONAL",
    "Path",
    "ROOT",
    "RUN_ID_PATTERN",
    "ReleaseManager",
    "RunRecorder",
    "STRATEGIC_PROFILE_QUALIFIER",
    "ZERO_HASH",
    "_A2A_FINALIZATION_BINDING_FIELDS",
    "_A2A_SOURCE_ATTEST_CAPABILITY",
    "_A2A_SOURCE_ATTEST_RESOURCES",
    "_PrepareManyState",
    "_QUALIFICATION_TRANSITION_MARKER",
    "_a2a_intake_authorization_attests_source",
    "_correlation",
    "_default_retrieval_governance",
    "_replayed_a2a_payload_governance",
    "_retrieval_governance_for_payload",
    "_row_digest",
    "_seal_receipt",
    "_valid_a2a_finalization_binding",
    "_verify_activation_binding",
    "_verify_receipt",
    "_verify_rollback_binding",
    "a2a_normalized_content_projection",
    "annotations",
    "approved_validation_state",
    "assert_label_quality_no_regression",
    "assert_safe_mutable_path",
    "audit_graph_labels",
    "backend_assurance_tier",
    "binding_from_audit_record",
    "build_actual_graph_delta",
    "build_graph_mutation_summary",
    "build_sidecar",
    "canonical_audiences_json",
    "canonicalize_audiences",
    "connect_sqlite",
    "dataclass",
    "digest",
    "exact_file_copy",
    "field",
    "file_lock",
    "is_link_or_reparse",
    "json",
    "pair_assurance_tier",
    "prepare_consolidation_release",
    "prepare_dedup_release",
    "re",
    "read_json",
    "release_action_resources",
    "render_ingestion_decision_ledger_markdown",
    "require_approved_bge_m3_pair",
    "require_creatable_node_type",
    "reverify_ingress_finalization_normalized_bundle",
    "seal_artifact",
    "sha256_bytes",
    "sha256_file",
    "sqlite3",
    "sqlite_integrity",
    "stable_id",
    "utc_now",
    "validate_artifact",
    "validate_planned_graph_delta",
    "validate_sidecar",
    "validate_strategic_profile",
    "validated_ingress_finalization_reference",
    "verified_finalization_binding",
    "write_json",
    "write_json_exclusive",
)

from .retrieval_release_core import validate_activation_evaluation

__all__ += ("validate_activation_evaluation",)
