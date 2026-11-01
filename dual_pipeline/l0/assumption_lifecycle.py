"""L0 Assumption Lifecycle Policy and Predicate Definitions.

Governs epistemic assertion typing, checkpoint TTL resolution, and temporal expiry predicates.
Adheres strictly to L0 layer boundaries (zero upward dependencies).
"""
from __future__ import annotations

import datetime
import re
from typing import Any

from dual_pipeline.parameters import get_default_assumption_ttl_days

# Governed Epistemic Types registered in controlled_value_registry (vocabulary = 'assertion_type')
CONTROLLED_EPISTEMIC_TYPES: frozenset[str] = frozenset({
    "assumption",
    "hypothesis",
    "open_question",
    "recommendation",
    "decision",
    "direct_statement",
    "confirmed_canonical_fact",
    "extracted_claim",
})

# Epistemic types that represent unconfirmed exploratory assertions subject to TTL & revalidation
EXPLORATORY_EPISTEMIC_TYPES: frozenset[str] = frozenset({
    "assumption",
    "hypothesis",
})

# Negative and Superseding Decision Predicates
DECISION_GOVERNANCE_PREDICATES: frozenset[str] = frozenset({
    "DECIDED_AGAINST",
    "REJECTED",
    "STANDARDIZES_ON",
})

# Default TTL in days for working notes or uncheckpointed exploratory assumptions
DEFAULT_ASSUMPTION_TTL_DAYS: int = get_default_assumption_ttl_days()

# Fallback default validation owner when hitl_required=1 and no explicit owner is supplied
DEFAULT_VALIDATION_OWNER: str = "Amit / Account lead"


def normalize_epistemic_assertion_type(raw_type: str | None) -> str:
    """Normalize raw statement/candidate type to a governed controlled_value_registry assertion_type."""
    if not raw_type:
        return "extracted_claim"
    val = str(raw_type).strip().lower()
    if val in ("assumption", "proposal", "contingency", "speculation"):
        return "assumption"
    if val in ("hypothesis", "risk"):
        return "hypothesis"
    if val in ("open_question", "question"):
        return "open_question"
    if val in ("recommendation", "suggested_action"):
        return "recommendation"
    if val in ("decision", "resolved_choice"):
        return "decision"
    if val in ("direct_statement", "quote"):
        return "direct_statement"
    if val in ("confirmed_canonical_fact", "fact"):
        return "confirmed_canonical_fact"
    if val in CONTROLLED_EPISTEMIC_TYPES:
        return val
    return "extracted_claim"


def parse_iso_or_slash_date(raw_date: Any) -> str | None:
    """Parse various date formats (YYYY-MM-DD, MM/DD/YYYY, MM-DD-YYYY) to standard YYYY-MM-DD."""
    if not raw_date:
        return None
    val = str(raw_date).strip()
    # YYYY-MM-DD
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", val)
    if m_iso:
        return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
    # MM/DD/YYYY or MM-DD-YYYY
    m_us = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", val)
    if m_us:
        m, d, y = int(m_us.group(1)), int(m_us.group(2)), int(m_us.group(3))
        return f"{y:04d}-{m:02d}-{d:02d}"
    return None


def resolve_assumption_checkpoint_ttl(
    candidate: dict[str, Any],
    payload: dict[str, Any],
    source: dict[str, Any],
) -> str | None:
    """Resolve an ISO YYYY-MM-DD expiration date for exploratory assumptions.

    Precedence:
    1. Explicit checkpoint/stale_after date on payload or candidate.
    2. Checkpoint extracted from edge_traits or qualifiers.
    3. If source_posture == 'working_notes' or assertion_type is exploratory:
       source_date + DEFAULT_ASSUMPTION_TTL_DAYS (14 days).
    4. Otherwise None.
    """
    edge_traits = payload.get("edge_traits") or {}
    raw_deadline = (
        payload.get("stale_after")
        or payload.get("checkpoint_date")
        or candidate.get("stale_after")
        or candidate.get("checkpoint_date")
        or edge_traits.get("stale_after")
        or edge_traits.get("checkpoint_date")
    )
    parsed = parse_iso_or_slash_date(raw_deadline)
    if parsed:
        return parsed

    # Check if candidate or payload qualifies as exploratory
    raw_type = edge_traits.get("assertion_type") or candidate.get("assertion_type") or candidate.get("candidate_type")
    norm_type = normalize_epistemic_assertion_type(raw_type)
    source_posture = source.get("source_posture")

    if norm_type in EXPLORATORY_EPISTEMIC_TYPES or source_posture == "working_notes":
        src_date = parse_iso_or_slash_date(source.get("date"))
        if src_date:
            try:
                dt = datetime.date.fromisoformat(src_date)
                expiry = dt + datetime.timedelta(days=DEFAULT_ASSUMPTION_TTL_DAYS)
                return expiry.isoformat()
            except ValueError:
                pass
    return None


def _extract_stale_after_cutoff(stale_after: Any) -> str | None:
    """Extract a deadline string from a dict, JSON string, or date string."""
    if not stale_after:
        return None
    if isinstance(stale_after, dict):
        return stale_after.get("stale_after") or stale_after.get("checkpoint_date")
    if not isinstance(stale_after, str):
        return None
    val = stale_after.strip()
    if val.startswith("{") and ("stale_after" in val or "checkpoint_date" in val):
        try:
            import json
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed.get("stale_after") or parsed.get("checkpoint_date")
        except Exception:
            pass
    return val


def _resolve_as_of_date(as_of_date: str | datetime.date | None) -> datetime.date | None:
    """Resolve reference date for temporal expiration checks."""
    if as_of_date is None:
        return datetime.date.today()
    if isinstance(as_of_date, datetime.date):
        return as_of_date
    parsed = parse_iso_or_slash_date(as_of_date)
    if not parsed:
        return None
    try:
        return datetime.date.fromisoformat(parsed)
    except ValueError:
        return None


def is_assertion_temporally_expired(
    valid_to: str | None,
    stale_after: Any = None,
    as_of_date: str | datetime.date | None = None,
) -> bool:
    """Evaluate whether an assertion has expired based on valid_to or qualifiers.stale_after."""
    raw_cutoff = valid_to or _extract_stale_after_cutoff(stale_after)
    if not raw_cutoff:
        return False
    parsed_cutoff = parse_iso_or_slash_date(raw_cutoff)
    if not parsed_cutoff:
        return False

    ref_date = _resolve_as_of_date(as_of_date)
    if ref_date is None:
        return False

    try:
        cutoff_dt = datetime.date.fromisoformat(parsed_cutoff)
        return cutoff_dt <= ref_date
    except ValueError:
        return False


def adjust_retrieval_record_for_assumption(
    retrieval_metadata: dict[str, str],
    extensions: dict[str, Any],
    *,
    assertion_type: str | None,
    predicate: str | None,
    lane: str,
) -> None:
    """Adjust retrieval record answer_use and extensions for assumptions and decision governance."""
    norm_type = normalize_epistemic_assertion_type(assertion_type)
    if predicate in {"DECIDED_AGAINST", "REJECTED"}:
        retrieval_metadata["answer_use"] = "strategic_context"
    elif norm_type in EXPLORATORY_EPISTEMIC_TYPES and lane == "candidate":
        if retrieval_metadata.get("retrieval_kind") == "evidence_span":
            retrieval_metadata["answer_use"] = "citation_only"
        else:
            retrieval_metadata["answer_use"] = "candidate_hypothesis"
        extensions["com.partner_advisory.nee.epistemic-posture"] = "unconfirmed_assumption"


def build_assertion_retrieval_extensions(
    row: Any,
    row_keys: set[str],
    graph_entities: list[str],
    graph_evidence_ids: list[str],
    status_fields: dict[str, Any],
) -> dict[str, Any]:
    """Construct extensions dictionary for assertion retrieval records."""
    return {
        "com.partner_advisory.nee.graph-object-id": row["assertion_id"],
        "com.partner_advisory.nee.graph-entity-ids": graph_entities,
        "com.partner_advisory.nee.graph-evidence-ids": graph_evidence_ids,
        "com.partner_advisory.nee.source-id": row["primary_source_id"] if "primary_source_id" in row_keys else None,
        "com.partner_advisory.nee.predicate": row["predicate"],
        "com.partner_advisory.nee.assertion-type": row["assertion_type"] if "assertion_type" in row_keys else None,
        "com.partner_advisory.nee.origin-type": row["origin_type"] if "origin_type" in row_keys else None,
        "com.partner_advisory.nee.extraction-run-id": row["extraction_run_id"] if "extraction_run_id" in row_keys else None,
        "com.partner_advisory.nee.hitl-required": row["hitl_required"] if "hitl_required" in row_keys else None,
        "com.partner_advisory.nee.validation-priority": row["validation_priority"] if "validation_priority" in row_keys else None,
        "com.partner_advisory.nee.validation-owner": row["validation_owner"] if "validation_owner" in row_keys else None,
        "com.partner_advisory.nee.atomicity-state": row["atomicity_state"] if "atomicity_state" in row_keys else None,
        **status_fields,
    }


