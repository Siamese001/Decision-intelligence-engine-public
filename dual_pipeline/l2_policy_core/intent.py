"""Request intent analysis and materiality classification for L2 policy."""
from __future__ import annotations

import re

from .constants import (
    _BOUNDED_LOOKUP,
    _BOUNDED_RECORD_LOOKUP,
    _CANDIDATE,
    _COMPARISON,
    _DEFENSE,
    _DIRECT_REASONING_VERB,
    _EXPLANATION,
    _HIGH_MATERIALITY,
    _HOW_EXPLANATION,
    _PRIORITIZATION,
    _RECOMMENDATION,
    _RISK,
    _STRUCTURED_DELIVERABLE,
)
from .models import RequestIntent


def canonical_query_type(query_type: str) -> str:
    return "current_state_corroboration" if query_type == "current_state" else query_type


def analyze_request_intent(question: str) -> RequestIntent:
    normalized = " ".join(question.split())
    component_count = 1 + len(re.findall(r";|\band\s+(?:what|why|how|which)\b", normalized, re.I))
    component_count += max(normalized.count("?") - 1, 0)
    explanation = bool(_EXPLANATION.search(normalized) or _HOW_EXPLANATION.search(normalized))
    comparison = bool(_COMPARISON.search(normalized))
    recommendation = bool(_RECOMMENDATION.search(normalized))
    prioritization = bool(_PRIORITIZATION.search(normalized))
    defense = bool(_DEFENSE.search(normalized))
    risk = bool(_RISK.search(normalized))
    candidate = bool(_CANDIDATE.search(normalized))
    bounded_lookup = bool(_BOUNDED_LOOKUP.search(normalized))
    if _BOUNDED_RECORD_LOOKUP.search(normalized) and not _DIRECT_REASONING_VERB.search(normalized):
        explanation = False
        comparison = False
        recommendation = False
        prioritization = False
        defense = False
        risk = False
        candidate = False
    explicit_semantic = any(
        (explanation, comparison, recommendation, prioritization, defense, risk, candidate)
    )
    if bounded_lookup and not explicit_semantic:
        risk = False
    semantic = any(
        (explanation, comparison, recommendation, prioritization, defense, risk, candidate)
    )
    return RequestIntent(
        explanation=explanation,
        comparison=comparison,
        recommendation=recommendation,
        prioritization=prioritization,
        defense_or_rationale=defense,
        risk_or_opportunity=risk,
        candidate_synthesis=candidate,
        high_materiality=bool(_HIGH_MATERIALITY.search(normalized)),
        structured_deliverable=bool(_STRUCTURED_DELIVERABLE.search(normalized)),
        bounded_factual_state=component_count == 1 and not semantic,
        bounded_lookup=bounded_lookup and not semantic,
        component_count=component_count,
    )


def determine_materiality(
    question: str,
    query_type: str,
    *,
    requested_materiality: str | None = None,
    material_conflict: bool = False,
    route_high_materiality: bool = False,
) -> str:
    if requested_materiality is not None:
        if requested_materiality not in {"low", "medium", "high"}:
            raise ValueError("materiality must be low, medium, or high")
    canonical = canonical_query_type(query_type)
    intent = analyze_request_intent(question)
    if (
        requested_materiality == "high"
        or material_conflict
        or route_high_materiality
        or intent.high_materiality
    ):
        return "high"
    if canonical == "exact_lookup":
        return "low"
    return requested_materiality or "medium"
