"""
dual_pipeline.graph_maintenance_core.supersession_classifier

Deterministic Supersession & Contradiction Classifier for Wave 10.
Enforces:
1. Multi-valued predicates and disjoint validity intervals produce COEXISTENCE, never destructive supersession.
2. Contradictions require single-valued exclusivity with overlapping validity intervals.
3. Inconclusive evidence produces UNKNOWN; missing evidence produces DEFERRED.
4. Frequency never proves truth: mention count or recurrence cannot override provenance or validity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

MULTI_VALUED_PREDICATES: Set[str] = frozenset({
    "has_role",
    "skills",
    "attended",
    "member_of",
    "contributed_to",
    "certified_in",
    "author_of",
    "participant_in",
    "topic",
    "tag",
    "alias",
    "collaborator",
    "advisory_notes",
    "project_membership",
})


class MaintenanceOutcomeType(str, Enum):
    """Typed classification outcomes for graph maintenance and conflict evaluation."""
    CONTRADICTION = "CONTRADICTION"
    COEXISTENCE = "COEXISTENCE"
    UNKNOWN = "UNKNOWN"
    DEFERRED = "DEFERRED"


@dataclass(frozen=True)
class MaintenanceOutcomeResult:
    """Immutable classification result for competing or candidate assertions."""
    outcome_type: MaintenanceOutcomeType
    predicate: str
    is_multi_valued: bool
    is_temporally_disjoint: bool
    rationale: str
    requires_evidence_acquisition: bool = False
    permits_destructive_supersession: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


def are_intervals_disjoint(
    interval_a: Optional[Tuple[str, str]],
    interval_b: Optional[Tuple[str, str]],
) -> bool:
    """Returns True if two ISO date intervals [start, end] do not overlap."""
    if not interval_a or not interval_b:
        return False
    try:
        if len(interval_a) != 2 or len(interval_b) != 2:
            return False
        start_a, end_a = str(interval_a[0]).strip(), str(interval_a[1]).strip()
        start_b, end_b = str(interval_b[0]).strip(), str(interval_b[1]).strip()
    except (TypeError, IndexError, ValueError):
        return False

    if not (start_a and end_a and start_b and end_b):
        return False
    # Inverted dates are malformed intervals and cannot be evaluated as disjoint
    if start_a > end_a or start_b > end_b:
        return False

    # If A ends before B starts, or B ends before A starts, they are disjoint
    return end_a < start_b or end_b < start_a


def is_multi_valued_predicate(predicate: Optional[str]) -> bool:
    """Checks if predicate represents a multi-valued relation."""
    clean = str(predicate or "").strip().lower()
    return clean in MULTI_VALUED_PREDICATES


def _check_coexistence_rules(
    assertion_a: Dict[str, Any],
    assertion_b: Dict[str, Any],
    pred: str,
    val_a: str,
    val_b: str,
) -> Optional[MaintenanceOutcomeResult]:
    if val_a and val_b and val_a.lower() == val_b.lower():
        return MaintenanceOutcomeResult(
            outcome_type=MaintenanceOutcomeType.COEXISTENCE,
            predicate=pred,
            is_multi_valued=is_multi_valued_predicate(pred),
            is_temporally_disjoint=False,
            rationale="Identical assertion values indicate non-conflicting agreement.",
            permits_destructive_supersession=False,
        )

    if is_multi_valued_predicate(pred) or assertion_a.get("is_multi_valued") or assertion_b.get("is_multi_valued"):
        return MaintenanceOutcomeResult(
            outcome_type=MaintenanceOutcomeType.COEXISTENCE,
            predicate=pred,
            is_multi_valued=True,
            is_temporally_disjoint=False,
            rationale=f"Multi-valued predicate '{pred}' permits multiple simultaneous valid objects.",
            permits_destructive_supersession=False,
        )

    int_a = assertion_a.get("validity_interval")
    int_b = assertion_b.get("validity_interval")
    if isinstance(int_a, (list, tuple)) and isinstance(int_b, (list, tuple)):
        if are_intervals_disjoint(tuple(int_a[:2]), tuple(int_b[:2])):
            return MaintenanceOutcomeResult(
                outcome_type=MaintenanceOutcomeType.COEXISTENCE,
                predicate=pred,
                is_multi_valued=False,
                is_temporally_disjoint=True,
                rationale="Disjoint temporal validity intervals indicate state evolution over time, not contradiction.",
                permits_destructive_supersession=False,
            )
    return None


def classify_assertion_relationship(
    assertion_a: Dict[str, Any],
    assertion_b: Dict[str, Any],
    predicate: Optional[str] = None,
) -> MaintenanceOutcomeResult:
    """Classifies relationship between two assertions on the same subject."""
    pred = str(predicate or assertion_a.get("predicate") or assertion_b.get("predicate") or "").strip().lower()
    val_a = str(assertion_a.get("object_literal") or assertion_a.get("object_node_id") or "").strip()
    val_b = str(assertion_b.get("object_literal") or assertion_b.get("object_node_id") or "").strip()

    if assertion_a.get("evidence_missing") or assertion_b.get("evidence_missing"):
        return MaintenanceOutcomeResult(
            outcome_type=MaintenanceOutcomeType.DEFERRED,
            predicate=pred,
            is_multi_valued=is_multi_valued_predicate(pred),
            is_temporally_disjoint=False,
            rationale="Missing discriminating evidence: deferring action until evidence acquisition.",
            requires_evidence_acquisition=True,
            permits_destructive_supersession=False,
        )

    coexist_res = _check_coexistence_rules(assertion_a, assertion_b, pred, val_a, val_b)
    if coexist_res is not None:
        return coexist_res

    if not val_a or not val_b:
        return MaintenanceOutcomeResult(
            outcome_type=MaintenanceOutcomeType.UNKNOWN,
            predicate=pred,
            is_multi_valued=False,
            is_temporally_disjoint=False,
            rationale="Inconclusive values: cannot establish contradiction without discriminative evidence.",
            requires_evidence_acquisition=True,
            permits_destructive_supersession=False,
        )

    return MaintenanceOutcomeResult(
        outcome_type=MaintenanceOutcomeType.CONTRADICTION,
        predicate=pred,
        is_multi_valued=False,
        is_temporally_disjoint=False,
        rationale=f"Single-valued predicate '{pred}' has conflicting values ('{val_a}' vs '{val_b}') across overlapping validity intervals.",
        permits_destructive_supersession=True,
    )
