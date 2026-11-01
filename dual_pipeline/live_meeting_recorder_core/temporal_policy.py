import datetime
from typing import Any, Dict, Optional, Tuple


def normalize_iso_date(date_str: str) -> Optional[datetime.date]:
    if not date_str:
        return None
    try:
        if "T" in date_str:
            return datetime.datetime.fromisoformat(date_str).date()
        return datetime.date.fromisoformat(date_str)
    except ValueError:
        return None

def classify_temporal_state(assertion: Dict[str, Any], as_of: datetime.date) -> Tuple[bool, str, float]:
    """
    Evaluates temporal/clearance state of an assertion against an as_of date.
    Returns:
      (is_allowed: bool, soft_label: str, recency_score: float)
    """
    # 1. Hard filters
    # If not cleared
    if assertion.get("is_assertion_cleared") is False:
        return False, "", 0.0

    # If explicitly ineligible
    if assertion.get("retrieval_eligible") in (0, False, "false", "0"):
        return False, "", 0.0

    status = assertion.get("validation_status", "active").lower()
    if status in ("rejected", "superseded", "historical"):
        return False, "", 0.0

    if assertion.get("superseded_by") or assertion.get("currentness") in ("historical", "superseded"):
        return False, "", 0.0

    # Valid window check
    valid_from = normalize_iso_date(assertion.get("valid_from", ""))
    valid_to = normalize_iso_date(assertion.get("valid_to", ""))
    if valid_from and valid_from > as_of:
        return False, "", 0.0
    if valid_to and valid_to < as_of:
        return False, "", 0.0

    # 2. Soft labels
    labels = []
    if status == "candidate" or status == "unknown":
        labels.append("[UNCONFIRMED]")

    source_date_str = assertion.get("source_date_normalized") or assertion.get("observed_at")
    source_date = normalize_iso_date(source_date_str)
    if not source_date:
        labels.append("[UNDATED]")

    if assertion.get("has_unresolved_conflict") or assertion.get("assertion_conflict"):
        labels.append("[CONTESTED]")

    soft_label_str = " ".join(labels)

    # 3. Score logic: recency 0.5^(age/half_life)
    # half_life: 3d for blockers/risks, 30d for decisions/owners, 180d for governance
    pred = assertion.get("predicate", "").upper()
    if pred in ("HAS_RISK", "BLOCKS", "DEPENDS_ON"):
        half_life_days = 3.0
    elif pred in ("DECIDED", "OWNS", "OWNS_OR_LEADS"):
        half_life_days = 30.0
    else:
        half_life_days = 180.0

    recency_score = 1.0
    if source_date:
        age_days = (as_of - source_date).days
        if age_days < 0:
            age_days = 0
        recency_score = 0.5 ** (age_days / half_life_days)
    else:
        recency_score = 0.5  # default undated penalty

    # Multiply by evidence support if available
    ev_support = float(assertion.get("evidence_support", 1.0))
    recency_score *= ev_support

    return True, soft_label_str, recency_score
