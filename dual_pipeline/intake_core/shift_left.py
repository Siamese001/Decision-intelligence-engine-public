"""Shift-left exception digest and decision artifact builders."""

from __future__ import annotations

from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import stable_id, utc_now


def _extract_contradiction_exception(
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    intake_route: str,
    cand_comp: dict[str, Any],
    cand_text: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    cid = candidate["candidate_id"]
    kind = "SUPERSESSION" if intake_route == "SUPERSESSION" else "CONTRADICTION"
    overlaps = cand_comp.get("partial_overlaps", []) or cand_comp.get("exact_matches", [])
    existing_id = overlaps[0].get("object_id") if overlaps else None
    summary = (
        f"State flip / supersession for: {cand_text[:80]}"
        if kind == "SUPERSESSION"
        else f"Contradiction against existing assertion {existing_id or 'in graph'}: {cand_text[:80]}"
    )
    if kind == "CONTRADICTION":
        options = [
            {"decision": "SUPERSEDE", "label": "(Recommended) Supersede prior assertion with new verified fact", "recommended": True},
            {"decision": "KEEP_CANDIDATE", "label": "Keep new claim as candidate without retiring prior fact"},
            {"decision": "QUARANTINE", "label": "Quarantine contradiction for forensic review"},
        ]
    else:
        options = [
            {"decision": "SUPERSEDE", "label": "(Recommended) Apply supersession and retire prior state", "recommended": True},
            {"decision": "KEEP_CANDIDATE", "label": "Keep new claim as candidate"},
        ]
    return {
        "exception_id": stable_id("exc", bundle.get("run_id", "run"), cid, length=24),
        "candidate_id": cid,
        "candidate_ids": [cid],
        "kind": kind,
        "route": intake_route,
        "candidate_type": candidate.get("candidate_type", "assertion"),
        "text": cand_text,
        "evidence_ids": evidence_ids,
        "summary": summary,
        "existing_target_id": existing_id,
        "options": options,
    }


def _extract_alias_exception(
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    intake_route: str,
    resolutions: list[dict[str, Any]],
    cand_text: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    cid = candidate["candidate_id"]
    target_entity = None
    for r in resolutions:
        if r.get("outcome") == "HITL_CONFIRM_ENTITY":
            opts = r.get("extensions", {}).get("com.partner_advisory.nee.identity-review", {}).get("label_options", [])
            if opts:
                target_entity = opts[0].get("target_id")
                break
    return {
        "exception_id": stable_id("exc", bundle.get("run_id", "run"), cid, length=24),
        "candidate_id": cid,
        "candidate_ids": [cid],
        "kind": "ENTITY_ALIAS_REVIEW",
        "route": intake_route,
        "candidate_type": candidate.get("candidate_type", "entity_mention"),
        "text": cand_text or candidate.get("reported_name", ""),
        "evidence_ids": evidence_ids,
        "summary": f"Entity alias review for '{candidate.get('reported_name', cand_text)}'",
        "existing_target_id": target_entity,
        "options": [
            {"decision": "MERGE_ALIAS", "label": f"(Recommended) Merge as alias of existing entity {target_entity or ''}".strip(), "recommended": True},
            {"decision": "CREATE_NEW", "label": "Create separate distinct entity"},
        ],
    }


def _extract_candidate_review_exception(
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    intake_route: str,
    cand_text: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    cid = candidate["candidate_id"]
    return {
        "exception_id": stable_id("exc", bundle.get("run_id", "run"), cid, length=24),
        "candidate_id": cid,
        "candidate_ids": [cid],
        "kind": "CANDIDATE_REVIEW",
        "route": intake_route,
        "candidate_type": candidate.get("candidate_type", "assertion"),
        "text": cand_text,
        "evidence_ids": evidence_ids,
        "summary": f"Candidate review: {cand_text[:80]}",
        "existing_target_id": None,
        "options": [
            {"decision": "PROMOTE_APPROVED", "label": "(Recommended) Promote to approved fact", "recommended": True},
            {"decision": "KEEP_CANDIDATE", "label": "Keep as candidate in graph"},
            {"decision": "REJECT", "label": "Reject candidate"},
        ],
    }


def build_shift_left_exception_digest(
    bundle: dict[str, Any],
    routes: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a shift-left exception digest separating clean Tier-0 claims from true exceptions."""
    route_by_candidate = {value["subject_id"]: value for value in routes}
    disposition_by_candidate = {value["candidate_id"]: value for value in dispositions}
    comparisons = (comparison or {}).get("comparisons", {})

    tier_0_clean: list[str] = []
    exceptions: list[dict[str, Any]] = []

    for candidate in bundle.get("candidates", []):
        cid = candidate["candidate_id"]
        disp = disposition_by_candidate.get(cid, {})
        route_item = route_by_candidate.get(cid, {})
        intake_route = route_item.get("intake_route", "NEW_ASSERTION")
        cand_comp = comparisons.get(cid, {})

        if disp.get("disposition") == "APPROVED" and not disp.get("hitl_required"):
            tier_0_clean.append(cid)
            continue

        cand_text = candidate.get("normalized_text") or candidate.get("exact_text") or candidate.get("action_text") or ""
        evidence_ids = candidate.get("evidence_ids", [])

        if intake_route in {"CONTRADICTION", "SUPERSESSION"} or cand_comp.get("contradiction_detected"):
            exceptions.append(_extract_contradiction_exception(bundle, candidate, intake_route, cand_comp, cand_text, evidence_ids))
        elif intake_route == "ENTITY_ALIAS_REVIEW" or any(
            r.get("outcome") == "HITL_CONFIRM_ENTITY"
            for r in resolutions
            if r.get("mention_id") in [m.get("mention_id") for m in candidate.get("entity_mentions", [])]
        ):
            exceptions.append(_extract_alias_exception(bundle, candidate, intake_route, resolutions, cand_text, evidence_ids))
        elif disp.get("hitl_required") or disp.get("disposition") in {"CANDIDATE", "QUARANTINE"}:
            exceptions.append(_extract_candidate_review_exception(bundle, candidate, intake_route, cand_text, evidence_ids))

    return {
        "run_id": bundle.get("run_id", ""),
        "total_candidates": len(bundle.get("candidates", [])),
        "tier_0_auto_promoted_count": len(tier_0_clean),
        "tier_0_candidate_ids": tier_0_clean,
        "has_exceptions": len(exceptions) > 0,
        "exception_count": len(exceptions),
        "exceptions": exceptions,
    }


def create_shift_left_hitl_decision(
    bundle: dict[str, Any],
    exception_item: dict[str, Any],
    decision_val: str,
    reviewer: str = "operator",
    rationale: str | None = None,
) -> dict[str, Any]:
    """Create a sealed urn:nee:dual-pipeline:hitl-decision:1.0.0 artifact for an exception choice."""
    cid = exception_item["candidate_id"]
    dec_id = stable_id("hitl-decision", bundle.get("run_id", "run"), cid, length=24)
    rev_item_id = exception_item.get("exception_id") or stable_id("review-item", bundle.get("run_id", "run"), cid, length=26)

    mapping = {
        "MERGE_ALIAS": "CONFIRM",
        "KEEP_CANDIDATE": "HOLD",
    }
    effective_decision = mapping.get(decision_val, decision_val)
    ext: dict[str, Any] = {}
    if decision_val == "MERGE_ALIAS" and exception_item.get("existing_target_id"):
        ext["com.partner_advisory.nee.selected-entity-id"] = exception_item["existing_target_id"]
        ext["com.partner_advisory.nee.canonical-alias"] = {
            "entity_id": exception_item["existing_target_id"],
            "alias": exception_item.get("text") or exception_item.get("reported_name", ""),
        }

    return create_artifact(
        "hitl-decision",
        dec_id,
        decision_id=dec_id,
        review_item_id=rev_item_id,
        run_id=bundle.get("run_id", "run"),
        trace_id=bundle.get("trace_id") or ("0" * 32),
        tier=1,
        decision=effective_decision,
        reviewer=reviewer,
        decided_at=utc_now(),
        rationale=rationale or f"Operator selected {decision_val} for exception {cid}",
        evidence_reviewed=exception_item.get("evidence_ids", []),
        alternatives=[opt["decision"] for opt in exception_item.get("options", []) if opt["decision"] != decision_val],
        before_state_digest="0" * 64,
        proposed_after_state_digest="0" * 64,
        affected_candidate_ids=exception_item.get("candidate_ids", [cid]),
        challenger_required=False,
        challenger_id=None,
        extensions=ext,
    )
