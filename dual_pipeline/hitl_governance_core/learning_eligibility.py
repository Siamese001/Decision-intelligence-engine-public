"""
dual_pipeline/hitl_governance_core/learning_eligibility.py

Governed record eligibility, deduplication, and independent outcome evaluation
for Bayesian learning and operator prior calibration.
Enforces Master Invariants:
- Zero empirical calibration mass from replayed or flooded autonomous audit records.
- Zero sample maturity increase from test or mock decisions.
- Negative independent outcomes lower posterior confidence.
- Current choice cannot affect its frozen priors.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dual_pipeline.parameters import (
    get_hitl_autonomous_feedback_weight,
    get_hitl_human_feedback_weight,
)

logger = logging.getLogger(__name__)

TEST_OPERATOR_TOKENS = frozenset({"test", "mock", "pytest", "unit_test"})
AUTONOMOUS_OPERATOR_IDS = frozenset({
    "engine_autonomous_authority_01",
    "autonomous_engine",
    "autonomous",
    "auto_engine",
    "auto",
    "autonomous_context",
})


def is_test_record(record: Dict[str, Any]) -> bool:
    """Determines if a decision record is a test/mock fixture that must be excluded from learning."""
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    op_class = str(record.get("operator_class") or meta.get("operator_class") or "").strip().upper()
    if op_class == "TEST":
        return True
    if record.get("is_test") is True or meta.get("is_test") is True:
        return True

    op_id = str(record.get("operator_id", "")).lower().strip()
    if any(t in op_id for t in TEST_OPERATOR_TOKENS):
        return True

    dec_id = str(record.get("decision_id", "")).lower().strip()
    if dec_id.startswith("test_") or dec_id.startswith("mock_") or "_test_" in dec_id:
        return True

    return False


def is_autonomous_record(record: Dict[str, Any]) -> bool:
    """Determines if a decision record was produced autonomously without human interaction."""
    from .scoped_autonomy import is_autonomous_alias
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for f in ("authority_type", "operator_class", "outcome_type", "execution_mode", "agent_type"):
        val = str(record.get(f) or meta.get(f) or "").strip().upper()
        if val in ("AUTONOMOUS", "AUTO"):
            return True

    op_id = str(record.get("operator_id", "")).strip()
    return is_autonomous_alias(op_id)


def is_override_record(record: Dict[str, Any]) -> bool:
    """Determines if record is a human override of an autonomous/prior recommendation."""
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    if str(record.get("outcome_type") or meta.get("outcome_type") or "").strip().upper() == "OVERRIDDEN":
        return True
    if record.get("is_override") is True or meta.get("is_override") is True:
        return True
    rec_key = extract_override_recommendation(record)
    sel_key = str(record.get("selected_key") or record.get("selected_option_id") or "").strip().lower()
    return bool(rec_key and sel_key and rec_key.lower() != sel_key)


def extract_override_recommendation(record: Dict[str, Any]) -> Optional[str]:
    """Extracts the superseded recommendation key from an override record."""
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for k in ("recommended_key", "recommended_option_id", "top_option_key", "prior_recommendation"):
        val = record.get(k) or meta.get(k)
        if val:
            return str(val).strip()
    rec_opt = record.get("recommended_option") or meta.get("recommended_option")
    if isinstance(rec_opt, dict):
        return str(rec_opt.get("option_id") or rec_opt.get("key") or rec_opt.get("label") or "").strip()
    elif rec_opt:
        return str(rec_opt).strip()
    return None


def extract_independent_outcome(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extracts structured independent outcome if present on the record."""
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    outcome = record.get("independent_outcome") or meta.get("independent_outcome")
    if isinstance(outcome, dict):
        return outcome
    status = record.get("independent_outcome_status") or meta.get("independent_outcome_status")
    if status:
        return {
            "status": str(status).upper(),
            "reason": str(record.get("independent_outcome_reason") or meta.get("independent_outcome_reason") or ""),
        }
    return None


def is_negative_record(record: Dict[str, Any]) -> bool:
    """Returns True if record represents an outright rejected, declined, or failed outcome."""
    if is_override_record(record):
        return False
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    outcome_type = str(record.get("outcome_type") or meta.get("outcome_type") or "").strip().upper()
    if outcome_type in ("REJECTED", "DECLINED", "FAILURE"):
        return True
    indep = extract_independent_outcome(record)
    if isinstance(indep, dict):
        status = str(indep.get("status", "")).strip().upper()
        if status in ("FAILURE", "FAILED", "REJECTED", "ERROR"):
            return True
    return False


def _eval_autonomous_eligibility(indep: Optional[Dict[str, Any]]) -> Tuple[float, bool, str]:
    if indep is not None:
        status = str(indep.get("status", "")).strip().upper()
        if status in ("FAILURE", "FAILED", "REJECTED", "ERROR"):
            return -1.0, True, "AUTONOMOUS_INDEPENDENT_FAILURE"
        if status in ("SUCCESS", "AFFIRMED", "VERIFIED"):
            return get_hitl_autonomous_feedback_weight(), True, "AUTONOMOUS_INDEPENDENT_SUCCESS"
    return 0.0, False, "AUTONOMOUS_AUDIT_ONLY"


def _eval_human_eligibility(
    outcome_type: str, is_override: bool, indep: Optional[Dict[str, Any]]
) -> Tuple[float, bool, str]:
    human_w = get_hitl_human_feedback_weight()
    if is_override:
        return human_w, True, "HUMAN_OVERRIDE_AFFIRMED"
    if outcome_type in ("REJECTED", "DECLINED"):
        return -human_w, True, "HUMAN_REJECTED"
    if indep is not None:
        status = str(indep.get("status", "")).strip().upper()
        if status in ("FAILURE", "FAILED", "REJECTED", "ERROR"):
            return -human_w, True, "HUMAN_INDEPENDENT_FAILURE"
    return human_w, True, "HUMAN_AFFIRMED"


def compute_record_eligibility(record: Dict[str, Any]) -> Tuple[float, bool, str]:
    """Computes calibration weight and sample maturity eligibility for a decision record."""
    if not isinstance(record, dict) or not record or not (record.get("decision_id") or record.get("entry_id") or record.get("ambiguity_id")):
        return 0.0, False, "EMPTY_RECORD"
    if is_test_record(record):
        return 0.0, False, "TEST_RECORD_EXCLUDED"

    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    outcome_type = str(record.get("outcome_type") or meta.get("outcome_type") or "").strip().upper()
    indep = extract_independent_outcome(record)

    if is_autonomous_record(record):
        return _eval_autonomous_eligibility(indep)
    return _eval_human_eligibility(outcome_type, is_override_record(record), indep)


def _compute_dedup_key(r: Dict[str, Any]) -> str:
    meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
    idemp = str(r.get("idempotency_key") or meta.get("idempotency_key") or "").strip()
    if idemp:
        return f"idemp:{idemp}"
    dec_id = str(r.get("decision_id") or "").strip()
    sel = str(r.get("selected_key") or r.get("selected_option_id") or "").strip().lower()
    if dec_id and sel:
        return f"dec_sel:{dec_id}:{sel}"
    if dec_id:
        return f"dec:{dec_id}"
    entry_id = str(r.get("entry_id") or r.get("resolution_id") or "").strip()
    if entry_id:
        return f"entry:{entry_id}"
    return f"digest:{r.get('entry_digest') or r.get('resolution_digest') or id(r)}"


def deduplicate_records_for_learning(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicates decision records to prevent duplicate or replayed audit records from multiplying mass."""
    seen_keys: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        key = _compute_dedup_key(r)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(r)
    return deduped


def _is_self_lineage_record(r: Dict[str, Any], clean_curr: str, clean_lineage: str) -> bool:
    meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
    if clean_curr:
        r_dec = str(r.get("decision_id", "")).strip()
        r_parent = str(r.get("parent_decision_id") or meta.get("parent_decision_id") or "").strip()
        r_retry = str(r.get("retry_of") or meta.get("retry_of") or "").strip()
        if r_dec == clean_curr or r_parent == clean_curr or r_retry == clean_curr:
            return True
    if clean_lineage:
        r_lineage = str(r.get("lineage_id") or meta.get("lineage_id") or "").strip()
        if r_lineage == clean_lineage:
            return True
    return False


def filter_frozen_priors(
    records: Sequence[Dict[str, Any]],
    current_decision_id: Optional[str] = None,
    current_lineage_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filters records to freeze priors: current decision, retries, and lineage cannot affect their own evaluation."""
    if not current_decision_id and not current_lineage_id:
        return list(records)
    clean_curr = str(current_decision_id or "").strip()
    clean_lineage = str(current_lineage_id or "").strip()
    return [
        r for r in records
        if isinstance(r, dict) and not _is_self_lineage_record(r, clean_curr, clean_lineage)
    ]
