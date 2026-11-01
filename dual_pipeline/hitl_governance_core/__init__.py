"""
dual_pipeline/hitl_governance_core/__init__.py

Modularized core components for Human-in-the-Loop governance.
"""
from __future__ import annotations

from .approval_lookup import (
    ApprovalResolutionResult,
    ApprovalResolutionStatus,
    resolve_hitl_approval_reference,
)
from .audit_events import (
    DecisionResolutionEvent,
    IndependentOutcomeEvent,
    OperatorAnswerEvent,
    QuestionDeliveryEvent,
    emit_audit_event,
    sanitize_audit_payload,
)
from .bayesian import (
    CalibratedScore,
    ConfidenceResult,
    _match_keys,
    _normalize_key_tokens,
    _parse_iso_timestamp,
    compute_bayesian_calibrated_score,
    compute_beta_credible_interval,
)
from .classifier import (
    MANDATORY_HITL_CATEGORIES,
    DecisionCategory,
    DecisionClassificationResult,
    classify_decision_criticality,
)
from .dual_lock import (
    DualLockEvaluationResult,
    evaluate_dual_lock,
)
from .learning_eligibility import (
    compute_record_eligibility,
    deduplicate_records_for_learning,
    extract_override_recommendation,
    filter_frozen_priors,
    is_autonomous_record,
    is_override_record,
    is_test_record,
)
from .scoped_autonomy import (
    EvidenceCheckBudget,
    EvidenceCheckLimitExceeded,
    ExactContextReviewCache,
    LabelType,
    ScopedDecisionSnapshot,
    create_scoped_decision_snapshot,
    is_autonomous_alias,
)
from .layman_clarity import (
    LaymanClarityError,
    build_layman_option,
    contains_jargon,
    format_layman_display_label,
    scan_for_jargon,
    validate_impact_statement,
    validate_layman_option,
    validate_plain_english,
)
from .ledger import HITLDecisionStore, resolve_hitl_ledger_path
from .pre_surfacing import evaluate_pre_surfacing_gate
from .surfacing import (
    CONFIDENCE_TIER_SCORES,
    DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_DIVERGENCE_THRESHOLD,
    DEFAULT_ENTROPY_THRESHOLD,
    DEFAULT_MARGIN_THRESHOLD,
    DEFAULT_SURFACING_MODE,
    LEGACY_MARGIN_THRESHOLD,
    REQUIRED_HITL_PHRASES,
    SYNTHETIC_APPROVAL_MARKERS,
    HitlConfidenceMissingError,
    HitlSurfacingMode,
    _extract_percentage,
    _extract_tier,
    _option_key,
    _standardize_option,
    calculate_normalized_entropy,
    evaluate_hitl_surfacing_gate,
    parse_confidence_score,
    validate_approval_origin,
    validate_hitl_option,
)
from .validation import (
    validate_hitl_governance_controls,
    validate_hitl_presentation,
)

__all__ = [
    "ApprovalResolutionResult",
    "ApprovalResolutionStatus",
    "resolve_hitl_approval_reference",
    "CONFIDENCE_TIER_SCORES",
    "CalibratedScore",
    "ConfidenceResult",
    "DEFAULT_CONFIDENCE_FLOOR",
    "DEFAULT_DIVERGENCE_THRESHOLD",
    "DEFAULT_ENTROPY_THRESHOLD",
    "DEFAULT_MARGIN_THRESHOLD",
    "DEFAULT_SURFACING_MODE",
    "HITLDecisionStore",
    "HitlConfidenceMissingError",
    "HitlSurfacingMode",
    "LEGACY_MARGIN_THRESHOLD",
    "REQUIRED_HITL_PHRASES",
    "SYNTHETIC_APPROVAL_MARKERS",
    "_extract_percentage",
    "_extract_tier",
    "_match_keys",
    "_normalize_key_tokens",
    "_option_key",
    "_parse_iso_timestamp",
    "_standardize_option",
    "calculate_normalized_entropy",
    "compute_bayesian_calibrated_score",
    "compute_beta_credible_interval",
    "evaluate_hitl_surfacing_gate",
    "evaluate_pre_surfacing_gate",
    "parse_confidence_score",
    "resolve_hitl_ledger_path",
] + [
    "LaymanClarityError",
    "build_layman_option",
    "contains_jargon",
    "format_layman_display_label",
    "scan_for_jargon",
    "validate_approval_origin",
    "validate_hitl_governance_controls",
    "validate_hitl_option",
    "validate_hitl_presentation",
    "validate_impact_statement",
    "validate_layman_option",
    "validate_plain_english",
    "DecisionCategory",
    "DecisionClassificationResult",
    "MANDATORY_HITL_CATEGORIES",
    "classify_decision_criticality",
    "DualLockEvaluationResult",
    "evaluate_dual_lock",
    "compute_record_eligibility",
    "deduplicate_records_for_learning",
    "extract_override_recommendation",
    "filter_frozen_priors",
    "is_autonomous_record",
    "is_override_record",
    "is_test_record",
    "EvidenceCheckBudget",
    "EvidenceCheckLimitExceeded",
    "ExactContextReviewCache",
    "LabelType",
    "ScopedDecisionSnapshot",
    "create_scoped_decision_snapshot",
    "is_autonomous_alias",
]
