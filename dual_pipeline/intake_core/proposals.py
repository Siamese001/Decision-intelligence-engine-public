"""A2A provenance, external concepts, and internal entity proposals."""

from __future__ import annotations

import re
from typing import Any

from dual_pipeline.core import digest, normalize_name, stable_id
from dual_pipeline.identity import NEW_NODE_ASSESSMENT_EXTENSION_KEY

from .constants import (
    _A2A_FINALIZATION_BINDING_FIELDS,
    A2A_GUIDANCE_ANCHOR_KEY,
    EXTERNAL_CONCEPT_OUTCOME,
    EXTERNAL_CONCEPT_PROPOSAL_KEY,
    NEW_INTERNAL_ENTITY_DIGEST_KEY,
    NEW_INTERNAL_ENTITY_KEY,
    SEMANTIC_CORRECTION_CANDIDATE_KEY,
)


def _is_a2a_source_bundle(bundle: dict[str, Any]) -> bool:
    """Recognize A2A provenance from both normalized source and route metadata.

    The context field selects routing, while the source extension preserves the
    source-bound A2A lineage.  A direct caller must not be able to downgrade an
    A2A candidate to the generic path simply by stripping the routing field.
    """

    extensions = bundle.get("extensions", {})
    context = extensions.get("com.partner_advisory.nee.context", {})
    source = extensions.get("com.partner_advisory.nee.source", {})
    context_kind = context.get("source_kind") if isinstance(context, dict) else None
    source_has_a2a_marker = isinstance(source, dict) and any(
        key in source
        for key in (
            "a2a_evidence_spans",
            "a2a_source_span_selection",
            "source_zip_sha256",
            "packet_manifest_sha256",
        )
    )
    if context_kind == "a2a_fact_vector_packet" and not source_has_a2a_marker:
        raise ValueError("A2A context lacks the required immutable source provenance")
    if source_has_a2a_marker and context_kind not in {None, "a2a_fact_vector_packet"}:
        raise ValueError("A2A source provenance conflicts with its routed source kind")
    return source_has_a2a_marker or context_kind == "a2a_fact_vector_packet"


def _validated_a2a_finalization_binding(value: object) -> dict[str, Any]:
    """Validate the audit-safe projection of the replayed finalization receipt."""

    if not isinstance(value, dict) or set(value) != _A2A_FINALIZATION_BINDING_FIELDS:
        raise ValueError("A2A finalization binding has an invalid shape")
    if value.get("verification_state") != "finalization_archive_replayed":
        raise ValueError("A2A finalization binding was not replay-verified")
    if any(
        not isinstance(value.get(field), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value[field])
        for field in (
            "receipt_canonical_digest",
            "receipt_file_sha256",
            "package_file_sha256",
            "package_canonical_digest",
            "archive_binding_digest",
        )
    ) or any(
        not isinstance(value.get(field), str) or not value[field]
        for field in ("receipt_id", "archive_root_id")
    ):
        raise ValueError("A2A finalization binding has invalid values")
    return dict(value)


def _correlation(
    *,
    source_id: str | None = None,
    candidate_id: str | None = None,
    graph_operation_id: str | None = None,
    hitl_item_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "source_id": source_id,
        "candidate_id": candidate_id,
        "query_id": None,
        "graph_operation_id": graph_operation_id,
        "hitl_item_id": hitl_item_id,
    }


def _candidate_resolution_map(
    bundle: dict[str, Any], resolutions: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    by_mention = {value["mention_id"]: value for value in resolutions}
    result: dict[str, list[dict[str, Any]]] = {}
    for candidate in bundle["candidates"]:
        result[candidate["candidate_id"]] = [
            by_mention[mention["mention_id"]] for mention in candidate["entity_mentions"]
        ]
    return result


def _external_concept_proposal(
    resolution: dict[str, Any], bundle: dict[str, Any]
) -> dict[str, Any]:
    """Validate the deterministic source-scoped identity used by an external node."""

    if resolution.get("outcome") != EXTERNAL_CONCEPT_OUTCOME:
        raise ValueError("external concept proposal requires its governed resolution outcome")
    proposal = resolution.get("extensions", {}).get(EXTERNAL_CONCEPT_PROPOSAL_KEY)
    required = {
        "external_concept_id",
        "identity_scope",
        "no_internal_equivalence",
        "source_id",
        "source_version_id",
        "vendor",
        "reported_name",
        "normalized_label",
    }
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise ValueError("external concept proposal has an invalid governed shape")
    if (
        proposal["identity_scope"] != "source_scoped_external"
        or proposal["no_internal_equivalence"] is not True
        or proposal["source_id"] != bundle["source_id"]
        or proposal["source_version_id"] != bundle["source_version_id"]
        or proposal["reported_name"] != resolution["reported_name"]
        or proposal["normalized_label"] != normalize_name(resolution["reported_name"])
        or not isinstance(proposal["vendor"], str)
        or not proposal["vendor"].strip()
    ):
        raise ValueError("external concept proposal is not bound to its source-scoped mention")
    expected_id = stable_id(
        "external-concept",
        proposal["source_id"],
        proposal["vendor"],
        proposal["normalized_label"],
        length=28,
    )
    if proposal["external_concept_id"] != expected_id:
        raise ValueError("external concept proposal identity is not deterministic")
    return proposal


def _semantic_correction_control(
    bundle: dict[str, Any], candidate_id: str
) -> dict[str, Any]:
    """Return an exact source-bound correction control for one candidate."""

    raw = bundle.get("extensions", {}).get(SEMANTIC_CORRECTION_CANDIDATE_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("semantic correction candidate governance must be an object")
    value = raw.get(candidate_id)
    if not isinstance(value, dict) or set(value) != {
        "chunk_id",
        "force_hitl",
        "replaces_run_id",
        "retire_only",
        "supersedes_assertion_id",
    }:
        raise ValueError("semantic correction candidate governance has an invalid shape")
    if (
        value["force_hitl"] is not True
        or not isinstance(value["chunk_id"], str)
        or not value["chunk_id"]
        or not isinstance(value["replaces_run_id"], str)
        or not value["replaces_run_id"].startswith(
            ("run-intake-", "extraction-run-")
        )
        or not isinstance(value["retire_only"], bool)
        or not isinstance(value["supersedes_assertion_id"], str)
        or not value["supersedes_assertion_id"].startswith("assertion-")
    ):
        raise ValueError("semantic correction candidate governance is incomplete")
    return dict(value)


def _new_internal_entity_proposal(
    *,
    candidate: dict[str, Any],
    resolutions: list[dict[str, Any]],
    decision: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate an approval-bound internal entity proposal selected by HITL."""

    if decision is None or decision.get("decision") != "CREATE_NEW":
        return None
    proposal = decision.get("extensions", {}).get(NEW_INTERNAL_ENTITY_KEY)
    required = {
        "canonical_name",
        "description",
        "entity_id",
        "node_type",
        "status",
        "source_id",
        "source_version_id",
        "source_stable_identifier",
        "source_profile_digest",
        "evidence_ids",
        "affected_candidate_ids",
        "possible_duplicate_labels",
    }
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise ValueError("CREATE_NEW requires an exact internal entity proposal")
    if any(
        not isinstance(proposal[key], str) or not proposal[key].strip()
        for key in (
            "canonical_name",
            "description",
            "entity_id",
            "node_type",
            "status",
            "source_id",
            "source_version_id",
            "source_stable_identifier",
            "source_profile_digest",
        )
    ):
        raise ValueError("internal entity proposal has incomplete values")
    proposal_digest = digest(proposal)
    if decision.get("extensions", {}).get(NEW_INTERNAL_ENTITY_DIGEST_KEY) != proposal_digest:
        raise ValueError("CREATE_NEW does not bind the exact emitted proposal digest")
    matching_assessments = [
        value
        for resolution in resolutions
        if isinstance(value := resolution.get("extensions", {}).get(NEW_NODE_ASSESSMENT_EXTENSION_KEY), dict)
        and value.get("status") == "PROPOSE_NEW_CANDIDATE"
        and value.get("proposal") == proposal
        and value.get("proposal_digest") == proposal_digest
    ]
    if not matching_assessments:
        raise ValueError("CREATE_NEW may approve only an emitted new-node proposal")
    stable_ids = {
        resolution.get("stable_identifier", {}).get("value")
        for resolution in resolutions
    }
    reported_names = {resolution.get("reported_name") for resolution in resolutions}
    if (
        proposal["source_stable_identifier"] not in stable_ids
        or proposal["canonical_name"] not in reported_names
        or proposal["status"] != "candidate"
        or candidate["candidate_id"] not in proposal["affected_candidate_ids"]
        or set(candidate["evidence_ids"]) - set(proposal["evidence_ids"])
        or set(decision["affected_candidate_ids"])
        != set(proposal["affected_candidate_ids"])
        or proposal["entity_id"]
        not in {
            stable_id(
                "entity",
                proposal["node_type"],
                proposal["source_id"],
                proposal["source_stable_identifier"],
                length=28,
            ),
            stable_id(
                "entity",
                proposal["node_type"],
                proposal["source_id"],
                __import__("re").sub(
                    r"[\s_]+", "-", str(proposal["source_stable_identifier"] or "").strip().lower()
                ),
                length=28,
            ),
        }
        or not any(
            mention.get("reported_text") == proposal["canonical_name"]
            and mention.get("stable_identifier")
            == proposal["source_stable_identifier"]
            for mention in candidate.get("entity_mentions", [])
        )
    ):
        raise ValueError("internal entity proposal is not bound to the source mention")
    return dict(proposal)


def _a2a_guidance_anchor(
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    decision: dict[str, Any] | None,
) -> str | None:
    """Return a reviewed pattern-node anchor for non-factual A2A guidance.

    A guidance anchor is deliberately not an identity resolution.  It lets a
    reviewer attach a recommendation to an existing governed pattern without
    claiming that a generic packet label such as ``ownership`` or ``privacy``
    is identical to that pattern.  The guidance remains candidate-only and is
    never eligible for factual promotion.
    """

    if decision is None:
        return None
    extensions = decision.get("extensions", {})
    anchor = extensions.get(A2A_GUIDANCE_ANCHOR_KEY)
    if anchor is None:
        return None
    from .hitl import _candidate_governance
    governance = _candidate_governance(bundle, candidate["candidate_id"])
    if (
        not _is_a2a_source_bundle(bundle)
        or candidate.get("candidate_type") != "action"
        or governance.get("retrieval_kind") != "guidance"
        or governance.get("answer_use") != "guidance"
        or governance.get("fact_promotion_eligible") is not False
        or governance.get("promotion_status") != "recommendation_only"
        or decision.get("decision") != "APPROVE_CANDIDATE"
        or not isinstance(anchor, str)
        or not anchor.strip()
    ):
        raise ValueError(
            "A2A guidance anchor requires an APPROVE_CANDIDATE decision for "
            "recommendation-only guidance"
        )
    if extensions.get("com.partner_advisory.nee.selected-entity-id"):
        raise ValueError(
            "A2A guidance anchor cannot also assert an entity identity resolution"
        )
    return anchor
