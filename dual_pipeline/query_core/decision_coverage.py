"""Decision-aware coverage manifest helpers and rank-1 gap recovery.

Extracts frame obligations, evaluates typed support for decision requirements,
and ranks missing requirements for bounded recovery.
Part of query_core (Layer 4/5).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from dual_pipeline.core import normalize_space
from dual_pipeline.l0.decision_support_contracts import (
    DecisionSupportState,
    evaluate_evidence_support,
)


def extract_frame_coverage_requirements(
    plan: Mapping[str, Any],
    default_lanes: Sequence[str],
) -> list[dict[str, Any]]:
    """Extract sealed decision frame requirements from plan extensions."""
    extensions = plan.get("extensions") or {}
    frame_ext = extensions.get("com.partner_advisory.nee.decision-frame") or {}
    requirements: list[dict[str, Any]] = []

    for freq in frame_ext.get("requirements", []):
        f_label = normalize_space(freq.get("requirement_label", ""))
        if not f_label:
            continue
        requirements.append({
            "requirement_id": freq["requirement_id"],
            "requirement_label": f_label,
            "requirement_kind": freq.get("requirement_kind", "decision_support_obligation"),
            "allowed_lanes": list(freq.get("allowed_lanes") or default_lanes),
            "missing_evidence_disposition": "explicit_gap",
            "materiality": freq.get("materiality", plan.get("materiality", "high")),
            "frame_digest": freq.get("frame_digest"),
            "option_id": freq.get("option_id"),
            "criterion_id": freq.get("criterion_id"),
            "option_label": freq.get("option_label", ""),
            "criterion_label": freq.get("criterion_label", ""),
        })
    return requirements


def match_decision_support_obligation(
    requirement: Mapping[str, Any],
    row: Mapping[str, Any],
) -> tuple[bool, str]:
    """Evaluate whether an evidence row satisfies a decision support obligation."""
    record_obj = row.get("record") if isinstance(row.get("record"), Mapping) else row
    eval_record = dict(record_obj)
    if isinstance(row.get("graph_path"), Mapping) and "graph_path" not in eval_record:
        eval_record["graph_path"] = row["graph_path"]
    option_label = str(requirement.get("option_label") or "")
    criterion_label = str(requirement.get("criterion_label") or "")

    if not option_label or not criterion_label:
        label = str(requirement.get("requirement_label") or "")
        if " satisfies " in label:
            parts = label.split(" satisfies ", 1)
            raw_opt = parts[0]
            if "[" in raw_opt and "]" in raw_opt:
                raw_opt = raw_opt[raw_opt.find("[") + 1 : raw_opt.rfind("]")]
            option_label = raw_opt.strip("[] ")
            criterion_label = parts[1].strip()

    sup_state, _ = evaluate_evidence_support(
        eval_record,
        option_label,
        criterion_label,
    )
    is_supported = sup_state == DecisionSupportState.SUPPORTED
    direction = str(record_obj.get("direction") or record_obj.get("support_direction") or "forward")
    return is_supported, direction


def rank_missing_gap_requirements(
    gap_requirements: Sequence[Mapping[str, Any]],
    requirement_by_id: Mapping[str, Mapping[str, Any]],
    prior_attempts: Optional[Sequence[str]] = None,
) -> list[Mapping[str, Any]]:
    """Rank missing gap requirements for rank-1 bounded recovery.

    Prioritizes:
    1. Attempt novelty (unattempted requirement IDs first)
    2. Materiality (high > medium > low)
    3. Requirement kind (decision obligations > policy > named > facets)
    """
    attempted_set = set(prior_attempts or [])

    def _priority_tuple(gap: Mapping[str, Any]) -> tuple[int, int, int, str]:
        req_id = str(gap.get("requirement_id") or "")
        req = requirement_by_id.get(req_id, {})
        novelty_score = 0 if req_id in attempted_set else 1

        materiality_map = {"high": 3, "medium": 2, "low": 1}
        m_score = materiality_map.get(str(req.get("materiality", "")).lower(), 1)

        kind_map = {
            "decision_support_obligation": 5,
            "decision_constraint_obligation": 4,
            "policy_requirement": 3,
            "named_subject": 2,
            "graph_facet": 1,
        }
        k_score = kind_map.get(str(req.get("requirement_kind", "")), 0)
        label = str(req.get("requirement_label") or "")
        return (-novelty_score, -m_score, -k_score, label)

    return sorted(gap_requirements, key=_priority_tuple)


import re


def _has_row_evidence(row: Any) -> bool:
    rec = row.get("record") if isinstance(row.get("record"), dict) else row
    if bool(row.get("evidence")):
        return True
    return bool(rec.get("evidence_ids", []))


def _dispatch_frame_policy_obligation(
    l_lower: str,
    label: str,
    row: Any,
    requirement: Mapping[str, Any],
    requirement_tokens: set[str],
    record_tokens: set[str],
) -> tuple[bool, str | None] | None:
    if (l_lower.startswith("option [") or l_lower.startswith("[")) and " satisfies " in l_lower:
        return match_decision_support_obligation(requirement, row)
    if l_lower.startswith("time horizoncurrentness [") or l_lower.startswith("temporal validity:"):
        return _match_constraint_or_temporal_obligation(
            "decision_temporal_obligation", label, row, requirement, requirement_tokens, record_tokens
        )
    if l_lower.startswith("constraint satisfaction:"):
        return _match_constraint_or_temporal_obligation(
            "decision_constraint_obligation", label, row, requirement, requirement_tokens, record_tokens
        )
    return None


def _is_policy_relevant(
    label: str,
    lane: str,
    row: Any,
    requirement: Mapping[str, Any],
    requirement_tokens: set[str],
    record_tokens: set[str],
) -> tuple[bool, str | None]:
    l_lower = label.casefold()
    frame_res = _dispatch_frame_policy_obligation(l_lower, label, row, requirement, requirement_tokens, record_tokens)
    if frame_res is not None:
        return frame_res
    if "approved internal facts" in l_lower or "approved guidance" in l_lower:
        return (lane == "approved" and bool(record_tokens)), None
    if any(k in l_lower for k in ("role-separated intent", "candidate hypotheses", "without factual-status leakage")):
        return (lane in {"approved", "candidate", "external_public"} and bool(record_tokens)), None
    if any(k in l_lower for k in ("dated project-status", "as-of date", "project-status snapshot", "explicit candidate labels", "proposal-only handling")):
        return (lane in {"approved", "candidate"} and (bool(record_tokens) or _has_row_evidence(row))), None
    if any(k in l_lower for k in ("evidence", "source locator", "citation-only")):
        return _has_row_evidence(row), None
    return (bool(record_tokens) and _has_row_evidence(row)), None


def _match_constraint_or_temporal_obligation(
    kind: str,
    label: str,
    row: Any,
    requirement: Mapping[str, Any],
    requirement_tokens: set[str],
    record_tokens: set[str],
) -> tuple[bool, str | None]:
    rec = row.get("record") if isinstance(row.get("record"), Mapping) else row
    if kind == "decision_temporal_obligation":
        currentness = str(rec.get("currentness_state") or rec.get("temporal_state") or "current").lower()
        return currentness not in {"stale", "superseded", "archived", "historical"}, None
    if kind == "decision_constraint_obligation":
        constraint_str = str(requirement.get("constraint") or label).casefold()
        c_tokens = {w for w in re.findall(r"[a-z0-9]{3,}", constraint_str) if w not in {"constraint", "satisfaction"}}
        if not c_tokens:
            return bool(record_tokens), None
        return bool(c_tokens & record_tokens) or bool(requirement_tokens & record_tokens), None
    return False, None


def _is_assertion_relevant(label: str, lane: str, row: Any) -> tuple[bool, bool]:
    l_lower = label.casefold()
    if l_lower.startswith("candidate assertions under review"):
        return True, (lane == "candidate" and _has_row_evidence(row))
    if l_lower.startswith("approved corroboration or contradiction"):
        return True, (lane == "approved" and _has_row_evidence(row))
    return False, False


def is_record_relevant_to_requirement(
    kind: str,
    label: str,
    lane: str,
    row: Any,
    requirement: Mapping[str, Any],
    requirement_tokens: set[str],
    record_tokens: set[str],
) -> tuple[bool, str | None]:
    """Determine whether a record row is relevant to a coverage requirement."""
    if kind == "conclusion":
        return bool(record_tokens), None
    if kind == "policy_requirement":
        return _is_policy_relevant(
            label, lane, row, requirement, requirement_tokens, record_tokens
        )
    if kind == "decision_support_obligation":
        rel, dir_m = match_decision_support_obligation(requirement, row)
        return rel, dir_m
    if kind in {"decision_constraint_obligation", "decision_temporal_obligation"}:
        return _match_constraint_or_temporal_obligation(
            kind, label, row, requirement, requirement_tokens, record_tokens
        )

    matched, is_rel = _is_assertion_relevant(label, lane, row)
    if matched:
        return is_rel, None

    overlap = requirement_tokens & record_tokens
    if not overlap:
        return False, None
    if len(overlap) >= 2 or kind == "graph_facet":
        return True, None
    frac = len(overlap) / max(1, len(requirement_tokens))
    return frac >= 0.12, None


__all__ = [
    "extract_frame_coverage_requirements",
    "is_record_relevant_to_requirement",
    "match_decision_support_obligation",
    "rank_missing_gap_requirements",
]
