"""Lexical matching and scoring algorithms for entity candidates."""
from __future__ import annotations

import difflib
import re
from typing import Any

from dual_pipeline.core import normalize_name
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

from .records import EntityRecord


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _initial_match(reported: str, canonical: str) -> bool:
    reported_tokens = re.findall(r"[a-z0-9]+", reported.casefold())
    canonical_tokens = re.findall(r"[a-z0-9]+", canonical.casefold())
    if len(reported_tokens) != 2 or len(canonical_tokens) < 2:
        return False
    first, second = reported_tokens
    return canonical_tokens[0].startswith(first) and canonical_tokens[-1].startswith(second[:1])


def _is_first_name_stakeholder_match(
    reported_tokens: set[str], canonical_tokens: set[str], record: EntityRecord
) -> bool:
    if len(reported_tokens) != 1 or len(canonical_tokens) < 2:
        return False
    if record.node_type not in {"stakeholder", "person", "internal_person"}:
        return False
    first_canon = re.findall(r"[a-z0-9]+", record.canonical_name.casefold())
    return bool(first_canon and next(iter(reported_tokens)) == first_canon[0] and len(first_canon[0]) >= 4)


def _score(
    reported: str,
    record: EntityRecord,
    context: str,
    workstream: str | None,
) -> tuple[float, list[str], list[str]]:
    reported_clean = re.sub(r"\s*--.*$", "", reported).strip() or reported
    normalized_reported = normalize_name(reported_clean)
    ratio = difflib.SequenceMatcher(None, normalized_reported, record.normalized_name).ratio()
    reported_tokens = _tokens(reported_clean)
    canonical_tokens = _tokens(record.canonical_name)
    supporting: list[str] = []
    conflicts: list[str] = []
    score = ratio
    if reported_tokens and (reported_tokens <= canonical_tokens or canonical_tokens <= reported_tokens):
        score = max(score + 0.18, 0.91 if _is_first_name_stakeholder_match(reported_tokens, canonical_tokens, record) else 0.0)
        supporting.append("Reported tokens occur in the canonical name")
    if _initial_match(reported, record.canonical_name):
        score += 0.14
        supporting.append("First-name and surname-initial pattern")
    context_tokens = _tokens(context)
    record_context = _tokens(record.description) | canonical_tokens | {record.node_type}
    overlap = sorted(context_tokens & record_context)
    if overlap:
        score += min(0.12, len(overlap) * 0.025)
        supporting.append(f"Context overlap: {', '.join(overlap[:5])}")
    if workstream and record.node_type == "workstream":
        workstream_tokens = _tokens(workstream)
        if workstream_tokens & canonical_tokens:
            score += 0.18
            supporting.append("Workstream-type context agrees")
    if "workstream" in context.casefold() and record.node_type != "workstream":
        score -= 0.08
        conflicts.append("Context indicates a workstream but candidate has another entity type")
    return max(0.0, min(1.0, score)), supporting, conflicts


def _stable_identifier(value: str | None) -> dict[str, str | None]:
    if not value:
        return {"kind": None, "value": None}
    kind = "email" if "@" in value else "system_id"
    return {"kind": kind, "value": value}


_COMPATIBLE_NODE_TYPE_FAMILIES: tuple[set[str], ...] = (
    {"stakeholder", "internal_person", "person", "role_group"},
    {"program", "workstream", "strategy", "opportunity"},
    {"technical_anchor", "capability", "pattern"},
    {"deliverable", "sow", "pricing_model", "operating_model"},
)


def are_node_types_compatible(type_a: str | None, type_b: str | None) -> bool:
    """Return True if two node types are identical or belong to the same compatible family."""
    if not type_a or not type_b:
        return True
    if type_a == type_b:
        return True
    return any(type_a in family and type_b in family for family in _COMPATIBLE_NODE_TYPE_FAMILIES)


def rank_mention_candidates(
    catalog: Any,
    reported: str,
    declared_type: str | None,
    context: str,
    workstream: str | None,
    candidate_ids: list[str],
) -> list[dict[str, Any]]:
    """Score and rank candidate entities from the catalog."""
    ranked: list[tuple[float, EntityRecord, list[str], list[str]]] = []
    for entity_id in candidate_ids:
        record = catalog.entities[entity_id]
        if declared_type and not are_node_types_compatible(record.node_type, declared_type):
            continue
        score, supporting, conflicts = _score(reported, record, context, workstream)
        if score >= 0.28:
            ranked.append((score, record, supporting, conflicts))
    ranked.sort(key=lambda item: (-item[0], item[1].canonical_name, item[1].entity_id))
    ranked = ranked[:5]
    alternatives: list[dict[str, Any]] = []
    for index, (score, record, supporting, conflicts) in enumerate(ranked):
        next_score = ranked[index + 1][0] if index + 1 < len(ranked) else None
        margin = None if next_score is None else max(0.0, score - next_score)
        alternatives.append(
            {
                "entity_id": record.entity_id,
                "canonical_name": record.canonical_name,
                "match_score": round(score, 4),
                "supporting_context": supporting,
                "uniqueness": "unknown",
                "margin_over_next": None if margin is None else round(margin, 4),
                "identity_conflicts": conflicts,
            }
        )
    return alternatives


def _check_auto_link_alternative(
    exact_matches: list[str], alternatives: list[dict[str, Any]] | None, profile: dict[str, Any]
) -> tuple[str, list[str]] | None:
    if exact_matches or not alternatives:
        return None
    if (
        profile.get("mutation_intent") != "UPDATE"
        and not profile.get("allow_high_confidence_link")
        and profile.get("entity_intent") == "resolve_or_create"
    ):
        return None
    top = alternatives[0]
    top_score = float(top.get("match_score") or 0.0)
    top_margin = top.get("margin_over_next")
    if (
        top_score >= 0.90
        and (top_margin is None or float(top_margin) >= 0.25)
        and bool(top.get("supporting_context"))
        and not top.get("identity_conflicts")
    ):
        return "AUTO_LINK_HIGH_CONFIDENCE", [
            f"High-confidence lexical/contextual match ({top_score:.2f}) with unambiguous margin",
        ]
    return None


def determine_resolution_outcome(
    *,
    type_conflict: bool,
    exact_matches: list[str],
    profile: dict[str, Any],
    declared_type_valid: bool,
    stable_identifier: str | None,
    alternatives: list[dict[str, Any]] = None,
) -> tuple[str, list[str]]:
    """Determine resolution outcome and explanatory basis strings."""
    if type_conflict:
        return "HITL_CONFIRM_ENTITY", [
            "The exact source identifier or name collides with another registered node type",
            "A human identity decision is required; similarity cannot resolve a type collision",
        ]
    if len(exact_matches) > 1:
        return "HITL_CONFIRM_ENTITY", ["Exact identity is non-unique", "Human confirmation is required"]

    auto_link = _check_auto_link_alternative(exact_matches, alternatives, profile)
    if auto_link is not None:
        return auto_link

    if profile.get("entity_intent") == "resolve_or_create":
        if not declared_type_valid:
            return "HITL_CONFIRM_ENTITY", [
                "The declared node type is missing, inactive, or excluded from generic creation",
                "A new source-bound package must select an active registry-backed type",
            ]
        if not stable_identifier:
            return "HITL_CONFIRM_ENTITY", [
                "The source did not provide a stable identifier for a new node",
                "Creation is unavailable until the source package is corrected",
            ]
        return "CREATE_NEW_ENTITY_PROPOSAL", [
            "No unique exact existing identity was found",
            "The source declares an active registered node type and stable identifier",
            "Similar names are retained only as possible duplicate evidence",
        ]

    if alternatives and len(alternatives) >= 2:
        top_margin = alternatives[0].get("margin_over_next")
        if top_margin is not None and top_margin < get_hitl_ambiguity_margin_threshold():
            return "HITL_CONFIRM_ENTITY", [
                "15% Ambiguity Margin Gate: Confidence margin between top two candidates is < 15%.",
                "Human confirmation is explicitly required for definitive choice."
            ]
    return "HITL_CONFIRM_ENTITY", [
        "No unique exact existing identity was found",
        "The source has no declared node type, so a new node cannot be proposed",
        "Similar names are reviewer candidates only and cannot auto-link",
    ]


def determine_assessment_status(
    *,
    outcome: str,
    type_conflict: bool,
    exact_count: int,
    profile: dict[str, Any],
    declared_type_valid: bool,
) -> str:
    """Determine assessment status enum."""
    if outcome in ("AUTO_LINK_EXACT", "AUTO_LINK_HIGH_CONFIDENCE"):
        return "EXACT_EXISTING_LINK"
    if type_conflict or exact_count > 1:
        return "IDENTITY_OR_TYPE_COLLISION"
    if outcome == "CREATE_NEW_ENTITY_PROPOSAL":
        return "PROPOSE_NEW_CANDIDATE"
    if profile.get("entity_intent") != "resolve_or_create" or not declared_type_valid:
        return "TYPE_REVIEW_REQUIRED"
    return "IDENTITY_REVIEW_REQUIRED"


def resolve_external_mention(
    *,
    artifact_id: str,
    bundle: dict[str, Any],
    mention: dict[str, Any],
    reported: str,
    context: dict[str, Any],
    external_scope: dict[str, str],
    stable_identifier_artifact: dict[str, str | None],
) -> dict[str, Any]:
    from dual_pipeline.contracts import create_artifact
    from dual_pipeline.core import utc_now

    from .proposals import _external_concept_proposal, _identity_review_profile
    from .records import IDENTITY_REVIEW_EXTENSION_KEY

    proposal = _external_concept_proposal(mention, external_scope)
    outcome = "CREATE_EXTERNAL_CONCEPT"
    basis = [
        "First-party vendor concept is source-scoped",
        "No internal equivalence or fuzzy identity link is asserted",
        "Explicit human creation decision is required",
    ]
    return create_artifact(
        "entity-resolution",
        artifact_id,
        resolution_id=artifact_id,
        run_id=bundle["run_id"],
        trace_id=bundle["trace_id"],
        mention_id=mention["mention_id"],
        reported_name=reported,
        stable_identifier=stable_identifier_artifact,
        context=context,
        alternatives=[],
        selected_entity_id=None,
        outcome=outcome,
        decision_basis=basis,
        requires_hitl=True,
        decided_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.external-concept-proposal": proposal,
            IDENTITY_REVIEW_EXTENSION_KEY: _identity_review_profile(
                mention_id=mention["mention_id"],
                reported_name=reported,
                stable_identifier=stable_identifier_artifact,
                alternatives=[],
                exact_match_count=0,
                outcome=outcome,
                decision_basis=basis,
            ),
        },
    )


def resolve_source_only_mention(
    *,
    artifact_id: str,
    bundle: dict[str, Any],
    mention: dict[str, Any],
    reported: str,
    context: dict[str, Any],
    profile: dict[str, Any],
    group: dict[str, Any],
    valid_types: list[str],
    stable_identifier_artifact: dict[str, str | None],
) -> dict[str, Any]:
    from dual_pipeline.contracts import create_artifact
    from dual_pipeline.core import utc_now

    from .proposals import _identity_review_profile, _new_node_assessment
    from .records import IDENTITY_REVIEW_EXTENSION_KEY, NEW_NODE_ASSESSMENT_EXTENSION_KEY

    basis = [
        "The source explicitly marks this anchor as source_only",
        "No existing identity lookup or knowledge-node proposal is authorized",
    ]
    assessment = _new_node_assessment(
        bundle=bundle,
        mention=mention,
        profile=profile,
        group=group,
        valid_node_types=valid_types,
        status="SOURCE_ONLY",
        alternatives=[],
        reasons=basis,
    )
    return create_artifact(
        "entity-resolution",
        artifact_id,
        resolution_id=artifact_id,
        run_id=bundle["run_id"],
        trace_id=bundle["trace_id"],
        mention_id=mention["mention_id"],
        reported_name=reported,
        stable_identifier=stable_identifier_artifact,
        context=context,
        alternatives=[],
        selected_entity_id=None,
        outcome="SOURCE_ONLY",
        decision_basis=basis,
        requires_hitl=False,
        decided_at=utc_now(),
        extensions={
            NEW_NODE_ASSESSMENT_EXTENSION_KEY: assessment,
            IDENTITY_REVIEW_EXTENSION_KEY: _identity_review_profile(
                mention_id=mention["mention_id"],
                reported_name=reported,
                stable_identifier=stable_identifier_artifact,
                alternatives=[],
                exact_match_count=0,
                outcome="SOURCE_ONLY",
                decision_basis=basis,
                new_node_assessment=assessment,
            ),
        },
    )
