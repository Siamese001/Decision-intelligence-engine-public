"""Selective candidate partitioning, advisory subset routing, and deterministic proposal merging."""
from __future__ import annotations

from typing import Any, Mapping

from .constants import ALLOWED_RECOMMENDATIONS, AMBIGUOUS_IDENTITY_OUTCOMES
from .errors import ProposalValidationError
from .models import IngestionReasoningPolicy


def _is_ambiguous_outcome(outcome: str | None) -> bool:
    if not outcome:
        return False
    return (
        outcome in AMBIGUOUS_IDENTITY_OUTCOMES
        or "AMBIGUOUS" in outcome
        or "HITL" in outcome
        or "QUARANTINE" in outcome
    )


def partition_candidates_by_complexity(
    bundle: Mapping[str, Any],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    policy: IngestionReasoningPolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition bundle candidates into advisory-eligible (hard) and deterministic-eligible (easy)."""
    resolution_by_mention = {
        value["mention_id"]: value["outcome"] for value in resolutions
    }
    high_types = set(policy.routing.get("high_candidate_types", []))
    comparisons = comparison.get("comparisons", {})

    hard_candidates: list[dict[str, Any]] = []
    easy_candidates: list[dict[str, Any]] = []

    for candidate in bundle.get("candidates", []):
        cand_id = candidate["candidate_id"]
        is_high_mat = candidate.get("materiality") == "high"
        is_complex_type = candidate.get("candidate_type") in high_types
        has_ambiguous_identity = any(
            _is_ambiguous_outcome(resolution_by_mention.get(m["mention_id"]))
            for m in candidate.get("entity_mentions", [])
        )
        cand_comp = comparisons.get(cand_id, {})
        has_semantic_conflict = len(cand_comp.get("semantic_matches", [])) > 1

        if is_high_mat or is_complex_type or has_ambiguous_identity or has_semantic_conflict:
            hard_candidates.append(dict(candidate))
        else:
            easy_candidates.append(dict(candidate))

    return hard_candidates, easy_candidates


def build_deterministic_recommendation(
    candidate: Mapping[str, Any],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Constructs a deterministic recommendation item for an easy or excluded candidate."""
    cand_id = candidate["candidate_id"]
    evidence_ids = sorted(candidate.get("evidence_ids", []))
    if not evidence_ids:
        raise ProposalValidationError([f"Candidate {cand_id} has no evidence IDs"])

    resolution_by_mention = {
        value["mention_id"]: value["outcome"] for value in resolutions
    }
    has_ambiguous = any(
        _is_ambiguous_outcome(resolution_by_mention.get(m["mention_id"]))
        for m in candidate.get("entity_mentions", [])
    )

    if has_ambiguous:
        rec = "HITL_REQUIRED"
        reason_codes = ["SPEAKER_AMBIGUITY_HITL_REQUIRED"]
        question = f"Operator review required for ambiguous identity in candidate {cand_id}"
    else:
        cand_comp = comparison.get("comparisons", {}).get(cand_id, {})
        if cand_comp.get("exact_matches"):
            rec = "REJECTED_NOOP"
            reason_codes = ["DETERMINISTIC_DUPLICATE_NOOP"]
        else:
            rec = "CANDIDATE"
            reason_codes = ["DETERMINISTIC_ROUTED"]
        question = None

    return {
        "candidate_id": cand_id,
        "recommendation": rec,
        "evidence_ids": evidence_ids,
        "reason_codes": reason_codes,
        "reviewer_question": question,
    }


def merge_advisory_and_deterministic_proposals(
    *,
    advisory_recommendations: list[Mapping[str, Any]],
    deterministic_candidates: list[Mapping[str, Any]],
    all_bundle_candidates: list[Mapping[str, Any]],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Merge model advisory recommendations with deterministic recommendations,
    ensuring every original candidate is covered exactly once and speaker ambiguity remains HITL-controlled."""
    resolution_by_mention = {
        value["mention_id"]: value["outcome"] for value in resolutions
    }
    expected_ids = {c["candidate_id"] for c in all_bundle_candidates}
    advisory_by_id = {r["candidate_id"]: dict(r) for r in advisory_recommendations}

    merged_items: list[dict[str, Any]] = []

    for cand in all_bundle_candidates:
        cand_id = cand["candidate_id"]
        if cand_id in advisory_by_id:
            item = dict(advisory_by_id[cand_id])
            # Invariant: Speaker ambiguity remains human-controlled; advisory cannot override to active/admitted
            has_ambiguous = any(
                _is_ambiguous_outcome(resolution_by_mention.get(m["mention_id"]))
                for m in cand.get("entity_mentions", [])
            )
            if has_ambiguous and item.get("recommendation") not in {"HITL_REQUIRED", "QUARANTINE"}:
                item["recommendation"] = "HITL_REQUIRED"
                if "SPEAKER_AMBIGUITY_HITL_REQUIRED" not in item.get("reason_codes", []):
                    item["reason_codes"] = ["SPEAKER_AMBIGUITY_HITL_REQUIRED"] + item.get("reason_codes", [])[:7]
            merged_items.append(item)
        else:
            # Deterministic recommendation for candidate excluded from paid advisory subpacket
            det_item = build_deterministic_recommendation(cand, resolutions, comparison)
            merged_items.append(det_item)

    merged_ids = {r["candidate_id"] for r in merged_items}
    if merged_ids != expected_ids or len(merged_items) != len(expected_ids):
        raise ProposalValidationError(
            [f"Merged recommendations must cover every candidate exactly once: expected {len(expected_ids)}, got {len(merged_items)}"]
        )

    return sorted(merged_items, key=lambda x: x["candidate_id"])


def prepare_selective_candidates(
    bundle: Mapping[str, Any],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    policy: IngestionReasoningPolicy,
    selective_active: bool,
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]], set[str] | None]:
    """Helper to partition candidates into selective subpackets if active."""
    if not selective_active or len(bundle.get("candidates", [])) <= 1:
        return False, list(bundle.get("candidates", [])), [], None
    hard, easy = partition_candidates_by_complexity(bundle, resolutions, comparison, policy)
    use = bool(hard and easy)
    return use, hard, easy, ({c["candidate_id"] for c in hard} if use else None)


def merge_advisory_subpacket(
    output: Mapping[str, Any],
    easy_candidates: list[dict[str, Any]],
    all_candidates: list[dict[str, Any]],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Helper to merge subpacket advisory outputs with deterministic recommendations."""
    merged_recs = merge_advisory_and_deterministic_proposals(
        advisory_recommendations=output["recommendations"],
        deterministic_candidates=easy_candidates,
        all_bundle_candidates=all_candidates,
        resolutions=resolutions,
        comparison=comparison,
    )
    res = dict(output)
    res["recommendations"] = merged_recs
    return res
