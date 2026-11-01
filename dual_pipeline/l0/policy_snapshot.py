"""Dual-Pipeline Layer 0: Versioned Policy Authority & Immutable Run Snapshots.

SSOT policy snapshotting engine enforcing Law 6 (Policy Authority & Invariance).
Provides:
1. Versioned policy snapshots with named, justified domain exceptions.
2. Immutable run binding: Current active runs retain their bound snapshot; mid-run mutation fails closed.
3. Decision agreement: Boundary (spread <= threshold), single-candidate, and invalid-value cases agree.
4. Cryptographic RFC-8259 content digest sealing.

Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import decimal
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


def _canonical_json_bytes(data: Any) -> bytes:
    """Serializes arbitrary Python dicts/lists into RFC-8259 canonical JSON bytes."""
    return json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _compute_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_divergence_margin_threshold,
)

# Canonical SSOT Defaults
CANONICAL_POLICY_VERSION: str = "1.1.0"
CANONICAL_AMBIGUITY_MARGIN: float = get_hitl_ambiguity_margin_threshold()
CANONICAL_BOUNDARY_INCLUSIVITY: str = "<="
CANONICAL_CONFIDENCE_FLOOR: float = 0.65
CANONICAL_ENTROPY_CEILING: float = 0.75
CANONICAL_DIVERGENCE_MARGIN: float = get_hitl_divergence_margin_threshold()

CANONICAL_DOMAIN_EXCEPTIONS: Dict[str, Dict[str, Any]] = {
    "entity_resolution": {
        "parameter_name": "hitl.unresolved_entity_margin_threshold",
        "threshold": 0.15,
        "boundary_inclusivity": "<=",
        "justification": "Entity extraction candidates operate on a narrower 0.15 margin to avoid over-prompting on high-volume name aliases.",
    },
    "layer_0_quarantine": {
        "parameter_name": "l0_quarantine_admission_threshold",
        "threshold": 0.60,
        "boundary_inclusivity": ">=",
        "justification": "Layer 0 quarantine admission uses DEFAULT_CONFIDENCE_THRESHOLD = 0.60 to quarantine low-signal assertions.",
    },
    "hitl_surfacing": {
        "parameter_name": "medium_tier_confidence_score",
        "threshold": 0.60,
        "boundary_inclusivity": "==",
        "justification": "Pre-wave and HITL surfacing assign 0.60 as nominal representative score for the MEDIUM confidence tier.",
    },
}


@dataclass(frozen=True)
class PolicySnapshot:
    """Immutable snapshot of governing policy authority for a pipeline run."""

    policy_version: str
    snapshot_id: str
    created_at: float
    ambiguity_margin: float = field(default_factory=get_hitl_ambiguity_margin_threshold)
    boundary_inclusivity: str = CANONICAL_BOUNDARY_INCLUSIVITY
    confidence_floor: float = CANONICAL_CONFIDENCE_FLOOR
    entropy_ceiling: float = CANONICAL_ENTROPY_CEILING
    divergence_margin: float = field(default_factory=get_hitl_divergence_margin_threshold)
    named_domain_exceptions: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: dict(CANONICAL_DOMAIN_EXCEPTIONS)
    )
    snapshot_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    def evaluate_ambiguity(
        self,
        top_confidence: Any,
        second_confidence: Any,
        domain: Optional[str] = None,
    ) -> bool:
        """Determines whether a confidence spread mandates Human-in-the-Loop review.

        Rules:
        - If top_confidence is None or invalid: True (fail-closed).
        - If second_confidence is None: False (single-candidate resolves autonomously).
        - If domain has a registered named exception, uses exception threshold.
        - Boundary spread == threshold returns True under '<=' inclusivity.
        """
        if top_confidence is None:
            return True
        if second_confidence is None:
            return False

        try:
            t_f = float(top_confidence)
            s_f = float(second_confidence)
        except (ValueError, TypeError):
            return True

        if math.isnan(t_f) or math.isnan(s_f) or math.isinf(t_f) or math.isinf(s_f):
            return True
        if t_f < 0.0 or s_f < 0.0:
            return True

        # Resolve threshold (domain override or canonical ambiguity margin)
        active_thresh = self.ambiguity_margin
        if domain and domain in self.named_domain_exceptions:
            exc = self.named_domain_exceptions[domain]
            if "threshold" in exc:
                active_thresh = float(exc["threshold"])

        try:
            dt = Decimal(str(round(t_f, 8)))
            ds = Decimal(str(round(s_f, 8)))
            d_thresh = Decimal(str(round(active_thresh, 8)))
            gap = abs(dt - ds)

            if self.boundary_inclusivity == "<=":
                return gap <= d_thresh
            return gap < d_thresh
        except (InvalidOperation, Exception):
            return True


def create_policy_snapshot(
    *,
    policy_version: str = CANONICAL_POLICY_VERSION,
    ambiguity_margin: Optional[float] = None,
    boundary_inclusivity: str = CANONICAL_BOUNDARY_INCLUSIVITY,
    confidence_floor: float = CANONICAL_CONFIDENCE_FLOOR,
    entropy_ceiling: float = CANONICAL_ENTROPY_CEILING,
    divergence_margin: Optional[float] = None,
    named_domain_exceptions: Optional[Dict[str, Dict[str, Any]]] = None,
    snapshot_id: Optional[str] = None,
    timestamp: Optional[float] = None,
) -> PolicySnapshot:
    """Constructs, seals, and returns an immutable PolicySnapshot."""
    eff_ambiguity_margin = (
        ambiguity_margin
        if ambiguity_margin is not None
        else get_hitl_ambiguity_margin_threshold()
    )
    eff_divergence_margin = (
        divergence_margin
        if divergence_margin is not None
        else get_hitl_divergence_margin_threshold()
    )
    ts = timestamp if timestamp is not None else time.time()
    sid = snapshot_id or f"policy-snap-{int(ts)}-{int(eff_ambiguity_margin * 100)}"
    exceptions = (
        dict(named_domain_exceptions)
        if named_domain_exceptions is not None
        else dict(CANONICAL_DOMAIN_EXCEPTIONS)
    )

    body = {
        "policy_version": policy_version,
        "snapshot_id": sid,
        "created_at": ts,
        "ambiguity_margin": eff_ambiguity_margin,
        "boundary_inclusivity": boundary_inclusivity,
        "confidence_floor": confidence_floor,
        "entropy_ceiling": entropy_ceiling,
        "divergence_margin": eff_divergence_margin,
        "named_domain_exceptions": exceptions,
        "snapshot_digest": "",
    }

    digest_val = _compute_sha256(_canonical_json_bytes(body))

    return PolicySnapshot(
        policy_version=policy_version,
        snapshot_id=sid,
        created_at=ts,
        ambiguity_margin=eff_ambiguity_margin,
        boundary_inclusivity=boundary_inclusivity,
        confidence_floor=confidence_floor,
        entropy_ceiling=entropy_ceiling,
        divergence_margin=eff_divergence_margin,
        named_domain_exceptions=exceptions,
        snapshot_digest=digest_val,
    )


def verify_policy_snapshot_integrity(snapshot: PolicySnapshot) -> Tuple[bool, str]:
    """Verifies that a PolicySnapshot's content digest matches its payload."""
    body = snapshot.to_dict()
    recorded_digest = body.get("snapshot_digest", "")
    body["snapshot_digest"] = ""
    recalculated = _compute_sha256(_canonical_json_bytes(body))

    if recorded_digest != recalculated:
        return False, f"Policy snapshot digest mismatch: recorded={recorded_digest}, recalculated={recalculated}"
    return True, f"Policy snapshot {snapshot.snapshot_id} verified [digest: {recorded_digest[:16]}...]"


class ActiveRunPolicyRegistry:
    """Manages active run policy snapshots ensuring immutable binding per run.

    Once a run is bound to a snapshot, any attempt to mutate policy parameters
    or substitute a different snapshot during that run fails closed immediately.
    """

    def __init__(self) -> None:
        self._bound_runs: Dict[str, PolicySnapshot] = {}

    def bind_run_snapshot(self, run_id: str, snapshot: PolicySnapshot) -> PolicySnapshot:
        """Binds a run to a policy snapshot. Fails closed if already bound to a different snapshot."""
        if run_id in self._bound_runs:
            existing = self._bound_runs[run_id]
            if existing.snapshot_digest != snapshot.snapshot_digest:
                raise ValueError(
                    f"POLICY_MUTATION_PROHIBITED: Run '{run_id}' is already bound to policy snapshot "
                    f"'{existing.snapshot_id}' [digest={existing.snapshot_digest[:16]}]. "
                    f"Attempted mutation to snapshot '{snapshot.snapshot_id}' [digest={snapshot.snapshot_digest[:16]}]."
                )
            return existing

        ok, reason = verify_policy_snapshot_integrity(snapshot)
        if not ok:
            raise ValueError(f"CANNOT_BIND_INVALID_SNAPSHOT: {reason}")

        self._bound_runs[run_id] = snapshot
        return snapshot

    def get_run_snapshot(self, run_id: str) -> Optional[PolicySnapshot]:
        """Returns the bound snapshot for run_id, or None if unbound."""
        return self._bound_runs.get(run_id)

    def verify_run_policy_invariance(self, run_id: str, candidate: PolicySnapshot) -> Tuple[bool, str]:
        """Verifies that candidate snapshot matches the bound snapshot for run_id."""
        bound = self._bound_runs.get(run_id)
        if bound is None:
            return False, f"Run '{run_id}' has no bound policy snapshot."
        if bound.snapshot_digest != candidate.snapshot_digest:
            return (
                False,
                f"POLICY_MUTATION_DETECTED: Active run '{run_id}' bound digest {bound.snapshot_digest[:16]} "
                f"differs from candidate digest {candidate.snapshot_digest[:16]}.",
            )
        return True, f"Run '{run_id}' policy invariance verified."

    def unbind_run(self, run_id: str) -> None:
        """Cleans up bound run snapshot upon terminal completion."""
        self._bound_runs.pop(run_id, None)

    def clear(self) -> None:
        self._bound_runs.clear()


# Global Singleton for active runs
GLOBAL_POLICY_REGISTRY = ActiveRunPolicyRegistry()
