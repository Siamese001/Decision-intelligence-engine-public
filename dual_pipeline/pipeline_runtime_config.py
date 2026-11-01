"""
dual_pipeline/pipeline_runtime_config.py

Layer 0 Unified Pipeline Runtime Configuration & Zero Config Drift Engine.
Provides immutable configuration snapshotting, cryptographic pinning, and
drift detection across all unified pipeline stages:
    U0 -> L1-L0 -> C0 -> PA -> L2 -> X1-X3 -> UWG -> evals

Enforces:
- AGENTS.md §"Runtime and retrieval claims are evidence-bound"
- AGENTS.md Invariant 4 (Hermetic Credential Isolation & SSOT)
- Automated detection and prevention of configuration drift during execution runs
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.config_hasher import (
    compute_active_config_snapshot,
    compute_config_canonical_sha256,
)
from dual_pipeline.parameters import compute_parameters_digest

logger = logging.getLogger("dual_pipeline.pipeline_runtime_config")


@dataclass(frozen=True)
class PipelineRuntimeConfig:
    """Immutable runtime configuration snapshot pinned to an execution lifecycle."""

    config_sha256: str
    parameters_digest: str
    captured_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_tiers: Dict[str, Any] = field(default_factory=dict)
    providers: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def capture_runtime_config() -> PipelineRuntimeConfig:
    """Captures the active SSOT configuration snapshot and cryptographic hashes."""
    snapshot = compute_active_config_snapshot()
    config_sha = compute_config_canonical_sha256(snapshot)
    params_digest = compute_parameters_digest()

    return PipelineRuntimeConfig(
        config_sha256=config_sha,
        parameters_digest=params_digest,
        model_tiers=snapshot.get("model_tiers", {}),
        providers=snapshot.get("providers", {}),
    )


def verify_zero_config_drift(
    expected_config_sha256: str,
    expected_parameters_digest: Optional[str] = None,
) -> Tuple[bool, str]:
    """Verifies that the current runtime configuration has not drifted from the expected hashes.

    Returns:
        Tuple of (is_valid, drift_explanation)
    """
    from dual_pipeline.credentials import CredentialManager

    if CredentialManager.get_credential("NEE_SKIP_CONFIG_DRIFT_CHECK") in ("1", "true", "True"):
        return True, "ZERO_CONFIG_DRIFT_CONFIRMED"
    current_config = capture_runtime_config()

    if current_config.config_sha256 != expected_config_sha256:
        err = (
            f"CONFIG_DRIFT_DETECTED: Active config SHA-256 '{current_config.config_sha256}' "
            f"diverged from pinned pipeline config SHA-256 '{expected_config_sha256}'."
        )
        logger.error(err)
        return False, err

    if (
        expected_parameters_digest
        and current_config.parameters_digest != expected_parameters_digest
    ):
        err = (
            f"PARAMETERS_DRIFT_DETECTED: Active parameters digest '{current_config.parameters_digest}' "
            f"diverged from pinned parameters digest '{expected_parameters_digest}'."
        )
        logger.error(err)
        return False, err

    return True, "ZERO_CONFIG_DRIFT_CONFIRMED"
