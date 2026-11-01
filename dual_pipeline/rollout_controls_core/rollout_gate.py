"""dual_pipeline.rollout_controls_core.rollout_gate

Rollout control gate enforcing staged deployment, predecessor lock verification,
and operator authority for Wave 12.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, Optional, Sequence, Set

from dual_pipeline.core import utc_now

logger = logging.getLogger(__name__)

REQUIRED_PREDECESSOR_WAVES: Set[str] = frozenset(
    f"wave_{i}.complete" for i in range(1, 12)
)


class RolloutTier(str, Enum):
    """Governed rollout progression tiers."""
    DISABLED = "DISABLED"
    ISOLATED_QUALIFICATION = "ISOLATED_QUALIFICATION"
    NARROW_OPERATOR_PILOT = "NARROW_OPERATOR_PILOT"
    BROAD_APPROVED_COHORT = "BROAD_APPROVED_COHORT"


@dataclass(frozen=True)
class RolloutDecision:
    """Immutable evaluation decision for staged rollout progression."""
    surface_id: str
    current_tier: RolloutTier
    target_tier: RolloutTier
    approved: bool
    rationale: str
    predecessor_locks_verified: bool
    operator_authorization_verified: bool
    details: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = field(default_factory=utc_now)


class RolloutGate:
    """Manages staged rollout promotion and fail-closed rollback authority."""

    def __init__(self) -> None:
        self._surface_tiers: Dict[str, RolloutTier] = {}

    def get_surface_tier(self, surface_id: str) -> RolloutTier:
        """Returns active rollout tier for a given operational surface."""
        return self._surface_tiers.get(surface_id, RolloutTier.ISOLATED_QUALIFICATION)

    def evaluate_promotion(
        self,
        surface_id: str,
        current_tier: RolloutTier,
        target_tier: RolloutTier,
        predecessor_locks: Sequence[str],
        operator_approved: bool = False,
        guardrail_report: Optional[Dict[str, Any]] = None,
    ) -> RolloutDecision:
        """Evaluates whether an operational surface is authorized to advance to target tier."""
        lock_set = set(predecessor_locks)
        missing_locks = REQUIRED_PREDECESSOR_WAVES - lock_set

        # Invariant 1: All 11 predecessor wave locks must be verified
        if missing_locks:
            return RolloutDecision(
                surface_id=surface_id,
                current_tier=current_tier,
                target_tier=target_tier,
                approved=False,
                rationale=f"Missing required predecessor locks: {sorted(list(missing_locks))}",
                predecessor_locks_verified=False,
                operator_authorization_verified=operator_approved,
            )

        # Invariant 2: Production/pilot tiers require explicit operator approval
        needs_approval = target_tier in (
            RolloutTier.NARROW_OPERATOR_PILOT,
            RolloutTier.BROAD_APPROVED_COHORT,
        )
        if needs_approval and not operator_approved:
            return RolloutDecision(
                surface_id=surface_id,
                current_tier=current_tier,
                target_tier=target_tier,
                approved=False,
                rationale="AUTHORIZATION_REQUIRED: Advancement to pilot or broad cohort requires human operator approval.",
                predecessor_locks_verified=True,
                operator_authorization_verified=False,
            )

        # Invariant 3: Guardrail violations block rollout
        if guardrail_report and not guardrail_report.get("is_compliant", True):
            violations = guardrail_report.get("violations", ["Unspecified guardrail violation"])
            return RolloutDecision(
                surface_id=surface_id,
                current_tier=current_tier,
                target_tier=target_tier,
                approved=False,
                rationale=f"GUARDRAIL_VIOLATIONS: {'; '.join(violations)}",
                predecessor_locks_verified=True,
                operator_authorization_verified=operator_approved,
                details={"violations": violations},
            )

        # Advancement approved
        self._surface_tiers[surface_id] = target_tier
        return RolloutDecision(
            surface_id=surface_id,
            current_tier=current_tier,
            target_tier=target_tier,
            approved=True,
            rationale=f"Surface '{surface_id}' authorized for {target_tier.value}.",
            predecessor_locks_verified=True,
            operator_authorization_verified=operator_approved,
        )

    def rollback_to_tier(
        self,
        surface_id: str,
        target_tier: RolloutTier,
        reason: str,
    ) -> RolloutDecision:
        """Executes instant, fail-closed rollback to a lower rollout tier."""
        curr = self.get_surface_tier(surface_id)
        self._surface_tiers[surface_id] = target_tier
        logger.warning("ROLLBACK_EXECUTED for %s: %s -> %s (Reason: %s)", surface_id, curr.value, target_tier.value, reason)
        return RolloutDecision(
            surface_id=surface_id,
            current_tier=curr,
            target_tier=target_tier,
            approved=True,
            rationale=f"ROLLBACK: {reason}",
            predecessor_locks_verified=True,
            operator_authorization_verified=True,
            details={"is_rollback": True},
        )
