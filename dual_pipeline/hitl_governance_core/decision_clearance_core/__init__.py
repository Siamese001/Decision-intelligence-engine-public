"""
dual_pipeline/hitl_governance_core/decision_clearance_core/__init__.py

Public package interface for decision_clearance_core.
"""
from __future__ import annotations

from .models import (
    ClearanceAuthority,
    ClearanceResult,
    ClearanceVerificationRequest,
    ClearanceVerificationResult,
    NoDecisionAttestation,
    PreWaveDecision,
    PreWaveGateResult,
    ScopedDecision,
    EXIT_CODE_CLEARED,
    EXIT_CODE_NO_DECISIONS,
    EXIT_CODE_OPERATIONAL_ERROR,
    EXIT_CODE_OPERATOR_INPUT_REQUIRED,
)
from .legacy_adapter import (
    evaluate_legacy_clearance,
    normalize_legacy_record,
)
from .plan_parser import (
    discover_implementation_plans,
    parse_confidence_pairs,
    parse_decisions_from_markdown,
    parse_decisions_from_table,
    parse_no_decision_attestations,
    validate_decision_collection,
)
from .verifier import (
    is_decision_ratified_in_ledger,
    verify_decision_clearance,
    verify_decision_ratification,
)

__all__ = [
    "ClearanceAuthority",
    "ClearanceResult",
    "ClearanceVerificationRequest",
    "ClearanceVerificationResult",
    "NoDecisionAttestation",
    "PreWaveDecision",
    "PreWaveGateResult",
    "ScopedDecision",
    "EXIT_CODE_CLEARED",
    "EXIT_CODE_NO_DECISIONS",
    "EXIT_CODE_OPERATIONAL_ERROR",
    "EXIT_CODE_OPERATOR_INPUT_REQUIRED",
    "discover_implementation_plans",
    "evaluate_legacy_clearance",
    "is_decision_ratified_in_ledger",
    "normalize_legacy_record",
    "parse_confidence_pairs",
    "parse_decisions_from_markdown",
    "parse_decisions_from_table",
    "parse_no_decision_attestations",
    "validate_decision_collection",
    "verify_decision_clearance",
    "verify_decision_ratification",
]
