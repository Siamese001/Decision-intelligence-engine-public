"""Deterministic routing for candidates."""

from __future__ import annotations

from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import stable_id, utc_now

from .constants import EXTERNAL_CONCEPT_OUTCOME
from .graph_delta_distiller import is_portfolio_allocation_relationship
from .proposals import _candidate_resolution_map


def _is_role_change_candidate(candidate: dict[str, Any]) -> bool:
    return bool(
        candidate.get("has_role_change")
        or candidate.get("has_role_resolution")
        or candidate.get("assertion_type") in ("speaker_role", "role_assignment")
        or candidate.get("candidate_type") in ("role_assertion", "role_change")
        or (
            candidate.get("candidate_type") == "architecture_relationship"
            and (
                " HAS_ROLE_IN " in str(candidate.get("normalized_text", ""))
                or " HAS_ROLE " in str(candidate.get("normalized_text", ""))
            )
        )
    )


def _route_exact_match(
    candidate: dict[str, Any], comparison: dict[str, Any]
) -> tuple[str, list[str]] | None:
    exact_matches = comparison.get("exact_matches", [])
    if not exact_matches:
        return None
    matched_sources = {m.get("source_id") for m in exact_matches if m.get("source_id")}
    candidate_source = candidate.get("source_id")
    is_new_evidence = (
        candidate.get("has_new_evidence")
        or bool(candidate.get("corroborating_evidence"))
        or (candidate_source and any(s != candidate_source for s in matched_sources))
        or bool(comparison.get("corroborating_evidence"))
    )
    if is_new_evidence and candidate.get("candidate_type") in {
        "assertion",
        "decision",
        "risk",
        "dependency",
        "source_chunk",
        "architecture_relationship",
        "relationship",
    }:
        return "CORROBORATING_EVIDENCE", [
            "corroborating evidence for existing assertion from distinct source"
        ]
    return "EXACT_DUPLICATE", ["exact normalized content match"]


def _route_partial_overlap(
    candidate: dict[str, Any], comparison: dict[str, Any]
) -> tuple[str, list[str]] | None:
    if not comparison.get("partial_overlaps") and not comparison.get("contradiction_detected"):
        return None
    if candidate.get("candidate_type") == "entity_mention":
        return "CORROBORATING_EVIDENCE", ["corroborating entity mention across meetings"]
    if is_portfolio_allocation_relationship(candidate) and not comparison.get("contradiction_detected"):
        return None
    if comparison.get("perspective_owner_shift"):
        return "CANDIDATE_PROMOTION_REVIEW", [
            "cross-perspective owner or lead shift requires operator review"
        ]
    return "CONTRADICTION", ["partial content overlap requires contradiction review"]


def _route_unresolved_identities(
    resolutions: list[dict[str, Any]],
) -> tuple[str, list[str]] | None:
    if not resolutions:
        return None
    if all(value["outcome"] == "SOURCE_ONLY" for value in resolutions):
        return "SOURCE_ONLY", [
            "source profile explicitly retains evidence without a knowledge node"
        ]
    unresolved = [
        value
        for value in resolutions
        if value["outcome"] not in {"AUTO_LINK_EXACT", "AUTO_LINK_HIGH_CONFIDENCE", "SOURCE_ONLY"}
    ]
    if not unresolved:
        return None
    if any(value["outcome"] == "CREATE_NEW_ENTITY_PROPOSAL" for value in unresolved):
        return "NEW_ENTITY", [
            "complete source-bound new-node proposal requires exact human approval"
        ]
    if any(value["outcome"] == "HITL_CONFIRM_ENTITY" for value in unresolved):
        return "ENTITY_ALIAS_REVIEW", ["identity requires human confirmation"]
    if any(value["outcome"] == EXTERNAL_CONCEPT_OUTCOME for value in unresolved):
        return "EXTERNAL_VALIDATION", [
            "source-scoped external concept requires an explicit creation decision"
        ]
    return "QUARANTINE", ["identity is not resolved or bounded"]


_DEFAULT_CANDIDATE_ROUTES: dict[str, tuple[str, list[str]]] = {
    "entity_mention": ("CORROBORATING_EVIDENCE", ["entity mention resolved"]),
    "architecture_component": ("NEW_ENTITY", ["new architecture component"]),
    "architecture_relationship": ("NEW_RELATIONSHIP", ["new architecture relationship"]),
    "action": ("ACTION_ITEM", ["explicit action semantics"]),
    "open_question": ("QUARANTINE", ["open ambiguity requires evidence"]),
    "relationship": ("NEW_RELATIONSHIP", ["relationship candidate"]),
}


def _route_for_candidate(
    candidate: dict[str, Any],
    resolutions: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> tuple[str, list[str]]:
    if candidate.get("document_signal_disposition") == "SOURCE_ONLY":
        return "SOURCE_ONLY", ["document signal router classified candidate as source-only boilerplate"]

    cand_source = candidate.get("source_id")
    same_source_exact = bool(
        cand_source
        and any(m.get("source_id") == cand_source for m in comparison.get("exact_matches", []))
    )
    if _is_role_change_candidate(candidate) and not same_source_exact:
        return "NEW_ASSERTION", ["explicit supported role assertion change for identity"]

    exact_route = _route_exact_match(candidate, comparison)
    if exact_route is not None:
        return exact_route

    overlap_route = _route_partial_overlap(candidate, comparison)
    if overlap_route is not None:
        return overlap_route

    unresolved_route = _route_unresolved_identities(resolutions)
    if unresolved_route is not None:
        return unresolved_route

    return _DEFAULT_CANDIDATE_ROUTES.get(
        candidate["candidate_type"], ("NEW_ASSERTION", ["novel assertion candidate"])
    )


def build_routes(
    bundle: dict[str, Any],
    resolutions: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> list[dict[str, Any]]:
    by_candidate = _candidate_resolution_map(bundle, resolutions)
    values: list[dict[str, Any]] = []
    for candidate in bundle["candidates"]:
        candidate_id = candidate["candidate_id"]
        cand_comp = comparison["comparisons"][candidate_id]
        candidate_with_source = (
            candidate
            if candidate.get("source_id")
            else {**candidate, "source_id": bundle.get("source_id")}
        )
        route, reasons = _route_for_candidate(
            candidate_with_source, by_candidate[candidate_id], cand_comp
        )
        artifact_id = stable_id("route-decision", bundle["run_id"], candidate_id, length=26)
        lanes = (
            []
            if route
            in {
                "EXACT_DUPLICATE",
                "QUARANTINE",
                "ENTITY_ALIAS_REVIEW",
                "EXTERNAL_VALIDATION",
                "SOURCE_ONLY",
            }
            else ["candidate"]
        )
        if "approved" in lanes:
            raise PermissionError(
                "L0_LANE_LEAKAGE: ingestion routes are strictly forbidden from granting allowed_lanes=['approved']. "
                "Only UWG promotion receipts may authorize approved lane writes."
            )
        route_ext: dict[str, Any] = {}
        if route == "CORROBORATING_EVIDENCE" and cand_comp.get("exact_matches"):
            route_ext["com.partner_advisory.nee.corroborate-assertion-id"] = cand_comp["exact_matches"][0]["object_id"]
        values.append(
            create_artifact(
                "route-decision",
                artifact_id,
                decision_id=artifact_id,
                run_id=bundle["run_id"],
                trace_id=bundle["trace_id"],
                use_case="intake",
                subject_id=candidate_id,
                intake_route=route,
                query_route=None,
                allowed_lanes=lanes,
                materiality=candidate["materiality"],
                execution_form="MANAGED_WORKFLOW" if route in {"ENTITY_ALIAS_REVIEW", "EXTERNAL_VALIDATION", "CANDIDATE_PROMOTION_REVIEW"} else "SINGLE_STEP",
                rule_id=f"intake-route-{route.casefold().replace('_', '-')}",
                rule_version="1.0.0",
                reason_codes=reasons,
                model_recommendation=None,
                decided_at=utc_now(),
                extensions=route_ext,
            )
        )
    if len(values) != len(bundle["candidates"]):
        raise ValueError("each candidate must receive exactly one route")
    return values
