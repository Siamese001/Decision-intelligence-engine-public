"""
dual_pipeline/hitl_governance_core/autonomous_resolution/__init__.py

Governed Autonomous Decision Resolution Package.
Exports contracts, models, policy snapshot loaders, deterministic eligibility evaluators,
Ed25519 signing utilities, append-only SQLite storage, and JSON ledger projections.
"""
from .models import (
    AuthorityType,
    AutonomousResolutionRecord,
    DecisionMode,
    DecisionOption,
    DecisionRequest,
    DecisionScope,
    EligibilityEvaluationResult,
    PolicySnapshot,
    ResolutionTerminalResult,
)
from .policy import (
    compute_policy_digest,
    load_policy_snapshot,
    validate_policy_snapshot,
)
from .eligibility import (
    evaluate_decision_eligibility,
)
from .signing import (
    canonical_json_bytes,
    compute_canonical_digest,
    export_private_key_pem,
    export_public_key_pem,
    generate_autonomous_keypair,
    load_private_key_pem,
    load_public_key_pem,
    sign_autonomous_payload,
    verify_autonomous_signature,
)
from .storage import (
    AutonomousResolutionStore,
    ConflictResolutionError,
    compute_idempotency_key,
)
from .projection import (
    export_projection,
    verify_projection_integrity,
)
from .service import (
    AutonomousDecisionResult,
    resolve_governed_decision,
)
from .rollout import (
    RolloutConfiguration,
    RolloutMode,
    evaluate_rollout_authorization,
    get_rollout_mode,
    is_autonomous_consumption_allowed,
    is_autonomous_rollout_enabled,
    is_kill_switch_active,
)

__all__ = [
    "AuthorityType",
    "AutonomousDecisionResult",
    "AutonomousResolutionRecord",
    "AutonomousResolutionStore",
    "ConflictResolutionError",
    "DecisionMode",
    "DecisionOption",
    "DecisionRequest",
    "DecisionScope",
    "EligibilityEvaluationResult",
    "PolicySnapshot",
    "ResolutionTerminalResult",
    "canonical_json_bytes",
    "compute_canonical_digest",
    "compute_idempotency_key",
    "compute_policy_digest",
    "evaluate_decision_eligibility",
    "export_private_key_pem",
    "export_projection",
    "export_public_key_pem",
    "generate_autonomous_keypair",
    "load_policy_snapshot",
    "load_private_key_pem",
    "load_public_key_pem",
    "resolve_governed_decision",
    "sign_autonomous_payload",
    "validate_policy_snapshot",
    "verify_autonomous_signature",
    "verify_projection_integrity",
    "RolloutConfiguration",
    "RolloutMode",
    "evaluate_rollout_authorization",
    "get_rollout_mode",
    "is_autonomous_consumption_allowed",
    "is_autonomous_rollout_enabled",
    "is_kill_switch_active",
]
