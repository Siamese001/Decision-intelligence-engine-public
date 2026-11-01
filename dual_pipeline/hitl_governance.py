"""Governance and validation controls for Human-in-the-Loop (HITL) decisions.

Enforces that all HITL decisions across pipelines are:
1. Atomic (presented strictly one at a time, never bundled or bulk-queried).
2. Optionated (concrete, mutually exclusive choices).
3. Confidence-scored (calibrated confidence score/tier assigned to each option).
4. Persistently stored (operator answers recorded in governed decision ledger).
5. Confidence-calibrated (historical answers inform future option confidence).
6. Ambiguity-gated (surfaced only when confidence margin between top two options < 15%).

Architectural Facade Module: Delegates to modularized components in
dual_pipeline.hitl_governance_core and dual_pipeline.storage.hitl_sqlite_store.
"""
from __future__ import annotations

from dual_pipeline.hitl_governance_core import (
    CONFIDENCE_TIER_SCORES,
    DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_DIVERGENCE_THRESHOLD,
    DEFAULT_ENTROPY_THRESHOLD,
    DEFAULT_MARGIN_THRESHOLD,
    DEFAULT_SURFACING_MODE,
    LEGACY_MARGIN_THRESHOLD,
    REQUIRED_HITL_PHRASES,
    SYNTHETIC_APPROVAL_MARKERS,
    CalibratedScore,
    ConfidenceResult,
    HitlConfidenceMissingError,
    HITLDecisionStore,
    HitlSurfacingMode,
    _extract_percentage,
    _extract_tier,
    _match_keys,
    _normalize_key_tokens,
    _option_key,
    _parse_iso_timestamp,
    _standardize_option,
    calculate_normalized_entropy,
    compute_bayesian_calibrated_score,
    compute_beta_credible_interval,
    evaluate_hitl_surfacing_gate,
    parse_confidence_score,
    resolve_hitl_ledger_path,
    validate_approval_origin,
    validate_hitl_governance_controls,
    validate_hitl_option,
    validate_hitl_presentation,
)
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore

__all__ = [
    "CONFIDENCE_TIER_SCORES",
    "CalibratedScore",
    "ConfidenceResult",
    "DEFAULT_CONFIDENCE_FLOOR",
    "DEFAULT_DIVERGENCE_THRESHOLD",
    "DEFAULT_ENTROPY_THRESHOLD",
    "DEFAULT_MARGIN_THRESHOLD",
    "DEFAULT_SURFACING_MODE",
    "HITLDecisionStore",
    "HITLSQLiteStore",
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
    "parse_confidence_score",
    "resolve_hitl_ledger_path",
    "validate_approval_origin",
    "validate_hitl_governance_controls",
    "validate_hitl_option",
    "validate_hitl_presentation",
]
