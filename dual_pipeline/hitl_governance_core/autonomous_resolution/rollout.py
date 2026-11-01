"""
dual_pipeline/hitl_governance_core/autonomous_resolution/rollout.py

Governed feature configuration, activation bounds, observe-only shadow mode,
and operational kill-switch enforcement for autonomous decision resolution.

Guarantees:
- Default state is strictly DISABLED (fail-closed).
- Observe-only (shadow) mode evaluates without mutating store, returning executable choices,
  or suppressing human questions.
- Activation is strictly bounded by approved operation classes, runtime roots, and policy digest.
- Kill-switch immediately terminates active resolutions and pending authority consumption.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

from config.credentials import CredentialManager
from dual_pipeline.parameters import (
    get_bool_parameter,
    get_str_parameter,
)

from .models import DecisionMode, DecisionRequest, PolicySnapshot


class RolloutMode(str, Enum):
    """Governed rollout mode for autonomous decision resolution."""
    DISABLED = "DISABLED"
    OBSERVE_ONLY = "OBSERVE_ONLY"
    ENABLED = "ENABLED"


DEFAULT_APPROVED_CLASSES: frozenset[str] = frozenset({
    "routine_ambiguity",
    "entity_disambiguation",
    "classification",
    "general_disambiguation",
    "speaker_hint",
})

FORBIDDEN_OPERATION_CLASSES: frozenset[str] = frozenset({
    "architectural",
    "destructive",
    "policy",
    "governance",
    "release",
    "tier0",
    "tier1",
    "learning_proposal",
    "graph_mutation",
    "attribution_remediation",
})


@dataclass(frozen=True)
class RolloutConfiguration:
    """Explicit parameters governing feature rollout and boundaries."""
    mode: RolloutMode = RolloutMode.DISABLED
    kill_switch: bool = False
    approved_operation_classes: frozenset[str] = DEFAULT_APPROVED_CLASSES
    allowed_runtime_roots: Optional[frozenset[str]] = None
    required_policy_digest: Optional[str] = None
    allow_autonomous_consumption: bool = False


def get_rollout_mode() -> RolloutMode:
    """Returns the active rollout mode from SSOT parameters or environment.

    Default is strictly DISABLED (fail-closed, default off).
    """
    raw_env = CredentialManager.get_credential("NEE_PARAM_HITL_AUTONOMOUS_ROLLOUT_MODE")
    if raw_env:
        try:
            return RolloutMode(raw_env.strip().upper())
        except ValueError:
            return RolloutMode.DISABLED

    param_val = get_str_parameter("hitl.autonomous_rollout_mode", "DISABLED")
    try:
        return RolloutMode(param_val.strip().upper())
    except ValueError:
        return RolloutMode.DISABLED


def is_kill_switch_active() -> bool:
    """Returns True if the autonomous resolution kill-switch is engaged."""
    env_val = CredentialManager.get_credential("NEE_PARAM_HITL_AUTONOMOUS_KILL_SWITCH")
    if env_val is not None:
        return env_val.strip().lower() in ("true", "1", "yes", "on")
    return get_bool_parameter("hitl.autonomous_kill_switch", False)


def is_autonomous_rollout_enabled() -> bool:
    """Returns True iff the feature is explicitly ENABLED and kill-switch is NOT engaged."""
    if is_kill_switch_active():
        return False
    return get_rollout_mode() == RolloutMode.ENABLED


def is_autonomous_consumption_allowed() -> bool:
    """Returns True if autonomous clearance authority is allowed to be consumed by execution."""
    return is_autonomous_rollout_enabled()


def _is_operation_class_approved(
    request: DecisionRequest,
    approved_classes: frozenset[str],
) -> Tuple[bool, str]:
    """Validates that request operation class and category are permitted."""
    if request.mode in (
        DecisionMode.ARCHITECTURAL,
        DecisionMode.DESTRUCTIVE,
        DecisionMode.POLICY,
        DecisionMode.GOVERNANCE,
    ):
        return False, f"DecisionMode '{request.mode.value}' is prohibited from autonomous lane."

    cat = (request.category or "").lower().strip()
    if cat in FORBIDDEN_OPERATION_CLASSES:
        return False, f"Category '{cat}' is in forbidden operation classes."

    target = (request.scope.action_target or "").lower().strip()
    if any(f in target for f in FORBIDDEN_OPERATION_CLASSES):
        return False, f"Action target '{target}' contains forbidden operation token."

    # Must match at least one approved class or category
    if cat in approved_classes or target in approved_classes:
        return True, "Approved operation class."

    # Check request context tags
    ctx = getattr(request, "context", {}) or {}
    op_class = str(ctx.get("operation_class", "")).lower().strip()
    if op_class and op_class in approved_classes:
        return True, "Approved operation class from context."

    # If category is routine / general ambiguity
    if "ambiguity" in cat or "disambiguation" in cat or "routine" in cat:
        return True, "Routine ambiguity category permitted."

    return False, f"Category '{cat}' not in approved operation classes."


def _is_runtime_root_allowed(
    runtime_root: Optional[Path],
    allowed_roots: Optional[frozenset[str]],
) -> Tuple[bool, str]:
    """Validates that execution runtime root falls within approved path prefixes."""
    if not allowed_roots:
        return True, "No runtime root restriction active."
    if runtime_root is None:
        return False, "Runtime root required when boundary restrictions are active."

    resolved_rt = Path(runtime_root).resolve()
    for root_str in allowed_roots:
        allowed_path = Path(root_str).resolve()
        try:
            resolved_rt.relative_to(allowed_path)
            return True, f"Runtime root '{resolved_rt}' within allowed root '{allowed_path}'."
        except ValueError:
            continue

    return False, f"Runtime root '{resolved_rt}' not within any authorized root."


def evaluate_rollout_authorization(
    request: DecisionRequest,
    policy_snapshot: PolicySnapshot,
    runtime_root: Optional[Path] = None,
    config: Optional[RolloutConfiguration] = None,
) -> Tuple[bool, str, RolloutMode]:
    """Evaluates whether an autonomous decision resolution is authorized to proceed.

    Returns:
        (is_authorized, reason_code, effective_mode)
    """
    mode = config.mode if config else get_rollout_mode()
    kill_switch = config.kill_switch if config else is_kill_switch_active()

    if kill_switch:
        return False, "KILL_SWITCH_ACTIVE", RolloutMode.DISABLED

    if mode == RolloutMode.DISABLED:
        return False, "ROLLOUT_DISABLED", RolloutMode.DISABLED

    if mode == RolloutMode.OBSERVE_ONLY:
        return False, "OBSERVE_ONLY_MODE", RolloutMode.OBSERVE_ONLY

    # Mode is ENABLED: evaluate operational boundaries
    approved_classes = (
        config.approved_operation_classes if config else DEFAULT_APPROVED_CLASSES
    )
    class_ok, class_msg = _is_operation_class_approved(request, approved_classes)
    if not class_ok:
        return False, f"OPERATION_CLASS_NOT_APPROVED: {class_msg}", RolloutMode.DISABLED

    allowed_roots = config.allowed_runtime_roots if config else None
    root_ok, root_msg = _is_runtime_root_allowed(runtime_root, allowed_roots)
    if not root_ok:
        return False, f"RUNTIME_ROOT_NOT_ALLOWED: {root_msg}", RolloutMode.DISABLED

    req_digest = config.required_policy_digest if config else None
    if req_digest and policy_snapshot.policy_digest != req_digest:
        return False, (
            f"POLICY_DIGEST_MISMATCH: Snapshot digest '{policy_snapshot.policy_digest[:16]}' "
            f"does not match approved digest '{req_digest[:16]}'."
        ), RolloutMode.DISABLED

    return True, "AUTHORIZED", RolloutMode.ENABLED
