"""Entity resolution algorithms for mentions and bundles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import normalize_name, normalize_space, stable_id, utc_now
from dual_pipeline.node_type_policy import active_creatable_node_types

from .catalog import GraphEntityCatalog
from .matching import (
    _stable_identifier,
    determine_assessment_status,
    determine_resolution_outcome,
    rank_mention_candidates,
    resolve_external_mention,
    resolve_source_only_mention,
)
from .proposals import (
    _external_concept_scope,
    _identity_review_profile,
    _intent_profile,
    _new_node_assessment,
)
from .records import (
    IDENTITY_REVIEW_EXTENSION_KEY,
    NEW_NODE_ASSESSMENT_EXTENSION_KEY,
)


def _deduplicate_exact_matches(
    catalog: GraphEntityCatalog,
    exact_matches: list[str],
    declared_type: str | None,
) -> tuple[list[str], bool]:
    from .matching import are_node_types_compatible
    if len(exact_matches) > 1 and declared_type in ("role_group", "role", "technical_anchor", "capability", "stakeholder", "person", "internal_person"):
        exact_matches = sorted(
            exact_matches,
            key=lambda eid: (0 if getattr(catalog.entities.get(eid), "status", "") == "canonical" else 1, eid),
        )[:1]
    type_conflict = bool(
        declared_type
        and exact_matches
        and any(not are_node_types_compatible(catalog.entities[value].node_type, declared_type) for value in exact_matches)
    )
    return exact_matches, type_conflict


def resolve_mention(
    catalog: GraphEntityCatalog | None,
    bundle: dict[str, Any],
    mention: dict[str, Any],
    *,
    external_scope: dict[str, str] | None = None,
    intent_profile: dict[str, Any] | None = None,
    identity_group: dict[str, Any] | None = None,
    valid_node_types: list[str] | None = None,
) -> dict[str, Any]:
    context_extension = bundle.get("extensions", {}).get("com.partner_advisory.nee.context", {})
    attendees = list(dict.fromkeys(context_extension.get("attendees", [])))
    workstream = context_extension.get("workstream")
    reported = normalize_space(mention["reported_text"])
    stable_identifier = mention.get("stable_identifier")
    context = {
        "attendees": attendees,
        "workstream": workstream,
        "role": None,
        "organization": None,
        "project": workstream,
        "co_occurring_participants": [name for name in attendees if name != reported],
    }
    artifact_id = stable_id("entity-resolution", bundle["run_id"], mention["mention_id"], length=26)
    stable_identifier_artifact = _stable_identifier(stable_identifier)

    if external_scope is not None:
        return resolve_external_mention(
            artifact_id=artifact_id, bundle=bundle, mention=mention, reported=reported,
            context=context, external_scope=external_scope, stable_identifier_artifact=stable_identifier_artifact,
        )

    if catalog is None:
        raise ValueError("an internal entity resolution requires a graph entity catalog")
    profile = intent_profile or _intent_profile(bundle, mention)
    valid_types = valid_node_types or []
    group = identity_group or {
        "group_id": stable_id("identity-decision-group", bundle["source_id"], mention["mention_id"], length=26),
        "candidate_ids": [], "evidence_ids": [], "source_locations": [],
    }
    if profile["entity_intent"] == "source_only":
        return resolve_source_only_mention(
            artifact_id=artifact_id, bundle=bundle, mention=mention, reported=reported,
            context=context, profile=profile, group=group, valid_types=valid_types,
            stable_identifier_artifact=stable_identifier_artifact,
        )

    declared_type = profile.get("node_type")
    exact_matches, exact_basis = catalog.exact(reported, stable_identifier, declared_type=declared_type)
    declared_type_valid = declared_type in valid_types
    exact_matches, type_conflict = _deduplicate_exact_matches(catalog, exact_matches, declared_type)

    if len(exact_matches) == 1 and not type_conflict:
        selected = exact_matches[0]
        record = catalog.entities[selected]
        alternatives = [{
            "entity_id": selected, "canonical_name": record.canonical_name, "match_score": 1.0,
            "supporting_context": [exact_basis.replace("_", " ")], "uniqueness": "unique",
            "margin_over_next": 1.0, "identity_conflicts": [],
        }]
        outcome, basis = "AUTO_LINK_EXACT", [f"Unique {exact_basis.replace('_', ' ')} match"]
    else:
        selected = None
        candidate_ids = exact_matches if exact_matches else list(catalog.entities)
        alternatives = rank_mention_candidates(
            catalog, reported, declared_type, mention["context"], workstream, candidate_ids
        )
        outcome, basis = determine_resolution_outcome(
            type_conflict=type_conflict, exact_matches=exact_matches, profile=profile,
            declared_type_valid=declared_type_valid, stable_identifier=stable_identifier, alternatives=alternatives,
        )
        if outcome == "AUTO_LINK_HIGH_CONFIDENCE" and alternatives:
            selected = alternatives[0]["entity_id"]

    assessment_status = determine_assessment_status(
        outcome=outcome,
        type_conflict=type_conflict,
        exact_count=len(exact_matches),
        profile=profile,
        declared_type_valid=declared_type_valid,
    )
    assessment = _new_node_assessment(
        bundle=bundle,
        mention=mention,
        profile=profile,
        group=group,
        valid_node_types=valid_types,
        status=assessment_status,
        alternatives=alternatives,
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
        alternatives=alternatives,
        selected_entity_id=selected,
        outcome=outcome,
        decision_basis=basis,
        requires_hitl=outcome
        in {"HITL_CONFIRM_ENTITY", "ADD_ALIAS_PROPOSAL", "CREATE_NEW_ENTITY_PROPOSAL"},
        decided_at=utc_now(),
        extensions={
            NEW_NODE_ASSESSMENT_EXTENSION_KEY: assessment,
            IDENTITY_REVIEW_EXTENSION_KEY: _identity_review_profile(
                mention_id=mention["mention_id"],
                reported_name=reported,
                stable_identifier=stable_identifier_artifact,
                alternatives=alternatives,
                exact_match_count=len(exact_matches),
                outcome=outcome,
                decision_basis=basis,
                new_node_assessment=assessment,
            )
        },
    )


def _build_identity_decision_groups(
    bundle: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    mention_profiles: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {}
    for candidate in bundle["candidates"]:
        for mention in candidate["entity_mentions"]:
            profile = _intent_profile(bundle, mention)
            mention_profiles[mention["mention_id"]] = profile
            anchor_key = (
                profile.get("stable_identifier")
                or mention.get("stable_identifier")
                or normalize_name(mention["reported_text"])
            )
            group_id = stable_id(
                "identity-decision-group",
                bundle["source_id"],
                anchor_key,
                profile.get("entity_intent"),
                profile.get("node_type"),
                profile.get("source_profile_digest"),
                length=26,
            )
            group = groups.setdefault(
                group_id,
                {
                    "group_id": group_id,
                    "candidate_ids": [],
                    "evidence_ids": [],
                    "source_locations": [],
                },
            )
            group["candidate_ids"].append(candidate["candidate_id"])
            group["evidence_ids"].extend(candidate["evidence_ids"])
            group["source_locations"].append(candidate["source_locator"])
            profile["group_id"] = group_id
    for group in groups.values():
        group["candidate_ids"] = sorted(set(group["candidate_ids"]))
        group["evidence_ids"] = sorted(set(group["evidence_ids"]))
        group["source_locations"] = sorted(
            {json.dumps(value, sort_keys=True): value for value in group["source_locations"]}.values(),
            key=lambda value: (value.get("kind", ""), value.get("value", "")),
        )
    return mention_profiles, groups


def _resolve_candidate_mentions(
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    catalog: GraphEntityCatalog | None,
    external_scope: dict[str, str] | None,
    mention_profiles: dict[str, dict[str, Any]],
    groups: dict[str, dict[str, Any]],
    valid_node_types: list[str],
) -> list[dict[str, Any]]:
    resolutions = []
    if external_scope is not None:
        for mention in candidate["entity_mentions"]:
            resolutions.append(
                resolve_mention(None, bundle, mention, external_scope=external_scope)
            )
        return resolutions
    for mention in candidate["entity_mentions"]:
        profile = mention_profiles[mention["mention_id"]]
        resolutions.append(
            resolve_mention(
                catalog,
                bundle,
                mention,
                intent_profile=profile,
                identity_group=groups[profile["group_id"]],
                valid_node_types=valid_node_types,
            )
        )
    return resolutions


def _get_candidate_catalog(
    candidate: dict[str, Any],
    graph_path: Path,
    authorized_audiences: list[str] | None,
    catalogs: dict[tuple[str, tuple[str, ...]], GraphEntityCatalog],
) -> GraphEntityCatalog:
    candidate_audiences = (
        sorted(set(candidate["audiences"]) & set(authorized_audiences))
        if authorized_audiences is not None
        else candidate["audiences"]
    )
    catalog_key = ("unfiltered", ()) if authorized_audiences is None else (
        candidate["classification"],
        tuple(sorted(set(candidate_audiences))),
    )
    if catalog_key not in catalogs:
        catalogs[catalog_key] = (
            GraphEntityCatalog(graph_path)
            if authorized_audiences is None
            else GraphEntityCatalog(
                graph_path,
                classification=candidate["classification"],
                audiences=list(catalog_key[1]),
            )
        )
    return catalogs[catalog_key]


def resolve_bundle(
    bundle: dict[str, Any],
    graph_path: Path,
    *,
    authorized_audiences: list[str] | None = None,
) -> list[dict[str, Any]]:
    external_scope = _external_concept_scope(bundle)
    valid_node_types = active_creatable_node_types(graph_path)
    mention_profiles, groups = _build_identity_decision_groups(bundle)
    catalogs: dict[tuple[str, tuple[str, ...]], GraphEntityCatalog] = {}
    resolutions: list[dict[str, Any]] = []

    for candidate in bundle["candidates"]:
        catalog = None
        if external_scope is None:
            catalog = _get_candidate_catalog(
                candidate, graph_path, authorized_audiences, catalogs
            )

        cand_resolutions = _resolve_candidate_mentions(
            bundle,
            candidate,
            catalog,
            external_scope,
            mention_profiles,
            groups,
            valid_node_types,
        )
        resolutions.extend(cand_resolutions)

    mention_ids = {
        mention["mention_id"]
        for candidate in bundle["candidates"]
        for mention in candidate["entity_mentions"]
    }
    resolved_ids = {resolution["mention_id"] for resolution in resolutions}
    if mention_ids != resolved_ids:
        raise ValueError("every entity mention must receive exactly one resolution outcome")
    return resolutions
