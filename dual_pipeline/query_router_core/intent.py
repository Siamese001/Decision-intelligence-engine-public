"""Query intent and materiality classification helpers."""
from __future__ import annotations

import re

from ..l2 import canonical_query_type
from .constants import (
    _INDEPENDENT_GENERAL_TARGET,
    _TOP_LEVEL_JOINER,
)
from .models import QueryDestination, QueryRoutingPolicy
from .policy import _lexicon_match, _lexicon_matches


_CANDIDATE_EXCLUSION_COMPLAINT = re.compile(
    r"\b(?:strip(?:ping)?|exclud(?:e|ing|ed)|without|remove|no)\b.{0,40}\bcandidates?\b"
    r"|\bcandidates?\b.{0,40}\b(?:exclud(?:e|ing|ed)|strip(?:ped)?|not\s+be\s+included|never\s+be\s+cited)\b"
    r"|\bcanonical[- ]only\b",
    re.IGNORECASE,
)


def _match_status_or_bounded(policy: QueryRoutingPolicy, question: str) -> str | None:
    if _lexicon_matches(policy, "bounded", question):
        return "exact_lookup"
    if (
        _lexicon_matches(policy, "project_status", question)
        and _lexicon_matches(policy, "material", question)
    ):
        return "relationship_explanation"
    if _lexicon_matches(policy, "project_status", question):
        return (
            "project_status_history"
            if _lexicon_matches(policy, "status_history", question)
            else "project_status_current"
        )
    return None


def _query_type(policy: QueryRoutingPolicy, question: str, override: str | None) -> str:
    """Select a deterministic reasoning intent from policy-bound signals."""

    if override is not None:
        return canonical_query_type(override)
    status_bounded = _match_status_or_bounded(policy, question)
    if status_bounded:
        return status_bounded
    candidate_match = (
        None
        if _CANDIDATE_EXCLUSION_COMPLAINT.search(question)
        else _lexicon_match(policy, "candidate", question)
    )
    candidates = (
        (candidate_match, "candidate_promotion_review"),
        (_lexicon_match(policy, "recommendation", question), "opportunity_nba"),
        (_lexicon_match(policy, "risk", question), "gap_risk_inconsistency"),
        (_lexicon_match(policy, "synthesis", question), "cross_workstream_synthesis"),
        (_lexicon_match(policy, "relationship", question), "relationship_explanation"),
    )
    material_intents = [
        (match, query_type)
        for match, query_type in candidates
        if match is not None and query_type != "relationship_explanation"
    ]
    earliest_material = min(material_intents, key=lambda value: value[0].start(), default=None)
    risk_conclusive = _lexicon_match(policy, "risk_conclusive", question)
    if risk_conclusive and (
        earliest_material is None or risk_conclusive.start() <= earliest_material[0].start()
    ):
        return "gap_risk_inconsistency"
    candidate = candidate_match
    recommendation = _lexicon_match(policy, "recommendation", question)
    risk = _lexicon_match(policy, "risk", question)
    synthesis = _lexicon_match(policy, "synthesis", question)
    relationship = _lexicon_match(policy, "relationship", question)
    explicit_recommendation = _lexicon_match(
        policy, "recommendation_explicit", question
    )
    if (
        synthesis
        and recommendation
        and recommendation.start() < synthesis.start()
        and explicit_recommendation is None
        and not any(
            match is not None and match.start() < synthesis.start()
            for match in (candidate, risk, relationship)
        )
    ):
        return "cross_workstream_synthesis"
    matched = [(match.start(), query_type) for match, query_type in candidates if match]
    if matched:
        precedence = {
            "candidate_promotion_review": 0,
            "gap_risk_inconsistency": 1,
            "opportunity_nba": 2,
            "cross_workstream_synthesis": 3,
            "relationship_explanation": 4,
        }
        return min(matched, key=lambda value: (value[0], precedence[value[1]]))[1]
    from ..system_directory import find_systems_in_text

    if find_systems_in_text(question):
        return "exact_lookup"
    return "current_state_corroboration"


def _materiality(
    policy: QueryRoutingPolicy,
    question: str,
    requested: str | None,
    destination: QueryDestination,
) -> str:
    if requested is not None:
        if requested not in {"low", "medium", "high"}:
            raise ValueError("materiality must be low, medium, or high")
        return requested
    if destination in {
        QueryDestination.GENERAL_ASSISTANT_HANDOFF,
        QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF,
        QueryDestination.GRAPH_EVIDENCE_UTILITY,
    }:
        return "low"
    return "high" if _lexicon_matches(policy, "high_materiality", question) else "medium"


def _joined_independent_actions(first: re.Match[str], second: re.Match[str], question: str) -> bool:
    """Return whether two distinct actions are explicitly joined as peers."""

    earlier, later = sorted((first, second), key=lambda match: match.start())
    return _TOP_LEVEL_JOINER.search(question[earlier.end() : later.start()]) is not None


def _evidence_has_independent_material_action(
    policy: QueryRoutingPolicy, question: str
) -> bool:
    bounded = _lexicon_match(policy, "bounded", question)
    material = _lexicon_match(policy, "independent_material", question)
    return bool(bounded and material and _joined_independent_actions(bounded, material, question))


def _has_joined_semantic_and_handoff(
    policy: QueryRoutingPolicy, handoff_rule: str, question: str
) -> bool:
    """Identify two explicit peer actions without inferring a destination."""

    semantic = _lexicon_match(policy, "material", question)
    handoff = _lexicon_match(policy, handoff_rule, question)
    if not (semantic and handoff and _joined_independent_actions(semantic, handoff, question)):
        return False
    if handoff_rule == "general":
        handoff_match_text = handoff.group(0).lower()
        if handoff_match_text in {
            "summarize",
            "summarise",
            "summarizing",
            "summarising",
            "condense",
            "shorten",
            "polish",
            "edit",
        } or "summary" in handoff_match_text:
            after_handoff = question[handoff.end() :]
            before_handoff = question[: handoff.start()]
            if not (
                _INDEPENDENT_GENERAL_TARGET.search(after_handoff[:40])
                or _INDEPENDENT_GENERAL_TARGET.search(before_handoff[-40:])
            ):
                return False
    return True


__all__ = [
    "_evidence_has_independent_material_action",
    "_has_joined_semantic_and_handoff",
    "_joined_independent_actions",
    "_materiality",
    "_query_type",
]
