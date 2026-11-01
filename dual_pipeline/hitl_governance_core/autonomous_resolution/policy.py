"""
dual_pipeline/hitl_governance_core/autonomous_resolution/policy.py

Immutable policy snapshot binding and integrity validation.
Captures an immutable snapshot of SSOT parameters and guarantees zero drift
between evaluation and persistence.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_entropy_ambiguity_threshold,
    get_hitl_min_autonomous_confidence_floor,
)

from .models import PolicySnapshot


def compute_policy_digest(
    ambiguity_threshold: float,
    conviction_floor: float,
    max_entropy: float,
    divergence_threshold: float,
    policy_source: str,
) -> str:
    """Computes a deterministic SHA-256 digest of canonical policy fields."""
    canonical_data = {
        "ambiguity_threshold": round(float(ambiguity_threshold), 4),
        "conviction_floor": round(float(conviction_floor), 4),
        "divergence_threshold": round(float(divergence_threshold), 4),
        "max_entropy": round(float(max_entropy), 4),
        "policy_source": str(policy_source).strip(),
    }
    encoded = json.dumps(canonical_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_policy_snapshot(
    overrides: Optional[Mapping[str, Any]] = None,
    source: str = "governed_ssot_runtime",
) -> PolicySnapshot:
    """
    Constructs an immutable PolicySnapshot from active governed parameters
    or explicit authorized overrides.
    """
    overrides = overrides or {}

    ambiguity_thresh = float(
        overrides.get("ambiguity_threshold", get_hitl_ambiguity_margin_threshold())
    )
    conviction_fl = float(
        overrides.get("conviction_floor", get_hitl_min_autonomous_confidence_floor())
    )
    max_ent = float(
        overrides.get("max_entropy", get_hitl_entropy_ambiguity_threshold())
    )
    divergence_thresh = float(
        overrides.get("divergence_threshold", 0.20)
    )
    policy_src = str(overrides.get("policy_source", source))

    digest = compute_policy_digest(
        ambiguity_threshold=ambiguity_thresh,
        conviction_floor=conviction_fl,
        max_entropy=max_ent,
        divergence_threshold=divergence_thresh,
        policy_source=policy_src,
    )

    snapshot_id = f"policy_snap_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    return PolicySnapshot(
        snapshot_id=snapshot_id,
        ambiguity_threshold=ambiguity_thresh,
        conviction_floor=conviction_fl,
        max_entropy=max_ent,
        divergence_threshold=divergence_thresh,
        policy_source=policy_src,
        policy_digest=digest,
        frozen_at_iso=now_iso,
    )


def validate_policy_snapshot(snapshot: PolicySnapshot) -> Tuple[bool, List[str]]:
    """
    Validates mathematical and cryptographic integrity of a PolicySnapshot.
    Returns (is_valid, list_of_defects).
    """
    defects: List[str] = []

    # Check finite float values
    for name, val in [
        ("ambiguity_threshold", snapshot.ambiguity_threshold),
        ("conviction_floor", snapshot.conviction_floor),
        ("max_entropy", snapshot.max_entropy),
        ("divergence_threshold", snapshot.divergence_threshold),
    ]:
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            defects.append(f"Policy field '{name}' is not numeric.")
        elif math.isnan(val) or math.isinf(val):
            defects.append(f"Policy field '{name}' is NaN or Infinite.")
        elif val <= 0.0 or val >= 1.0:
            defects.append(f"Policy field '{name}' ({val}) is outside valid range (0.0, 1.0).")

    if not snapshot.snapshot_id:
        defects.append("Missing snapshot_id.")
    if not snapshot.frozen_at_iso:
        defects.append("Missing frozen_at_iso timestamp.")

    expected_digest = compute_policy_digest(
        ambiguity_threshold=snapshot.ambiguity_threshold,
        conviction_floor=snapshot.conviction_floor,
        max_entropy=snapshot.max_entropy,
        divergence_threshold=snapshot.divergence_threshold,
        policy_source=snapshot.policy_source,
    )
    if snapshot.policy_digest != expected_digest:
        defects.append(
            f"Policy digest mismatch: got '{snapshot.policy_digest}', expected '{expected_digest}'."
        )

    return len(defects) == 0, defects


load_active_policy_snapshot = load_policy_snapshot

