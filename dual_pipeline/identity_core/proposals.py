"""Proposals, review profiles, and label options for entity resolution."""
from __future__ import annotations

from typing import Any

from dual_pipeline.core import digest, normalize_name, normalize_space, stable_id

from .records import (
    ENTITY_INTENT_PROFILES_EXTENSION_KEY,
    MINIMUM_RANKED_CANDIDATE_SCORE,
)


def _external_concept_scope(bundle: dict[str, Any]) -> dict[str, str] | None:
    """Return the explicit source scope for a vendor concept, if applicable.

    A first-party A2A document can name a concept that resembles an internal
    workstream, pattern, or tool. That resemblance is not identity evidence.
    The scope check happens before catalog construction/scoring so no internal
    entity is ever exposed as a candidate for those mentions.
    """
    context = bundle.get("extensions", {}).get("com.partner_advisory.nee.context", {})
    source = bundle.get("extensions", {}).get("com.partner_advisory.nee.source", {})
    if not isinstance(context, dict) or not isinstance(source, dict):
        return None
    if (
        context.get("source_kind") != "a2a_fact_vector_packet"
        or context.get("source_posture") != "external_first_party"
    ):
        return None
    vendor = source.get("vendor")
    if not isinstance(vendor, str) or not normalize_space(vendor):
        raise ValueError("external first-party A2A source has no bounded vendor identity")
    source_id = bundle.get("source_id")
    source_version_id = bundle.get("source_version_id")
    if not isinstance(source_id, str) or not source_id or not isinstance(
        source_version_id, str
    ) or not source_version_id:
        raise ValueError("external first-party A2A source has no bounded source identity")
    return {
        "source_id": source_id,
        "source_version_id": source_version_id,
        "vendor": normalize_space(vendor),
    }


def _external_concept_proposal(
    mention: dict[str, Any], scope: dict[str, str]
) -> dict[str, Any]:
    reported_name = normalize_space(mention["reported_text"])
    normalized_label = normalize_name(reported_name)
    if not normalized_label:
        raise ValueError("external concept has no normalized label")
    return {
        "external_concept_id": stable_id(
            "external-concept",
            scope["source_id"],
            scope["vendor"],
            normalized_label,
            length=28,
        ),
        "identity_scope": "source_scoped_external",
        "no_internal_equivalence": True,
        "source_id": scope["source_id"],
        "source_version_id": scope["source_version_id"],
        "vendor": scope["vendor"],
        "reported_name": reported_name,
        "normalized_label": normalized_label,
    }


def _review_confidence(
    *,
    alternatives: list[dict[str, Any]],
    exact_match_count: int,
    outcome: str,
) -> dict[str, Any]:
    """Expose the deterministic identity calculation used for graph admission."""
    top = alternatives[0] if alternatives else None
    top_score = top["match_score"] if top is not None else None
    runner_up_score = alternatives[1]["match_score"] if len(alternatives) > 1 else None
    top_margin = top["margin_over_next"] if top is not None else None
    if outcome in ("AUTO_LINK_EXACT", "AUTO_LINK_HIGH_CONFIDENCE"):
        band = "exact"
    elif top is not None:
        band = "possible_duplicate_evidence"
    else:
        band = "no_ranked_candidate"

    reasons: list[str] = []
    if exact_match_count:
        reasons.append(
            f"exact identity matches: {exact_match_count} "
            f"({'unique' if exact_match_count == 1 else 'non-unique'})"
        )
    elif top is None:
        reasons.append(
            "no catalog candidate met the minimum ranked-candidate score "
            f"({MINIMUM_RANKED_CANDIDATE_SCORE:.2f})"
        )
    else:
        reasons.append(
            "similarity is possible duplicate evidence for human review; it has no merge authority"
        )

    return {
        "band": band,
        "exact_match_count": exact_match_count,
        "ranked_candidate_count": len(alternatives),
        "top_match_score": top_score,
        "runner_up_match_score": runner_up_score,
        "top_margin_over_next": top_margin,
        "top_candidate_has_supporting_context": bool(
            top and top["supporting_context"]
        ),
        "thresholds": {
            "minimum_ranked_candidate_score": MINIMUM_RANKED_CANDIDATE_SCORE,
        },
        "threshold_checks": {
            "top_score_passes_reviewer_candidate_floor": bool(
                top_score is not None
                and top_score >= MINIMUM_RANKED_CANDIDATE_SCORE
            ),
        },
        "similarity_authority": "possible_duplicate_evidence_only",
        "reasons": reasons,
    }


def _label_options(
    *,
    mention_id: str,
    reported_name: str,
    stable_identifier: dict[str, str | None],
    alternatives: list[dict[str, Any]],
    outcome: str,
    new_node_assessment: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return explicit, human-selectable labels instead of a blind failure."""
    options: list[dict[str, Any]] = []
    for alternative in alternatives:
        options.append(
            {
                "option_id": stable_id(
                    "identity-label-option", mention_id, alternative["entity_id"], length=26
                ),
                "decision": "CONFIRM",
                "label_kind": "existing_governed_entity",
                "entity_id": alternative["entity_id"],
                "canonical_name": alternative["canonical_name"],
                "available": True,
                "match_score": alternative["match_score"],
                "margin_over_next": alternative["margin_over_next"],
                "supporting_context": alternative["supporting_context"],
                "identity_conflicts": alternative["identity_conflicts"],
                "graph_effect": "stage_candidate_only_after_human_confirmation",
                "reason": "Candidate label; never an automatic identity link.",
            }
        )

    proposal = (
        new_node_assessment.get("proposal")
        if isinstance(new_node_assessment, dict)
        else None
    )
    proposal_digest = (
        new_node_assessment.get("proposal_digest")
        if isinstance(new_node_assessment, dict)
        else None
    )
    can_create_internal = bool(
        outcome == "CREATE_NEW_ENTITY_PROPOSAL"
        and isinstance(proposal, dict)
        and isinstance(proposal_digest, str)
    )
    options.extend(
        [
            {
                "option_id": stable_id(
                    "identity-label-option", mention_id, "create-new", length=26
                ),
                "decision": "CREATE_NEW",
                "label_kind": "new_internal_candidate_entity",
                "entity_id": proposal.get("entity_id") if can_create_internal else None,
                "canonical_name": (
                    proposal.get("canonical_name") if can_create_internal else reported_name
                ),
                "available": can_create_internal,
                "match_score": None,
                "margin_over_next": None,
                "supporting_context": [],
                "identity_conflicts": [],
                "graph_effect": "stage_candidate_only_after_exact_new_entity_proposal",
                "reason": (
                    f"Approve only the exact source-bound proposal digest {proposal_digest}."
                    if can_create_internal
                    else "Unavailable until the source declares a valid registered node type and stable identifier."
                ),
            },
            {
                "option_id": stable_id("identity-label-option", mention_id, "external", length=26),
                "decision": "CREATE_EXTERNAL_CONCEPT",
                "label_kind": "source_scoped_external_concept",
                "entity_id": None,
                "canonical_name": reported_name,
                "available": outcome == "CREATE_EXTERNAL_CONCEPT",
                "match_score": None,
                "margin_over_next": None,
                "supporting_context": [],
                "identity_conflicts": [],
                "graph_effect": "stage_candidate_only_after_exact_external_concept_decision",
                "reason": (
                    "The package already carries a bounded source-scoped external identity."
                    if outcome == "CREATE_EXTERNAL_CONCEPT"
                    else (
                        "Requires an explicitly source-scoped external intake package; "
                        "review cannot relabel an ordinary package as external."
                    )
                ),
            },
            {
                "option_id": stable_id("identity-label-option", mention_id, "hold", length=26),
                "decision": "HOLD",
                "label_kind": "no_graph_label",
                "entity_id": None,
                "canonical_name": None,
                "available": True,
                "match_score": None,
                "margin_over_next": None,
                "supporting_context": [],
                "identity_conflicts": [],
                "graph_effect": "no_graph_write",
                "reason": "Retain source-bound review evidence without adding a graph label.",
            },
            {
                "option_id": stable_id("identity-label-option", mention_id, "reject", length=26),
                "decision": "REJECT",
                "label_kind": "reject_source_candidate",
                "entity_id": None,
                "canonical_name": None,
                "available": outcome != "AUTO_LINK_EXACT",
                "match_score": None,
                "margin_over_next": None,
                "supporting_context": [],
                "identity_conflicts": [],
                "graph_effect": "no_graph_write",
                "reason": "Reject this source candidate without creating or linking a graph node.",
            },
        ]
    )
    return options


def _identity_review_profile(
    *,
    mention_id: str,
    reported_name: str,
    stable_identifier: dict[str, str | None],
    alternatives: list[dict[str, Any]],
    exact_match_count: int,
    outcome: str,
    decision_basis: list[str],
    new_node_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "profile_version": "1.0.0",
        "automated_outcome": outcome,
        "decision_basis": decision_basis,
        "confidence": _review_confidence(
            alternatives=alternatives,
            exact_match_count=exact_match_count,
            outcome=outcome,
        ),
        "label_options": _label_options(
            mention_id=mention_id,
            reported_name=reported_name,
            stable_identifier=stable_identifier,
            alternatives=alternatives,
            outcome=outcome,
            new_node_assessment=new_node_assessment,
        ),
    }


def _intent_profile(bundle: dict[str, Any], mention: dict[str, Any]) -> dict[str, Any]:
    raw_profiles = bundle.get("extensions", {}).get(
        ENTITY_INTENT_PROFILES_EXTENSION_KEY, {}
    )
    if not isinstance(raw_profiles, dict):
        raise ValueError("entity intent profiles extension must be an object")
    raw = raw_profiles.get(mention["mention_id"])
    if raw is None:
        return {
            "stable_identifier": mention.get("stable_identifier"),
            "entity_intent": "unknown",
            "node_type": None,
            "declaration_status": "MISSING",
            "source_profile_digest": digest(
                {
                    "profile": None,
                    "source_id": bundle["source_id"],
                    "mention_id": mention["mention_id"],
                }
            ),
        }
    required = {
        "stable_identifier",
        "entity_intent",
        "node_type",
        "declaration_status",
        "source_profile_digest",
    }
    if not isinstance(raw, dict) or not required.issubset(raw):
        raise ValueError("mention-bound entity intent profile has an invalid shape")
    if raw["stable_identifier"] != mention.get("stable_identifier"):
        raise ValueError("mention-bound entity intent profile changed the source identifier")
    if raw["entity_intent"] not in {"source_only", "resolve_or_create", "unknown"}:
        raise ValueError("mention-bound entity intent profile has an invalid intent")
    return dict(raw)


def _new_node_assessment(
    *,
    bundle: dict[str, Any],
    mention: dict[str, Any],
    profile: dict[str, Any],
    group: dict[str, Any],
    valid_node_types: list[str],
    status: str,
    alternatives: list[dict[str, Any]],
    reasons: list[str],
) -> dict[str, Any]:
    proposal: dict[str, Any] | None = None
    declared_type = profile.get("node_type")
    stable_identifier = profile.get("stable_identifier")
    if status == "PROPOSE_NEW_CANDIDATE":
        import re as _re
        canonical_sid = (
            _re.sub(r"[\s_]+", "-", str(stable_identifier).strip().lower())
            if stable_identifier
            else stable_identifier
        )
        if profile.get("cross_source_dedup") is True:
            entity_id = stable_id(
                "entity",
                declared_type,
                canonical_sid,
                length=28,
            )
        else:
            entity_id = stable_id(
                "entity",
                declared_type,
                bundle["source_id"],
                canonical_sid,
                length=28,
            )
        proposal = {
            "entity_id": entity_id,
            "canonical_name": normalize_space(mention["reported_text"]),
            "node_type": declared_type,
            "description": normalize_space(mention["context"]),
            "status": "candidate",
            "source_id": bundle["source_id"],
            "source_version_id": bundle["source_version_id"],
            "source_stable_identifier": stable_identifier,
            "source_profile_digest": profile["source_profile_digest"],
            "evidence_ids": group["evidence_ids"],
            "affected_candidate_ids": group["candidate_ids"],
            "possible_duplicate_labels": [
                {
                    "entity_id": value["entity_id"],
                    "canonical_name": value["canonical_name"],
                    "match_score": value["match_score"],
                }
                for value in alternatives
            ],
        }
    return {
        "assessment_version": "1.0.0",
        "group_id": group["group_id"],
        "status": status,
        "entity_intent": profile["entity_intent"],
        "declared_node_type": declared_type,
        "source_stable_identifier": stable_identifier,
        "source_profile_digest": profile["source_profile_digest"],
        "valid_node_type_choices": valid_node_types,
        "affected_candidate_ids": group["candidate_ids"],
        "evidence_ids": group["evidence_ids"],
        "source_locations": group["source_locations"],
        "similarity_role": "possible_duplicate_evidence_only",
        "reasons": reasons,
        "proposal": proposal,
        "proposal_digest": digest(proposal) if proposal is not None else None,
    }
