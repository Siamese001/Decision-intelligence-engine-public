"""Dual-Pipeline Layer 0 Package Facade.

Central entry point for all Layer 0 contracts, primitives, validation,
provenance, quarantine, and receipt utilities.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

from dual_pipeline.l0.authoritative_exit_binding import (
    CAPABILITY_REQUIREMENTS,
    AuthoritativeExitError,
    ExitCapability,
    ExitEvidenceRequirement,
    validate_copilot_exit_parity,
    validate_exit_evidence,
    verify_active_pair_and_coverage,
)
from dual_pipeline.l0.recurrence_prevention import (
    CanaryEvaluationResult,
    MutationCanaryTarget,
    RecurrenceViolationError,
    RollbackResult,
    TreeIdentityVerification,
    execute_governed_rollback,
    run_boundary_mutation_canary,
    verify_ci_hook_parity,
)
from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    AdvisoryCategory,
    CanonicalGraphDelta,
    CanonicalGraphNode,
    ClaimCheck,
    ConfidenceTier,
    ContentDigest,
    EvidencePack,
    EvidenceRecord,
    EvidenceSpan,
    ExecutiveRecap,
    FastPathContext,
    GraphEdge,
    HandoffEnvelope,
    InternalAdvisoryNode,
    MutationRecord,
    Namespace,
    OperationType,
    PolicyContext,
    QuarantineProfile,
    QuarantineReason,
    RuntimeReceipt,
    TerminalState,
    ValidationResult,
)
from dual_pipeline.l0.evaluation_types import (
    CompositeEvaluationVerdict,
    EvaluationModality,
    EvaluationVerdict,
    FailureCategory,
    HeuristicAssessmentResult,
    SemanticAssessmentResult,
    VectorAssessmentResult,
    check_live_to_mock_fallback,
    validate_claim_grounding,
    validate_evaluator_payload,
)
from dual_pipeline.l0.policy import (
    PolicyEnforcementOrderTracker,
    authorize_namespace_access,
    filter_candidates_pre_retrieval,
    is_candidate_eligible,
)
from dual_pipeline.l0.policy_snapshot import (
    ActiveRunPolicyRegistry,
    GLOBAL_POLICY_REGISTRY,
    PolicySnapshot,
    create_policy_snapshot,
    verify_policy_snapshot_integrity,
)
from dual_pipeline.l0.output_spec import (
    ALL_OUTPUTS,
    CANONICAL_OUTPUT_MAP,
    parse_regenerate_spec,
)
from dual_pipeline.l0.prompt_contract import (
    CANONICAL_OUTPUT1_PROMPT_CONTRACT,
    CANONICAL_OUTPUT2_PROMPT_CONTRACT,
    CANONICAL_OUTPUT3_PROMPT_CONTRACT,
    CANONICAL_PROMPT_CONTRACTS,
    PromptContract,
    canonical_obligation_digest,
    compute_assembled_bundle_fingerprint,
    detect_contract_mutation,
    validate_owner_string,
    verify_contract_parity,
)
from dual_pipeline.l0.provenance import (
    canonical_json_bytes,
    chain_parent_digests,
    compute_content_digest,
    mint_claim_check,
    verify_digest_integrity,
)
from dual_pipeline.l0.quarantine import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_EVIDENCE_QUALITY_THRESHOLD,
    DEFAULT_STRATEGIC_THRESHOLD,
    DEFAULT_TACTICALITY_CEILING,
    apply_quarantine_to_dict,
    evaluate_quarantine_profile,
)
from dual_pipeline.l0.receipts import (
    create_runtime_receipt,
    create_sequential_lock,
    verify_predecessor_lock,
    verify_runtime_receipt,
    verify_sequential_lock,
    write_immutable_receipt,
)
from dual_pipeline.l0.runtime_gate_preservation import (
    CallbackFence,
    CallbackFencedError,
    CacheDependencyBinding,
    ExhaustedRepairError,
    InvalidCacheDependencyError,
    ReplanRequiredError,
    ReplanStatus,
    compute_context_hash,
    compute_roster_hash,
    validate_cache_reuse_dependencies,
    validate_fact_mutation_requires_rerun,
    validate_healing_outcome,
    validate_replan_clearance,
)
from dual_pipeline.l0.validation import (
    validate_canonical_graph_delta,
    validate_evidence_record,
    validate_fast_path_context,
    validate_handoff_envelope,
    validate_monotonic_state_transition,
)
from dual_pipeline.l0.request_context import (
    ActionRole,
    BudgetExhaustionError,
    DeadlineExceededError,
    OptionalWorkDisabledError,
    RequestContext,
    ReservationError,
    ReservationLease,
    ReservationState,
    get_clock_epoch_id,
    verify_clock_continuity,
)
from dual_pipeline.l0.reservation_manager import (
    BudgetReservationManager,
    get_reservation_manager,
)
from dual_pipeline.l0.decision_support_contracts import (
    DecisionSupportObligation,
    DecisionSupportState,
    compile_frame_evidence_requirements,
    evaluate_evidence_support,
)
from dual_pipeline.l0.wal_evidence import (
    append_evidence_record,
    append_mutation_record,
    get_evidence_record_by_digest,
    get_governed_wal_connection,
    init_evidence_wal_db,
)

_CONTRACT_EXPORTS = (
    # Contracts & Enums
    "CLIENT_SAFE_NAMESPACE",
    "INTERNAL_ADVISORY_NAMESPACE",
    "Namespace",
    "AdvisoryCategory",
    "QuarantineReason",
    "TerminalState",
    "ConfidenceTier",
    "OperationType",
    "ContentDigest",
    "ClaimCheck",
    "EvidenceSpan",
    "EvidenceRecord",
    "CanonicalGraphNode",
    "InternalAdvisoryNode",
    "GraphEdge",
    "CanonicalGraphDelta",
    "ExecutiveRecap",
    "MutationRecord",
    "ValidationResult",
    "PolicyContext",
    "QuarantineProfile",
    "FastPathContext",
    "HandoffEnvelope",
    "EvidencePack",
    "RuntimeReceipt",
    # Output Deliverable Specifications
    "ALL_OUTPUTS",
    "CANONICAL_OUTPUT_MAP",
    "parse_regenerate_spec",
    # Provenance
    "canonical_json_bytes",
    "compute_content_digest",
    "mint_claim_check",
    "verify_digest_integrity",
    "chain_parent_digests",
    # Validation
    "validate_evidence_record",
    "validate_fast_path_context",
    "validate_canonical_graph_delta",
    "validate_handoff_envelope",
    "validate_monotonic_state_transition",
    # Request Context & Budget Reservations
    "ActionRole",
    "BudgetExhaustionError",
    "DeadlineExceededError",
    "OptionalWorkDisabledError",
    "RequestContext",
    "ReservationError",
    "ReservationLease",
    "ReservationState",
    "get_clock_epoch_id",
    "verify_clock_continuity",
    "BudgetReservationManager",
    "get_reservation_manager",
    # Decision Support & Frame Obligations
    "DecisionSupportObligation",
    "DecisionSupportState",
    "compile_frame_evidence_requirements",
    "evaluate_evidence_support",
)

_POLICY_AND_EVAL_EXPORTS = (
    # Policy & Snapshots
    "authorize_namespace_access",
    "is_candidate_eligible",
    "filter_candidates_pre_retrieval",
    "PolicyEnforcementOrderTracker",
    "PolicySnapshot",
    "create_policy_snapshot",
    "verify_policy_snapshot_integrity",
    "ActiveRunPolicyRegistry",
    "GLOBAL_POLICY_REGISTRY",
    # Prompt Contracts
    "PromptContract",
    "CANONICAL_OUTPUT1_PROMPT_CONTRACT",
    "CANONICAL_OUTPUT2_PROMPT_CONTRACT",
    "CANONICAL_OUTPUT3_PROMPT_CONTRACT",
    "CANONICAL_PROMPT_CONTRACTS",
    "canonical_obligation_digest",
    "compute_assembled_bundle_fingerprint",
    "validate_owner_string",
    "detect_contract_mutation",
    "verify_contract_parity",
    # Evaluation Results & Envelopes
    "EvaluationModality",
    "EvaluationVerdict",
    "FailureCategory",
    "HeuristicAssessmentResult",
    "VectorAssessmentResult",
    "SemanticAssessmentResult",
    "CompositeEvaluationVerdict",
    "validate_evaluator_payload",
    "validate_claim_grounding",
    "check_live_to_mock_fallback",
    # Quarantine
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_STRATEGIC_THRESHOLD",
    "DEFAULT_EVIDENCE_QUALITY_THRESHOLD",
    "DEFAULT_TACTICALITY_CEILING",
    "evaluate_quarantine_profile",
    "apply_quarantine_to_dict",
)

_RUNTIME_AND_EXIT_EXPORTS = (
    # Receipts
    "create_runtime_receipt",
    "write_immutable_receipt",
    "create_sequential_lock",
    "verify_sequential_lock",
    "verify_predecessor_lock",
    "verify_runtime_receipt",
    # Runtime Gate Preservation & Reuse
    "CallbackFence",
    "CallbackFencedError",
    "CacheDependencyBinding",
    "ExhaustedRepairError",
    "InvalidCacheDependencyError",
    "ReplanRequiredError",
    "ReplanStatus",
    "compute_context_hash",
    "compute_roster_hash",
    "validate_cache_reuse_dependencies",
    "validate_fact_mutation_requires_rerun",
    "validate_healing_outcome",
    "validate_replan_clearance",
    # WAL Storage
    "get_governed_wal_connection",
    "init_evidence_wal_db",
    "append_evidence_record",
    "append_mutation_record",
    "get_evidence_record_by_digest",
    # Authoritative Exit Evidence Binding
    "ExitCapability",
    "AuthoritativeExitError",
    "ExitEvidenceRequirement",
    "CAPABILITY_REQUIREMENTS",
    "validate_exit_evidence",
    "verify_active_pair_and_coverage",
    "validate_copilot_exit_parity",
    # Recurrence Prevention & Rollback Invariants
    "CanaryEvaluationResult",
    "MutationCanaryTarget",
    "RecurrenceViolationError",
    "RollbackResult",
    "TreeIdentityVerification",
    "execute_governed_rollback",
    "run_boundary_mutation_canary",
    "verify_ci_hook_parity",
)

__all__ = list(_CONTRACT_EXPORTS + _POLICY_AND_EVAL_EXPORTS + _RUNTIME_AND_EXIT_EXPORTS)
