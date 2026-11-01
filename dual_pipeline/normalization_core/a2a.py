"""A2A fact-vector packet normalization with strict provenance and span integrity."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..a2a_finalization import VerifiedA2AFinalization, verified_finalization_binding
from ..contracts import create_artifact, validate_artifact
from ..core import sha256_bytes, stable_id, trace_id
from ..source_spans import HUMAN_ATTESTATION, SELECTION_SCHEMA_VERSION
from ..source_trust import assert_pinned_vendor_host
from .meeting import _context_extension, _extension


def _canonical_selected_span_locator(
    *, final_url: str, anchor: object, start: object, end: object, quote_sha256: object
) -> dict[str, str]:
    if (
        not isinstance(anchor, str)
        or not anchor
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or not isinstance(quote_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", quote_sha256)
    ):
        raise ValueError("A2A selected evidence span has an invalid canonical locator binding")
    return {
        "kind": "uri_fragment",
        "value": f"{final_url}#{anchor}:V{start}-{end}",
        "quote_digest": quote_sha256,
    }


def _require_aware_timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset")
    return value


def _selected_span_selection_bindings(
    *,
    selection: object,
    fresh_capture: dict[str, Any],
    evidence_spans: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Validate the pre-intake human selection binding for frozen source spans."""

    if not isinstance(selection, dict):
        raise ValueError("A2A selected evidence spans require a source selection extension")
    required = {
        "schema_version",
        "span_ledger_sha256",
        "selection_ledger_sha256",
        "reviewer",
        "decided_at",
        "human_attestation",
        "reviewer_authorization_reference",
        "decisions",
    }
    if set(selection) != required:
        raise ValueError("A2A source selection extension has an invalid shape")
    reviewer = selection.get("reviewer")
    authorization_reference = selection.get("reviewer_authorization_reference")
    if (
        selection.get("schema_version") != SELECTION_SCHEMA_VERSION
        or selection.get("span_ledger_sha256") != fresh_capture.get("source_span_ledger_sha256")
        or selection.get("selection_ledger_sha256")
        != fresh_capture.get("selection_ledger_sha256")
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or selection.get("human_attestation") != HUMAN_ATTESTATION
        or (
            authorization_reference is not None
            and (
                not isinstance(authorization_reference, str)
                or not authorization_reference.strip()
            )
        )
        or not isinstance(selection.get("decisions"), list)
    ):
        raise ValueError("A2A source selection attestation is invalid or not capture-bound")
    _require_aware_timestamp(selection.get("decided_at"), label="A2A selection decided_at")
    by_evidence: dict[str, dict[str, Any]] = {}
    required_decision = {
        "fact_id",
        "decision",
        "span_proposal_id",
        "evidence_id",
        "source_id",
        "rewritten_claim",
        "rationale",
        "quote_sha256",
        "visible_start_offset",
        "visible_end_offset",
        "raw_response_sha256",
        "visible_text_sha256",
        "canonical_locator",
    }
    for row in selection["decisions"]:
        evidence_id = row.get("evidence_id") if isinstance(row, dict) else None
        if (
            not isinstance(row, dict)
            or set(row) != required_decision
            or not isinstance(evidence_id, str)
            or evidence_id not in evidence_spans
            or evidence_id in by_evidence
            or row.get("decision") not in {"CONFIRM", "REWRITE"}
            or not isinstance(row.get("fact_id"), str)
            or not isinstance(row.get("span_proposal_id"), str)
            or not isinstance(row.get("rationale"), str)
            or not row["rationale"].strip()
        ):
            raise ValueError("A2A source selection decision is invalid or incomplete")
        span = evidence_spans[evidence_id]
        evidence = evidence_by_id[evidence_id]
        expected_locator = _canonical_selected_span_locator(
            final_url=fresh_capture["final_url"],
            anchor=span.get("anchor"),
            start=span.get("visible_start_offset"),
            end=span.get("visible_end_offset"),
            quote_sha256=span.get("quote_sha256"),
        )
        if (
            row["fact_id"] != span.get("fact_id")
            or row["span_proposal_id"] != span.get("span_proposal_id")
            or row["source_id"] != span.get("source_id")
            or row["quote_sha256"] != span.get("quote_sha256")
            or row["visible_start_offset"] != span.get("visible_start_offset")
            or row["visible_end_offset"] != span.get("visible_end_offset")
            or row["raw_response_sha256"] != span.get("raw_response_sha256")
            or row["visible_text_sha256"] != span.get("visible_text_sha256")
            or row["canonical_locator"] != expected_locator
            or evidence["locator"] != expected_locator
        ):
            raise ValueError("A2A source selection decision does not bind to its frozen quote")
        if row["decision"] == "REWRITE":
            rewritten = row.get("rewritten_claim")
            if not isinstance(rewritten, str) or not rewritten.strip():
                raise ValueError("A2A source selection REWRITE requires a narrowed claim")
        elif row.get("rewritten_claim") is not None:
            raise ValueError("A2A source selection CONFIRM cannot carry a rewritten claim")
        by_evidence[evidence_id] = {
            "selection_ledger_sha256": selection["selection_ledger_sha256"],
            "span_ledger_sha256": selection["span_ledger_sha256"],
            "reviewer": reviewer.strip(),
            "decided_at": selection["decided_at"],
            "human_attestation": HUMAN_ATTESTATION,
            "reviewer_authorization_reference": authorization_reference,
            "fact_id": row["fact_id"],
            "decision": row["decision"],
            "span_proposal_id": row["span_proposal_id"],
            "evidence_id": evidence_id,
            "source_id": row["source_id"],
            "rationale": row["rationale"],
            "quote": span["quote"],
            "quote_sha256": row["quote_sha256"],
            "visible_start_offset": row["visible_start_offset"],
            "visible_end_offset": row["visible_end_offset"],
            "raw_response_sha256": row["raw_response_sha256"],
            "visible_text_sha256": row["visible_text_sha256"],
            "canonical_locator": expected_locator,
        }
    if set(by_evidence) != set(evidence_spans):
        raise ValueError("A2A source selection does not decide every selected evidence span")
    return by_evidence


def _validated_a2a_finalization_binding(
    proof: object, *, package: dict[str, Any]
) -> dict[str, Any] | None:
    """Accept only the nonserialized ingress proof for this exact package.

    The binding is constructed by the runtime archive-replay verifier, never
    read from an A2A package extension.  A direct normalizer caller may still
    inspect a selected candidate, but it cannot mark that candidate ready for
    fact promotion without this proof.
    """

    if proof is None:
        return None
    if not isinstance(proof, VerifiedA2AFinalization):
        raise ValueError("A2A finalization proof must be verifier-issued")
    try:
        return verified_finalization_binding(proof, package=package)
    except ValueError as exc:
        raise ValueError("A2A finalization proof is not bound to this package") from exc


def normalize_a2a_fact_vector_packet(
    package: dict[str, Any],
    *,
    run_context: str = "unbound",
    finalization_proof: VerifiedA2AFinalization | None = None,
) -> dict[str, Any]:
    """Normalize one verified A2A packet source into forced-HITL candidates.

    Packet signature verification proves package integrity only.  The adapter
    therefore preserves first-party facts as candidates and keeps derived
    practices as guidance; neither becomes an approved assertion here.
    """

    validate_artifact(package)
    if not package["contract_id"].endswith(
        ":a2a-fact-vector-packet-intake:1.0.0"
    ):
        raise ValueError("expected an A2A fact-vector packet intake contract")
    if package.get("adapter") != {
        "name": "nee-a2a-fact-vector-packet",
        "version": "1.0.0",
    }:
        raise ValueError("A2A fact-vector package has an unsupported adapter binding")
    packet = package["packet"]
    source = package["source"]
    if packet["signature_status"] != "verified_integrity_only":
        raise ValueError("A2A fact-vector packet signature is not integrity-verified")
    if source["source_posture"] == "external_first_party" and source["first_party"] is True:
        assert_pinned_vendor_host(source.get("vendor"), source.get("url"))
    verified_finalization_binding = _validated_a2a_finalization_binding(
        finalization_proof,
        package=package,
    )

    fresh_capture = _extension(package, "com.partner_advisory.nee.a2a-fresh-capture", None)
    evidence_spans = _extension(package, "com.partner_advisory.nee.a2a-evidence-spans", None)
    source_selection = _extension(
        package, "com.partner_advisory.nee.a2a-source-span-selection", None
    )
    if fresh_capture is not None:
        if not isinstance(fresh_capture, dict):
            raise ValueError("A2A fresh-capture lineage must be an object")
        required_capture_fields = (
            "capture_id",
            "capture_manifest_sha256",
            "captured_at",
            "requested_url",
            "final_url",
            "raw_response_sha256",
            "visible_text_sha256",
            "evidence_outcomes",
        )
        if (
            any(not isinstance(fresh_capture.get(key), str) or not fresh_capture[key] for key in required_capture_fields[:-1])
            or not isinstance(fresh_capture.get("evidence_outcomes"), dict)
            or source["source_posture"] != "external_first_party"
            or source["first_party"] is not True
            or source["url"] != fresh_capture["final_url"]
            or source["source_location"] != fresh_capture["final_url"]
            or not re.fullmatch(r"[0-9a-f]{64}", fresh_capture["capture_manifest_sha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", fresh_capture["raw_response_sha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", fresh_capture["visible_text_sha256"])
        ):
            raise ValueError("A2A fresh-capture lineage is invalid or not source-bound")
        assert_pinned_vendor_host(source.get("vendor"), fresh_capture["final_url"])

    evidence = package["evidence"]
    evidence_ids = [item["evidence_id"] for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("A2A fact-vector package has duplicate evidence IDs")
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    for item in evidence:
        if (
            item["source_id"] != source["source_id"]
            or item["source_version_id"] != source["source_version_id"]
        ):
            raise ValueError("A2A evidence must belong to the package source version")
    selected_span_bindings: dict[str, dict[str, Any]] = {}
    if evidence_spans is not None:
        if fresh_capture is None or not isinstance(evidence_spans, dict):
            raise ValueError("A2A selected evidence spans require a fresh capture")
        if set(evidence_spans) != set(evidence_ids):
            raise ValueError("A2A selected evidence spans must account for every evidence item")
        required_span_fields = {
            "source_id",
            "source_version_id",
            "capture_id",
            "raw_response_sha256",
            "visible_text_sha256",
            "requested_url",
            "final_url",
            "captured_at",
            "visible_start_offset",
            "visible_end_offset",
            "quote",
            "quote_sha256",
            "anchor",
            "status",
        }
        for item in evidence:
            span = evidence_spans[item["evidence_id"]]
            if (
                not isinstance(span, dict)
                or not required_span_fields <= set(span)
                or span["source_id"] != source["source_id"]
                or span["source_version_id"] != source["source_version_id"]
                or span["capture_id"] != fresh_capture["capture_id"]
                or any(
                    span[key] != fresh_capture[key]
                    for key in (
                        "raw_response_sha256",
                        "visible_text_sha256",
                        "requested_url",
                        "final_url",
                        "captured_at",
                    )
                )
                or not isinstance(span["visible_start_offset"], int)
                or not isinstance(span["visible_end_offset"], int)
                or span["visible_start_offset"] < 0
                or span["visible_end_offset"] <= span["visible_start_offset"]
                or not isinstance(span["quote"], str)
                or not span["quote"].strip()
                or span["visible_end_offset"] - span["visible_start_offset"]
                != len(span["quote"])
                or span["quote_sha256"]
                != sha256_bytes(span["quote"].encode("utf-8"))
                or item["locator"]
                != _canonical_selected_span_locator(
                    final_url=fresh_capture["final_url"],
                    anchor=span["anchor"],
                    start=span["visible_start_offset"],
                    end=span["visible_end_offset"],
                    quote_sha256=span["quote_sha256"],
                )
                or item["content_digest"] != span["quote_sha256"]
                or item["locator"].get("quote_digest") != span["quote_sha256"]
                or span["status"] != "SELECTED_FOR_HITL"
                or item["verification_state"] != "fresh_capture_selected_span"
            ):
                raise ValueError("A2A selected evidence span is not bound to its frozen quote")
        selected_span_bindings = _selected_span_selection_bindings(
            selection=source_selection,
            fresh_capture=fresh_capture,
            evidence_spans=evidence_spans,
            evidence_by_id=evidence_by_id,
        )
    elif source_selection is not None:
        raise ValueError("A2A source selection extension requires selected evidence spans")
    if fresh_capture is not None:
        outcomes = fresh_capture["evidence_outcomes"]
        if set(outcomes) != set(evidence_ids):
            raise ValueError("A2A fresh capture does not account for every evidence item")
        expected_state = {
            "MATCH": "fresh_capture_verified",
            "MISSING": "fresh_capture_preview_mismatch",
            "NOT_APPLICABLE_LOCATOR_REQUIRES_HITL_SPAN_SELECTION": "fresh_capture_locator_pending",
            "SELECTED_CURRENT_SPAN": "fresh_capture_selected_span",
        }
        for item in evidence:
            outcome = outcomes[item["evidence_id"]]
            if expected_state.get(outcome) != item["verification_state"]:
                raise ValueError("A2A fresh capture evidence status does not match its recorded outcome")

    candidates = package["candidates"]
    candidate_ids = [item["candidate_id"] for item in candidates]
    source_item_ids = [item["source_item_id"] for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("A2A fact-vector package has duplicate candidate IDs")
    if len(source_item_ids) != len(set(source_item_ids)):
        raise ValueError("A2A fact-vector package has duplicate source item IDs")
    known_evidence_ids = set(evidence_ids)
    candidate_governance: dict[str, dict[str, Any]] = {}
    normalized_candidates: list[dict[str, Any]] = []
    expected_category = {
        "assertion": ("assertion", "candidate_hypothesis"),
        "guidance": ("action", "guidance"),
        "evidence_span": ("source_chunk", "citation_only"),
    }
    # A verified historic preview is useful provenance, but it is not an
    # independently selected current quotation.  Only a source-selection
    # record tied to an exact frozen span may enter the later UWG fact path.
    # Keep ``candidate_evidence_bound_hitl_required`` as a candidate status so
    # older source-review packets remain inspectable, but never treat it as a
    # fact-promotion-ready state.
    fact_promotion_ready_status = "candidate_evidence_selected_span_hitl_required"
    for candidate in candidates:
        category = candidate["retrieval_kind"]
        if expected_category.get(category) != (
            candidate["candidate_type"],
            candidate["answer_use"],
        ):
            raise ValueError(
                "A2A candidate retrieval category does not match its governed type"
            )
        if not set(candidate["evidence_ids"]) <= known_evidence_ids:
            raise ValueError("A2A candidate refers to unknown evidence")
        referenced_evidence = [
            evidence_by_id[evidence_id] for evidence_id in candidate["evidence_ids"]
        ]
        if candidate["source_locator"] not in [
            item["locator"] for item in referenced_evidence
        ]:
            raise ValueError("A2A candidate source locator must equal referenced evidence")
        if category == "guidance" and candidate["promotion_status"] != "recommendation_only":
            raise ValueError("A2A guidance is limited to recommendation_only promotion status")
        if category == "evidence_span":
            if candidate["promotion_status"] != "citation_only":
                raise ValueError("A2A evidence span is limited to citation_only promotion status")
            # An A2A citation record must be a current, selected quotation,
            # not the packet's historic preview text.  Citation-only material
            # remains outside fact promotion, but it must still be honestly
            # attributable to the frozen first-party capture.
            if evidence_spans is None or len(candidate["evidence_ids"]) != 1:
                raise ValueError("A2A citation-only retrieval requires one selected frozen span")
            evidence_id = candidate["evidence_ids"][0]
            if (
                candidate["normalized_text"] != evidence_spans[evidence_id]["quote"]
                or evidence_by_id[evidence_id]["verification_state"]
                != "fresh_capture_selected_span"
            ):
                raise ValueError("A2A selected citation span text must equal its frozen quote")
        if category == "assertion" and candidate["promotion_status"] in {
            "recommendation_only",
            "citation_only",
        }:
            raise ValueError("only A2A assertion candidates are fact-promotion eligible")
        if category == "assertion" and fresh_capture is None:
            if candidate["promotion_status"] != "candidate_evidence_bound_refetch_required":
                raise ValueError(
                    "A2A assertion without a fresh capture must remain refetch-required"
                )
        if fresh_capture is not None and category == "assertion":
            evidence_states = {
                item["verification_state"]
                for item in evidence
                if item["evidence_id"] in candidate["evidence_ids"]
            }
            expected_promotion_status = (
                "candidate_evidence_selected_span_hitl_required"
                if evidence_states == {"fresh_capture_selected_span"}
                else (
                    "candidate_evidence_bound_hitl_required"
                    if evidence_states == {"fresh_capture_verified"}
                    else (
                        "candidate_evidence_preview_mismatch_requires_hitl"
                        if "fresh_capture_preview_mismatch" in evidence_states
                        else "candidate_evidence_locator_pending"
                    )
                )
            )
            if candidate["promotion_status"] != expected_promotion_status:
                raise ValueError("A2A assertion promotion state does not match fresh evidence")
        if fresh_capture is not None and category == "evidence_span":
            if any(
                item["verification_state"] != "fresh_capture_selected_span"
                for item in evidence
                if item["evidence_id"] in candidate["evidence_ids"]
            ):
                raise ValueError("A2A citation-only retrieval needs a selected fresh excerpt")
        candidate_bindings = [
            selected_span_bindings[evidence_id]
            for evidence_id in candidate["evidence_ids"]
            if evidence_id in selected_span_bindings
        ]
        selected_current_evidence = bool(candidate["evidence_ids"]) and all(
            evidence_by_id[evidence_id]["verification_state"]
            == "fresh_capture_selected_span"
            for evidence_id in candidate["evidence_ids"]
        )
        if (
            category == "assertion"
            and candidate["promotion_status"] == fact_promotion_ready_status
            and (
                fresh_capture is None
                or not selected_current_evidence
                or len(candidate_bindings) != len(candidate["evidence_ids"])
            )
        ):
            raise ValueError(
                "A2A fact-promotion eligibility requires selected, attested frozen source spans"
            )
        governance = {
            "source_item_id": candidate["source_item_id"],
            "retrieval_kind": category,
            "answer_use": candidate["answer_use"],
            # This is deliberately an eligibility marker, not a fact label.
            # A true value permits the candidate to enter the separately
            # evidence-bound UWG promotion workflow; it never authorizes an
            # answer or a graph promotion on its own.
            "fact_promotion_eligible": (
                category == "assertion"
                and candidate["promotion_status"] == fact_promotion_ready_status
                and fresh_capture is not None
                and selected_current_evidence
                and len(candidate_bindings) == len(candidate["evidence_ids"])
                and verified_finalization_binding is not None
            ),
            "review_questions": [candidate["review_question"]],
            "promotion_status": candidate["promotion_status"],
            "evidence_verification_states": {
                item["evidence_id"]: item["verification_state"]
                for item in evidence
                if item["evidence_id"] in candidate["evidence_ids"]
            },
            # The user requested that no source assertion, recommendation, or
            # example bypass individual source-data review.
            "force_hitl": True,
        }
        if selected_span_bindings:
            governance["source_selection_bindings"] = candidate_bindings
            if len(governance["source_selection_bindings"]) != len(candidate["evidence_ids"]):
                raise ValueError("A2A selected candidate lacks a source selection binding")
        if verified_finalization_binding is not None:
            governance["finalization_binding"] = verified_finalization_binding
        candidate_governance[candidate["candidate_id"]] = governance
        normalized_candidates.append(
            {
                key: candidate[key]
                for key in (
                    "candidate_id",
                    "candidate_type",
                    "assertion_type",
                    "normalized_text",
                    "materiality",
                    "entity_mentions",
                    "evidence_ids",
                    "source_locator",
                    "classification",
                    "audiences",
                    "extraction_confidence",
                )
            }
        )

    package_digest = package["canonical_digest"]["value"]
    intake_id = stable_id("intake", package["artifact_id"], package_digest, length=24)
    run_id = stable_id(
        "run-intake", package["artifact_id"], package_digest, run_context, length=24
    )
    current_trace_id = trace_id(run_id, package_digest)
    return create_artifact(
        "normalized-candidate-bundle",
        stable_id("candidate-bundle", run_id, length=26),
        intake_id=intake_id,
        run_id=run_id,
        trace_id=current_trace_id,
        source_id=source["source_id"],
        source_version_id=source["source_version_id"],
        evidence=[
            {
                key: item[key]
                for key in (
                    "evidence_id",
                    "source_id",
                    "source_version_id",
                    "locator",
                    "relationship",
                    "directness",
                    "authority_rank",
                    "content_digest",
                )
            }
            for item in evidence
        ],
        candidates=normalized_candidates,
        extensions=_context_extension(
            source_kind="a2a_fact_vector_packet",
            attendees=[],
            workstream=None,
            source_posture=source["source_posture"],
            authority_rank=source["authority_rank"],
        )
        | {
            "com.partner_advisory.nee.source": {
                "name": source["title"],
                "date": source["source_date"],
                "package_digest": package_digest,
                "source_location": source["source_location"],
                "source_zip_sha256": packet["source_zip_sha256"],
                "packet_manifest_sha256": packet["packet_manifest_sha256"],
                "packet_signature_status": packet["signature_status"],
                "packet_trust_scope": packet["trust_scope"],
                "source_posture": source["source_posture"],
                "authority_rank": source["authority_rank"],
                "fresh_capture": fresh_capture,
                "vendor": source["vendor"],
                "a2a_evidence_spans": evidence_spans,
                "a2a_source_span_selection": source_selection,
                "a2a_finalization_binding": verified_finalization_binding,
            },
            "com.partner_advisory.nee.a2a-packet-candidate-governance": candidate_governance,
        },
    )
