"""
dual_pipeline/live_meeting_recorder_core/copilot_predicates.py

Single Source of Truth (SSOT) predicate vocabulary for Executive Copilot
and query planning blueprints. Aligned with the database-level
`assertion_predicate_registry`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Optional, Set

from storage.connection import create_sqlite_conn, sqlite3

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_predicates")

# Canonical registry SSOT matching the database assertion_predicate_registry table
CANONICAL_PREDICATE_REGISTRY: frozenset[str] = frozenset(
    {
        "AFFILIATED_WITH",
        "CANDIDATE_FOR",
        "CONSTRAINS",
        "CONTAINS",
        "CONTEXT_FOR",
        "CONTRIBUTES_TO",
        "DELIVERS",
        "DEPENDS_ON",
        "GOVERNS",
        "HAS_CLAIM_TEXT",
        "HAS_RISK",
        "HAS_ROLE_IN",
        "OWNS_OR_LEADS",
        "RELATED_TO",
        "REPORTS_TO",
        "ROUTES_TO",
        "SUPPORTED_BY",
        "TRANSITIONS_TO",
        "USES_PATTERN",
        "VALIDATES",
    }
)

# High-salience relationship families and predicates prioritized for live executive context.
# Explicitly includes HAS_RISK, SUPPORTED_BY, CANDIDATE_FOR, and CONTAINS (fixing gap E3).
PRIORITY_PREDICATES: frozenset[str] = frozenset(
    {
        "HAS_RISK",
        "SUPPORTED_BY",
        "CANDIDATE_FOR",
        "CONTAINS",
        "OWNS_OR_LEADS",
        "DELIVERS",
        "DEPENDS_ON",
        "GOVERNS",
        "CONSTRAINS",
        "VALIDATES",
        "CONTEXT_FOR",
        "CONTRIBUTES_TO",
        "USES_PATTERN",
        "TRANSITIONS_TO",
    }
)

# Backward-compatible alias for existing imports
PRIORITY_RELATIONSHIPS = PRIORITY_PREDICATES

# Predicates prioritized for synthesizing high-salience questions (decisions, risks, constraints)
QUESTION_PREDICATES: frozenset[str] = frozenset(
    {
        "HAS_RISK",
        "CANDIDATE_FOR",
        "CONSTRAINS",
        "CONTEXT_FOR",
        "TRANSITIONS_TO",
    }
)

# Predicates prioritized for synthesizing high-salience actions (ownership, deliverables, dependencies)
ACTION_PREDICATES: frozenset[str] = frozenset(
    {
        "OWNS_OR_LEADS",
        "DELIVERS",
        "DEPENDS_ON",
        "GOVERNS",
        "VALIDATES",
        "CONTRIBUTES_TO",
    }
)

# Structured prefetch query specification predicate filters
PREFETCH_DECISION_PREDICATES: tuple[str, ...] = (
    "CONTEXT_FOR",
    "TRANSITIONS_TO",
    "VALIDATES",
    "CANDIDATE_FOR",
)

PREFETCH_RISK_PREDICATES: tuple[str, ...] = (
    "HAS_RISK",
    "CONSTRAINS",
)

PREFETCH_ACTION_PREDICATES: tuple[str, ...] = (
    "DELIVERS",
    "DEPENDS_ON",
    "OWNS_OR_LEADS",
    "GOVERNS",
)

PREFETCH_TOPIC_PREDICATES: tuple[str, ...] = (
    "OWNS_OR_LEADS",
    "VALIDATES",
    "GOVERNS",
    "DELIVERS",
    "DEPENDS_ON",
    "CONTAINS",
    "SUPPORTED_BY",
)

PREFETCH_ATTENDEE_PREDICATES: tuple[str, ...] = (
    "OWNS_OR_LEADS",
    "VALIDATES",
    "GOVERNS",
    "AFFILIATED_WITH",
    "HAS_ROLE_IN",
    "REPORTS_TO",
)

# Superset of all copilot predicates
ALL_COPILOT_PREDICATES: frozenset[str] = frozenset(
    PRIORITY_PREDICATES
    | QUESTION_PREDICATES
    | ACTION_PREDICATES
    | set(PREFETCH_DECISION_PREDICATES)
    | set(PREFETCH_RISK_PREDICATES)
    | set(PREFETCH_ACTION_PREDICATES)
    | set(PREFETCH_TOPIC_PREDICATES)
    | set(PREFETCH_ATTENDEE_PREDICATES)
)

# Predicate volatility families for temporal decay scoring (Wave 2)
VOLATILE_OPERATIONAL_PREDICATES: frozenset[str] = frozenset(
    {
        "HAS_RISK",
        "CONSTRAINS",
        "DELIVERS",
        "DEPENDS_ON",
        "CANDIDATE_FOR",
        "TRANSITIONS_TO",
        "CONTRIBUTES_TO",
        "CONTEXT_FOR",
    }
)

STRUCTURAL_IDENTITY_PREDICATES: frozenset[str] = frozenset(
    {
        "OWNS_OR_LEADS",
        "GOVERNS",
        "VALIDATES",
        "AFFILIATED_WITH",
        "HAS_ROLE_IN",
        "REPORTS_TO",
        "CONTAINS",
        "SUPPORTED_BY",
        "USES_PATTERN",
        "RELATED_TO",
    }
)

# Deterministic alias resolution from legacy or informal terms to canonical registry predicates
PREDICATE_ALIASES: dict[str, str] = {
    "RISK": "HAS_RISK",
    "RISK_OF": "HAS_RISK",
    "RAISED_CONCERN": "HAS_RISK",
    "ESCALATED": "HAS_RISK",
    "DECISION": "CONTEXT_FOR",
    "DECIDED": "CONTEXT_FOR",
    "DECISION_NEEDED": "CONTEXT_FOR",
    "HAS_OPEN_QUESTION": "CONTEXT_FOR",
    "QUESTIONED": "CONTEXT_FOR",
    "STATED": "CONTEXT_FOR",
    "REPORTED": "CONTEXT_FOR",
    "PROPOSED": "CANDIDATE_FOR",
    "RECOMMENDS": "CANDIDATE_FOR",
    "BLOCKED_BY": "CONSTRAINS",
    "CHALLENGED": "CONSTRAINS",
    "OWNS": "OWNS_OR_LEADS",
    "LEADS": "OWNS_OR_LEADS",
    "MANAGES": "OWNS_OR_LEADS",
    "COMMITTED_TO": "DELIVERS",
    "ALIGNED_ON": "VALIDATES",
    "AGREED_TO": "VALIDATES",
    "HAS_ROLE": "HAS_ROLE_IN",
    "SPONSORS": "GOVERNS",
    "GOVERNED_BY": "GOVERNS",
    "CRITICAL_DEPENDENCY": "DEPENDS_ON",
    "ADVISES": "AFFILIATED_WITH",
    "EMPLOYED_BY": "AFFILIATED_WITH",
    "MEMBER_OF": "AFFILIATED_WITH",
    "ASSIGNED_TO": "AFFILIATED_WITH",
    "PART_OF": "AFFILIATED_WITH",
    "ACCELERATES": "CONTRIBUTES_TO",
    "DELIVERS_VIA": "DELIVERS",
    "DELIVERS_VALUE_TO": "DELIVERS",
    "INTEGRATES_WITH": "RELATED_TO",
}


def normalize_copilot_predicate(raw_predicate: Optional[str]) -> str:
    """Normalizes a raw predicate string to canonical predicate registry representation."""
    if not raw_predicate:
        return ""
    cleaned = str(raw_predicate).strip().upper()
    if cleaned in CANONICAL_PREDICATE_REGISTRY:
        return cleaned
    if cleaned in PREDICATE_ALIASES:
        return PREDICATE_ALIASES[cleaned]
    return cleaned


class PredicateRegistryValidationError(ValueError):
    """Raised when copilot predicates fail validation against assertion_predicate_registry."""
    pass


def validate_copilot_predicates_against_registry(
    db_target: Any = None,
    predicates_to_check: Optional[Iterable[str]] = None,
) -> bool:
    """
    Validates that copilot predicates are strictly registered in assertion_predicate_registry.
    Fails closed if any predicate is missing or if the registry is empty.
    """
    to_verify = set(predicates_to_check) if predicates_to_check is not None else set(ALL_COPILOT_PREDICATES)
    if not to_verify:
        raise PredicateRegistryValidationError("Predicate set to verify is empty.")

    registered_predicates: Set[str] = set()

    # If an active sqlite connection is passed
    if isinstance(db_target, sqlite3.Connection):
        cur = db_target.cursor()
        has_table = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_predicate_registry'"
        ).fetchone()
        if not has_table:
            raise PredicateRegistryValidationError("Database is missing assertion_predicate_registry table.")
        rows = cur.execute("SELECT predicate FROM assertion_predicate_registry").fetchall()
        registered_predicates = {r[0] for r in rows if r[0]}
    elif isinstance(db_target, (str, Path)) and Path(db_target).is_file():
        conn = create_sqlite_conn(db_target, readonly=True)
        try:
            cur = conn.cursor()
            has_table = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_predicate_registry'"
            ).fetchone()
            if not has_table:
                raise PredicateRegistryValidationError("Database is missing assertion_predicate_registry table.")
            rows = cur.execute("SELECT predicate FROM assertion_predicate_registry").fetchall()
            registered_predicates = {r[0] for r in rows if r[0]}
        finally:
            conn.close()
    else:
        # Default to canonical registry SSOT
        registered_predicates = set(CANONICAL_PREDICATE_REGISTRY)

    if not registered_predicates:
        raise PredicateRegistryValidationError("Predicate registry is empty.")

    unregistered = to_verify - registered_predicates
    if unregistered:
        raise PredicateRegistryValidationError(
            f"Copilot predicates not registered in assertion_predicate_registry: {sorted(unregistered)}"
        )

    return True
