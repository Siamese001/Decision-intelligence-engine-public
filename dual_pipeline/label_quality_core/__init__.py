"""
dual_pipeline.label_quality_core

Modularized graph label governance, audit metrics, legacy migration plans,
and candidate execution.
"""
from __future__ import annotations

from .audiences import (
    canonical_audiences_json,
    canonicalize_audiences,
)
from .audit import (
    _audience_metrics,
    _normalized_coverage,
    _policy_state,
    _scalar,
    _table_exists,
    approved_validation_state,
    assert_label_quality_no_regression,
    audit_graph_labels,
    candidate_validation_state,
    compare_label_quality,
)
from .constants import (
    _APPROVAL_COLUMNS,
    _CANDIDATE_BASE_TABLES,
    _CANDIDATE_HARDENING_TABLES,
    _CANDIDATE_REQUIRED_TABLES,
    _CANDIDATE_REQUIRED_VALUES,
    APPROVED_VALIDATION_STATES,
    AUDIENCE_PATTERN,
    CANONICAL_EVIDENCE_DIRECTNESS,
    CANONICAL_SOURCE_POSTURES,
    LABEL_MIGRATION_CANDIDATE_CONTRACT_ID,
    LABEL_MIGRATION_CANDIDATE_STATUS,
    LABEL_MIGRATION_SCHEMA_ID,
    LABEL_MIGRATION_SCHEMA_STATES,
    LABEL_POLICY_ID,
    LABEL_POLICY_VERSION,
    NO_REGRESSION_METRICS,
    ZERO_HASH,
    LabelQualityError,
)
from .legacy_plan import (
    _review_item,
    build_legacy_label_migration_plan,
)
from .migration import (
    _approval_for_label_candidate,
    _assert_candidate_hardening_preconditions,
    _assert_legacy_plan_digest,
    _assert_registered_value,
    _candidate_operation,
    _candidate_schema_state,
    _ensure_mapping_rule,
    _insert_normalized_label_state,
    _label_hardening_schema_path,
    _legacy_state_for_operation,
    _mapping_canonical_value,
    _mapping_rule_id,
    _normalized_row_for_operation,
    _row_hash,
    _validate_label_candidate_approval_record,
    apply_approved_deterministic_label_migration_candidate,
    build_deterministic_label_migration_candidate_plan,
    validate_deterministic_label_migration_candidate_plan,
)
