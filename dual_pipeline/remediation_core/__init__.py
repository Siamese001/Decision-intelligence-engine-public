"""
dual_pipeline/remediation_core/__init__.py

Package exports for Selective Remediation & Retry State Machine.
"""

from .artifact_validator import (
    ALL_OUTPUTS,
    CANONICAL_OUTPUT_MAP,
    FrozenArtifactValidationResult,
    parse_regenerate_spec,
    validate_frozen_artifact_gate,
)
from .diff_injector import RemediationDiffInjector
from .monotonic_governor import BestCandidateLedger, CandidateRecord
from .state_machine import (
    OUTPUT2_PRODUCTION_PASS_THRESHOLD,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
    PRODUCTION_PASS_THRESHOLD,
    RemediationStateMachine,
    SelectiveRemediationStateMachine,
)

__all__ = [
    "ALL_OUTPUTS",
    "BestCandidateLedger",
    "CANONICAL_OUTPUT_MAP",
    "CandidateRecord",
    "FrozenArtifactValidationResult",
    "RemediationDiffInjector",
    "PRODUCTION_PASS_THRESHOLD",
    "OUTPUT2_PRODUCTION_PASS_THRESHOLD",
    "OUTPUT3_PRODUCTION_PASS_THRESHOLD",
    "RemediationStateMachine",
    "SelectiveRemediationStateMachine",
    "parse_regenerate_spec",
    "validate_frozen_artifact_gate",
    "persist_stage_checkpoint_seal",
    "verify_stage_checkpoint_seal",
]

from .stage_sealer import (
    persist_stage_checkpoint_seal,
    verify_stage_checkpoint_seal,
)

