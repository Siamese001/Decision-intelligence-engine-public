"""
dual_pipeline/hitl_governance_core/decision_clearance_core/legacy_adapter.py

Legacy compatibility mapping for historical human decision records.
Ensures legacy records are interpreted under strict human authority without
permitting forged, synthetic, or unauthenticated origins.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from .models import ClearanceAuthority


def normalize_legacy_record(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalizes legacy human decision record dictionary into a canonical structure."""
    if not isinstance(raw, dict):
        return None

    dec_id = str(raw.get("decision_id") or "").strip()
    if not dec_id:
        return None

    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    if not meta and isinstance(raw.get("metadata"), str):
        try:
            meta = json.loads(raw["metadata"])
        except Exception:
            meta = {}

    op_id = str(raw.get("operator_id") or "").strip()
    origin = meta.get("approval_origin") or meta.get("origin_verification") or op_id

    # Validate origin
    is_valid, origin_msg = validate_approval_origin(origin)

    outcome = str(raw.get("outcome_type") or meta.get("outcome_type") or "").upper()
    selected = raw.get("selected_option") or raw.get("selected_key")

    return {
        "decision_id": dec_id,
        "operator_id": op_id,
        "origin": origin,
        "is_origin_valid": is_valid,
        "origin_rejection_reason": origin_msg if not is_valid else None,
        "outcome_type": outcome,
        "selected_option": selected,
        "entry_digest": str(raw.get("entry_digest") or ""),
        "metadata": meta,
        "wave": meta.get("wave"),
        "authority_type": ClearanceAuthority.HUMAN,
    }


def evaluate_legacy_clearance(record_dict: Dict[str, Any]) -> Tuple[bool, str]:
    """Evaluates whether a normalized legacy record grants human clearance."""
    if not record_dict.get("is_origin_valid", False):
        reason = record_dict.get("origin_rejection_reason") or "Invalid origin"
        return False, f"SYNTHETIC_APPROVAL_REJECTED: {reason}"

    selected = str(record_dict.get("selected_option") or "").upper()
    outcome = str(record_dict.get("outcome_type") or "").upper()

    if selected in ("DECLINED", "CANCELLED", "OPT_DECLINE") or outcome == "OVERRIDDEN":
        return False, "DECLINED: Decision was explicitly declined by human operator."

    if outcome in ("AFFIRMED", "RATIFIED", "RESOLVED") or selected:
        return True, f"Verified human operator ratification ('{record_dict.get('operator_id')}')"

    return False, "UNRATIFIED: Legacy record does not contain an approved resolution."
