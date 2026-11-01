#!/usr/bin/env python3
"""Build final, source-span-bound A2A candidate packages after span selection.

This is deliberately a *package finalizer*, not a promotion tool.  It takes
the immutable first-party capture and a human span-selection ledger, verifies
every binding again, and emits candidate-only packets.  A later governed
intake run still produces and requires the normal, individual HITL decisions.

The selector input is intentionally small and reviewable::

  {
    "schema_version": "1.1.0",
    "span_ledger_sha256": "<sha256 of extract_a2a_evidence_spans output>",
    "reviewer": "reviewer@example.com",
    "decided_at": "2026-07-25T12:00:00Z",
    "human_attestation": "<fixed required phrase>",
    "reviewer_authorization_reference": null,
    "decisions": [
      {
        "fact_id": "FACT-001",
        "decision": "CONFIRM", "span_proposal_id": "a2a-span-...",
        "rationale": "The exact frozen quote directly supports the claim."
      },
      {
        "fact_id": "FACT-002",
        "decision": "REWRITE",
        "span_proposal_id": "a2a-span-...",
        "rewritten_claim": "A narrower directly supported claim."
      },
      {"fact_id": "FACT-003", "decision": "HOLD", "rationale": "Insufficient support."}
    ],
    "guidance_pattern_decisions": [
      {"source_item_id": "GP-001", "content_kind": "guidance", "decision": "KEEP_GUIDANCE", "rationale": "Useful but non-factual."}
    ]
  }

Every source fact must receive one decision.  Only CONFIRM and REWRITE rows
produce source-bound candidate packets.  HOLD and REJECT are retained in the
immutable finalization report and cannot silently become retrieval records.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.a2a_finalization import (  # noqa: E402
    ARCHIVED_SOURCE_BUNDLE_FILENAME,
    A2AFinalizationError,
    _relative_path,
    create_finalization_receipt,
    stage_finalization_archive,
)
from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    artifact_digest,
    seal_artifact,
    sha256_bytes,
    sha256_file,
    slug,
    stable_id,
    write_json_exclusive,
)
from dual_pipeline.source_spans import (  # noqa: E402
    HUMAN_ATTESTATION,
    SELECTION_SCHEMA_VERSION,
    SourceSpanError,
    validate_capture_document,
    validate_span_proposal,
)
from dual_pipeline.source_trust import assert_pinned_vendor_host  # noqa: E402
from tools.prepare_a2a_fact_vector_packet_intake import (  # noqa: E402
    PACKET_TRUST_SCOPE,
    SOURCE_KIND,
    _derived_evidence,
    _derived_source,
    _guidance_candidate,
    _pattern_candidate,
    read_and_verify_bundle,
)

CONTRACT_ID = "urn:nee:dual-pipeline:a2a-fact-vector-packet-intake:1.0.0"
SPAN_LEDGER_SCHEMA_VERSION = "1.0.0"
ALLOWED_SELECTION_DECISIONS = {"CONFIRM", "REWRITE", "HOLD", "REJECT"}
ALLOWED_GUIDANCE_DECISIONS = {"KEEP_GUIDANCE", "HOLD", "REJECT"}
SPAN_OUTCOME = "SELECTED_CURRENT_SPAN"


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _safe_capture_raw_path(manifest_path: Path, capture: dict[str, Any]) -> Path:
    raw_relative = capture.get("raw_path")
    try:
        safe_relative = _relative_path(raw_relative, label="capture raw path")
    except A2AFinalizationError as exc:
        raise ValueError("capture raw path must be a safe nonempty relative path") from exc
    raw_path = (manifest_path.parent / safe_relative).resolve()
    try:
        raw_path.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise ValueError("capture raw path escapes its manifest directory") from exc
    if not raw_path.is_file() or sha256_file(raw_path) != capture.get("raw_sha256"):
        raise ValueError("capture raw bytes are missing or hash-mismatched")
    return raw_path


def _capture_index(
    capture_manifest_path: Path, verified: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(capture_manifest_path, label="capture manifest")
    if (
        manifest.get("schema_version") != SPAN_LEDGER_SCHEMA_VERSION
        or manifest.get("status") != "PASS"
        or manifest.get("bundle", {}).get("sha256") != verified["bundle_sha256"]
        or manifest.get("bundle", {}).get("bundle_manifest_sha256")
        != verified["bundle_manifest_sha256"]
        or not isinstance(manifest.get("capture_id"), str)
        or not manifest["capture_id"]
    ):
        raise ValueError("capture manifest is not bound to the verified source bundle")
    result: dict[str, dict[str, Any]] = {}
    captures = manifest.get("captures")
    if not isinstance(captures, list) or not captures:
        raise ValueError("capture manifest has no captures")
    for capture in captures:
        source_id = capture.get("source_id") if isinstance(capture, dict) else None
        if (
            not isinstance(capture, dict)
            or not isinstance(source_id, str)
            or source_id in result
            or capture.get("status") != "CAPTURED"
            or not isinstance(capture.get("raw_sha256"), str)
            or not isinstance(capture.get("visible_text_sha256"), str)
            or not isinstance(capture.get("requested_url"), str)
            or not isinstance(capture.get("final_url"), str)
            or not isinstance(capture.get("captured_at"), str)
        ):
            raise ValueError("capture manifest contains an invalid capture record")
        _safe_capture_raw_path(capture_manifest_path, capture)
        result[source_id] = capture
    return manifest, result


def _validate_capture_source_bindings(
    captures: dict[str, dict[str, Any]], source_rows: dict[str, dict[str, Any]]
) -> None:
    """Bind capture identity and both URLs to the signed source inventory."""

    if set(captures) != set(source_rows):
        raise ValueError("capture manifest and source inventory do not have the same source IDs")
    for source_id, source_row in source_rows.items():
        capture = captures[source_id]
        vendor = source_row.get("vendor")
        requested_url = source_row.get("url")
        if (
            not isinstance(vendor, str)
            or not isinstance(requested_url, str)
            or capture.get("vendor") != vendor
            or capture.get("requested_url") != requested_url
        ):
            raise ValueError("capture manifest URL or vendor does not match the source inventory")
        assert_pinned_vendor_host(vendor, requested_url)
        assert_pinned_vendor_host(vendor, capture.get("final_url"))


def _validate_span_ledger(
    ledger_path: Path,
    *,
    verified: dict[str, Any],
    capture_manifest: dict[str, Any],
    captures: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    ledger = _read_json(ledger_path, label="source span ledger")
    capture_binding = ledger.get("capture_manifest")
    source_binding = ledger.get("source_bundle")
    if (
        ledger.get("schema_version") != SPAN_LEDGER_SCHEMA_VERSION
        or ledger.get("status") != "PASS"
        or ledger.get("ledger_kind") != "first_party_evidence_span_proposals"
        or ledger.get("canonical_digest", {}).get("algorithm") != "sha256"
        or ledger.get("canonical_digest", {}).get("scope")
        != "artifact_without_canonical_digest"
        or ledger.get("canonical_digest", {}).get("value") != artifact_digest(ledger)
        or not isinstance(capture_binding, dict)
        or capture_binding.get("sha256") != sha256_file(capture_manifest["_path"])
        or capture_binding.get("capture_id") != capture_manifest["capture_id"]
        or not isinstance(source_binding, dict)
        or source_binding.get("sha256") != verified["bundle_sha256"]
        or source_binding.get("bundle_manifest_sha256") != verified["bundle_manifest_sha256"]
    ):
        raise ValueError("source span ledger is not bound to this capture and source bundle")
    proposals = ledger.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise ValueError("source span ledger has no proposals")
    evidence_by_id = {
        row.get("evidence_id"): row
        for row in verified["data"]["evidence_records"]
    }
    facts_by_id = {row.get("fact_id"): row for row in verified["data"]["fact_candidates"]}
    if None in evidence_by_id or None in facts_by_id:
        raise ValueError("verified source bundle has invalid evidence or fact identities")
    documents: dict[str, Any] = {}
    for source_id, capture in captures.items():
        raw = _safe_capture_raw_path(capture_manifest["_path"], capture).read_bytes()
        try:
            documents[source_id] = validate_capture_document(capture, raw)
        except SourceSpanError as exc:
            raise ValueError("capture cannot reproduce a source span ledger") from exc

    by_id: dict[str, dict[str, Any]] = {}
    for proposal in proposals:
        proposal_id = proposal.get("span_proposal_id") if isinstance(proposal, dict) else None
        if not isinstance(proposal, dict) or not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("source span ledger has an invalid proposal identity")
        if proposal_id in by_id:
            raise ValueError("source span ledger has duplicate proposal identities")
        source_id = proposal.get("source_id")
        fact_id = proposal.get("fact_id")
        evidence_id = proposal.get("evidence_id")
        capture_binding = proposal.get("capture_binding")
        source_version = proposal.get("source_version")
        validation = proposal.get("validation")
        quote = proposal.get("quote")
        start = proposal.get("visible_start_offset")
        end = proposal.get("visible_end_offset")
        if (
            proposal.get("status") != "PROPOSED_FOR_HITL"
            or proposal.get("review_state") != "PROPOSED_FOR_HITL"
            or source_id not in captures
            or not isinstance(fact_id, str)
            or fact_id not in facts_by_id
            or not isinstance(evidence_id, str)
            or evidence_id not in evidence_by_id
            or not isinstance(capture_binding, dict)
            or capture_binding.get("capture_id") != capture_manifest["capture_id"]
            or capture_binding.get("capture_manifest_sha256")
            != sha256_file(capture_manifest["_path"])
            or not isinstance(source_version, dict)
            or not isinstance(validation, dict)
            or not all(
                validation.get(key) is True
                for key in (
                    "raw_hash_valid",
                    "visible_hash_valid",
                    "quote_at_offsets_valid",
                    "selected_heading_structural_valid",
                    "context_hashes_valid",
                )
            )
            or not isinstance(quote, str)
            or not quote.strip()
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end - start != len(quote)
            or proposal.get("quote_sha256") != sha256_bytes(quote.encode("utf-8"))
        ):
            raise ValueError("source span proposal is incomplete or not reproducibly valid")
        capture = captures[source_id]
        expected = {
            "requested_url": capture["requested_url"],
            "final_url": capture["final_url"],
            "captured_at": capture["captured_at"],
            "raw_response_sha256": capture["raw_sha256"],
            "visible_text_sha256": capture["visible_text_sha256"],
        }
        if any(source_version.get(key) != value for key, value in expected.items()):
            raise ValueError("source span proposal has drifted from its frozen capture")
        if capture_binding.get("raw_relative_path") != capture.get("raw_path"):
            raise ValueError("source span proposal points at a different frozen raw response")
        fact = facts_by_id[fact_id]
        evidence = evidence_by_id[evidence_id]
        if (
            evidence.get("source_id") != source_id
            or evidence_id not in fact.get("evidence_ids_json", [])
            or fact.get("source_ids_json") != [source_id]
        ):
            raise ValueError("source span proposal crosses verified fact/evidence lineage")
        expected_proposal_id = stable_id(
            "span-proposal",
            capture_manifest["capture_id"],
            fact_id,
            evidence_id,
            capture["raw_sha256"],
            start,
            end,
            proposal["quote_sha256"],
            length=32,
        )
        if proposal_id != expected_proposal_id:
            raise ValueError("source span proposal identity is not reproducible from the frozen capture")
        try:
            validate_span_proposal(proposal, documents[source_id], evidence)
        except SourceSpanError as exc:
            raise ValueError("source span proposal does not reproduce from the frozen capture") from exc
        by_id[proposal_id] = proposal
    return by_id


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


def _selection_attestation(
    selection: dict[str, Any], *, ledger_path: Path
) -> dict[str, Any]:
    required = {
        "schema_version",
        "span_ledger_sha256",
        "reviewer",
        "decided_at",
        "human_attestation",
        "reviewer_authorization_reference",
        "decisions",
        "guidance_pattern_decisions",
    }
    if set(selection) != required:
        raise ValueError("span selection ledger has an unsupported top-level shape")
    reviewer = selection.get("reviewer")
    authorization_reference = selection.get("reviewer_authorization_reference")
    if (
        selection.get("schema_version") != SELECTION_SCHEMA_VERSION
        or selection.get("span_ledger_sha256") != sha256_file(ledger_path)
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or len(reviewer) > 512
        or selection.get("human_attestation") != HUMAN_ATTESTATION
        or not isinstance(selection.get("decisions"), list)
        or not isinstance(selection.get("guidance_pattern_decisions"), list)
        or (
            authorization_reference is not None
            and (
                not isinstance(authorization_reference, str)
                or not authorization_reference.strip()
                or len(authorization_reference) > 1024
            )
        )
    ):
        raise ValueError("span selection ledger has an invalid human review attestation")
    return {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "span_ledger_sha256": selection["span_ledger_sha256"],
        "reviewer": reviewer.strip(),
        "decided_at": _require_aware_timestamp(selection.get("decided_at"), label="decided_at"),
        "human_attestation": HUMAN_ATTESTATION,
        "reviewer_authorization_reference": authorization_reference,
    }


def _selection_index(
    selection_path: Path,
    *,
    ledger_path: Path,
    facts_by_id: dict[str, dict[str, Any]],
    guidance_pattern_by_id: dict[str, str],
    proposals: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    selection = _read_json(selection_path, label="span selection ledger")
    attestation = _selection_attestation(selection, ledger_path=ledger_path)
    result: dict[str, dict[str, Any]] = {}
    allowed_fields = {
        "fact_id",
        "decision",
        "span_proposal_id",
        "rewritten_claim",
        "rationale",
    }
    for row in selection["decisions"]:
        fact_id = row.get("fact_id") if isinstance(row, dict) else None
        decision = row.get("decision") if isinstance(row, dict) else None
        rationale = row.get("rationale") if isinstance(row, dict) else None
        if (
            not isinstance(row, dict)
            or fact_id not in facts_by_id
            or fact_id in result
            or decision not in ALLOWED_SELECTION_DECISIONS
            or set(row) - allowed_fields
            or not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 4000
        ):
            raise ValueError("span selection ledger has an invalid or duplicate factual decision")
        proposal_id = row.get("span_proposal_id")
        rewritten = row.get("rewritten_claim")
        if decision in {"CONFIRM", "REWRITE"}:
            proposal = proposals.get(proposal_id) if isinstance(proposal_id, str) else None
            if proposal is None or proposal.get("fact_id") != fact_id:
                raise ValueError("selected source span does not belong to its fact")
            if decision == "REWRITE":
                if not isinstance(rewritten, str) or not rewritten.strip() or len(rewritten) > 4000:
                    raise ValueError("REWRITE requires a bounded nonempty rewritten_claim")
            elif rewritten is not None and rewritten != "":
                raise ValueError("CONFIRM cannot carry a rewritten claim")
        elif proposal_id is not None or (rewritten is not None and rewritten != ""):
            raise ValueError("HOLD and REJECT cannot select or rewrite source evidence")
        result[fact_id] = row
    if set(result) != set(facts_by_id):
        missing = sorted(set(facts_by_id) - set(result))
        raise ValueError(f"span selection ledger must decide every source fact; missing {missing}")

    guidance_result: dict[str, dict[str, Any]] = {}
    allowed_guidance_fields = {"source_item_id", "content_kind", "decision", "rationale"}
    for row in selection["guidance_pattern_decisions"]:
        source_item_id = row.get("source_item_id") if isinstance(row, dict) else None
        content_kind = row.get("content_kind") if isinstance(row, dict) else None
        decision = row.get("decision") if isinstance(row, dict) else None
        rationale = row.get("rationale") if isinstance(row, dict) else None
        if (
            not isinstance(row, dict)
            or source_item_id not in guidance_pattern_by_id
            or source_item_id in guidance_result
            or content_kind != guidance_pattern_by_id[source_item_id]
            or decision not in ALLOWED_GUIDANCE_DECISIONS
            or set(row) != allowed_guidance_fields
            or not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 4000
        ):
            raise ValueError("span selection ledger has an invalid guidance/pattern decision")
        guidance_result[source_item_id] = row
    if set(guidance_result) != set(guidance_pattern_by_id):
        missing = sorted(set(guidance_pattern_by_id) - set(guidance_result))
        raise ValueError(
            "span selection ledger must decide every guidance/pattern item; "
            f"missing {missing}"
        )
    return result, guidance_result, attestation


def _source(
    source_row: dict[str, Any],
    capture: dict[str, Any],
    *,
    audiences: list[str],
) -> dict[str, Any]:
    if (
        capture.get("vendor") != source_row.get("vendor")
        or capture.get("requested_url") != source_row.get("url")
    ):
        raise ValueError("capture source URL or vendor does not match the source inventory")
    assert_pinned_vendor_host(source_row.get("vendor"), capture.get("requested_url"))
    assert_pinned_vendor_host(source_row.get("vendor"), capture.get("final_url"))
    return {
        "source_id": source_row["source_id"],
        "source_version_id": stable_id(
            "source-version",
            source_row["source_id"],
            capture["final_url"],
            capture["raw_sha256"],
            length=28,
        ),
        "title": source_row["title"],
        "source_location": capture["final_url"],
        "source_date": str(capture["captured_at"]).split("T", 1)[0],
        "classification": "public",
        "audiences": audiences,
        "source_posture": "external_first_party",
        "authority_rank": 8,
        "vendor": source_row["vendor"],
        "url": capture["final_url"],
        "first_party": True,
    }


def _span_evidence(
    *,
    proposal: dict[str, Any],
    source: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    quote = proposal["quote"]
    quote_sha256 = proposal["quote_sha256"]
    evidence_id = stable_id(
        "evidence-current-span",
        source["source_version_id"],
        proposal["fact_id"],
        quote_sha256,
        length=28,
    )
    anchor = proposal.get("anchor") or "captured-source-span"
    locator = {
        "kind": "uri_fragment",
        "value": (
            f"{source['url']}#{anchor}:V{proposal['visible_start_offset']}"
            f"-{proposal['visible_end_offset']}"
        ),
        "quote_digest": quote_sha256,
    }
    evidence = {
        "evidence_id": evidence_id,
        "source_id": source["source_id"],
        "source_version_id": source["source_version_id"],
        "locator": locator,
        "relationship": "supports",
        "directness": "direct",
        "authority_rank": source["authority_rank"],
        "content_digest": quote_sha256,
        "verification_state": "fresh_capture_selected_span",
    }
    provenance = {
        "span_proposal_id": proposal["span_proposal_id"],
        "prior_evidence_id": proposal["evidence_id"],
        "fact_id": proposal["fact_id"],
        "source_id": source["source_id"],
        "source_version_id": source["source_version_id"],
        "capture_id": proposal["capture_binding"]["capture_id"],
        "raw_response_sha256": proposal["source_version"]["raw_response_sha256"],
        "visible_text_sha256": proposal["source_version"]["visible_text_sha256"],
        "requested_url": proposal["source_version"]["requested_url"],
        "final_url": proposal["source_version"]["final_url"],
        "captured_at": proposal["source_version"]["captured_at"],
        "heading_path": proposal["heading_path"],
        "anchor": proposal["anchor"],
        "visible_start_offset": proposal["visible_start_offset"],
        "visible_end_offset": proposal["visible_end_offset"],
        "quote": quote,
        "quote_sha256": quote_sha256,
        "context_before_sha256": proposal["context_before_sha256"],
        "context_after_sha256": proposal["context_after_sha256"],
        "section_visible_text_sha256": proposal["section_visible_text_sha256"],
        "extractor": proposal["selection"],
        "status": "SELECTED_FOR_HITL",
    }
    return evidence, provenance


def _mention(
    candidate_id: str,
    text: str,
    context: str,
    *,
    stable_identifier: str | None = None,
) -> dict[str, Any]:
    return {
        "mention_id": stable_id("mention", candidate_id, text, length=24),
        "reported_text": text,
        "stable_identifier": stable_identifier,
        "context": context,
    }


def _assertion_candidate(
    *,
    fact: dict[str, Any],
    source: dict[str, Any],
    evidence: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    original_id = fact["fact_id"]
    text = (
        decision["rewritten_claim"].strip()
        if decision["decision"] == "REWRITE"
        else fact["embedding_text"]
    )
    source_item_id = (
        original_id
        if decision["decision"] == "CONFIRM"
        else stable_id("rewritten-fact", original_id, text, length=28)
    )
    candidate_id = stable_id(
        "candidate",
        source["source_version_id"],
        source_item_id,
        text,
        evidence["evidence_id"],
        length=26,
    )
    subject = fact.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError(f"fact has no atomic subject: {original_id}")
    return {
        "candidate_id": candidate_id,
        "source_item_id": source_item_id,
        "candidate_type": "assertion",
        "assertion_type": "candidate_assertion",
        "normalized_text": text,
        "materiality": "medium",
        "entity_mentions": [
            _mention(
                candidate_id,
                subject,
                f"First-party {source['vendor']} concept from {source['source_id']}; "
                "create only a source-scoped external concept unless separately approved.",
            )
        ],
        "evidence_ids": [evidence["evidence_id"]],
        "source_locator": evidence["locator"],
        "classification": "public",
        "audiences": source["audiences"],
        "extraction_confidence": 0.7,
        "retrieval_kind": "assertion",
        "answer_use": "candidate_hypothesis",
        "review_question": (
            f"Does the selected frozen first-party quote directly entail {original_id} "
            "in its entirety? Confirm only as a candidate, rewrite more narrowly, hold, or reject."
        ),
        "promotion_status": "candidate_evidence_selected_span_hitl_required",
    }


def _citation_candidate(
    *, source: dict[str, Any], evidence: dict[str, Any], quote: str
) -> dict[str, Any]:
    candidate_id = stable_id(
        "candidate-evidence",
        source["source_version_id"],
        evidence["evidence_id"],
        evidence["content_digest"],
        length=26,
    )
    return {
        "candidate_id": candidate_id,
        "source_item_id": evidence["evidence_id"],
        "candidate_type": "source_chunk",
        "assertion_type": "candidate_assertion",
        "normalized_text": quote,
        "materiality": "medium",
        "entity_mentions": [
            _mention(
                candidate_id,
                source["title"],
                "First-party source document attached to a citation-only evidence span.",
                stable_identifier=source["source_id"],
            )
        ],
        "evidence_ids": [evidence["evidence_id"]],
        "source_locator": evidence["locator"],
        "classification": "public",
        "audiences": source["audiences"],
        "extraction_confidence": 0.6,
        "retrieval_kind": "evidence_span",
        "answer_use": "citation_only",
        "review_question": (
            "Does this exact frozen source quote and locator remain correctly recorded as "
            "citation-only evidence? It is not eligible for factual promotion."
        ),
        "promotion_status": "citation_only",
    }


def _packet(
    *,
    verified: dict[str, Any],
    source: dict[str, Any],
    evidence: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    fresh_capture: dict[str, Any] | None,
    evidence_spans: dict[str, dict[str, Any]] | None,
    selection_attestation: dict[str, Any] | None,
    selection_digest: str | None,
    selection_rows: list[dict[str, Any]] | None,
    lineage_kind: str,
) -> dict[str, Any]:
    if not evidence or not candidates:
        raise ValueError("final A2A package needs evidence and candidates")
    extensions: dict[str, Any] = {
        "com.partner_advisory.nee.a2a-packet-lineage": {
            "adapter_source_kind": SOURCE_KIND,
            "lineage_kind": lineage_kind,
            "outer_bundle_filename": ARCHIVED_SOURCE_BUNDLE_FILENAME,
            "outer_bundle_sha256": verified["bundle_sha256"],
            "outer_bundle_manifest_sha256": verified["bundle_manifest_sha256"],
            "upstream_packet_id": verified["upstream"]["packet_id"],
            "upstream_packet_manifest_sha256": verified["upstream"]["packet_manifest_sha256"],
            "upstream_signature_valid": True,
            "upstream_manifest_hashes_valid": True,
            "upstream_trust_scope": PACKET_TRUST_SCOPE,
            "fresh_first_party_capture_included": fresh_capture is not None,
            "automatic_fact_promotion_enabled": False,
        }
    }
    if fresh_capture is not None:
        extensions["com.partner_advisory.nee.a2a-fresh-capture"] = fresh_capture
    if evidence_spans is not None:
        extensions["com.partner_advisory.nee.a2a-evidence-spans"] = evidence_spans
    if selection_attestation is not None and selection_rows is not None:
        extensions["com.partner_advisory.nee.a2a-source-span-selection"] = (
            selection_attestation
            | {
                "selection_ledger_sha256": selection_digest,
                "decisions": selection_rows,
            }
        )
    artifact = {
        "contract_id": CONTRACT_ID,
        "schema_version": "1.0.0",
        "artifact_id": f"a2a-reviewed-packet-{slug(source['source_id'])}",
        "packet": {
            "packet_id": verified["upstream"]["packet_id"],
            "source_zip_sha256": verified["bundle_sha256"],
            "packet_manifest_sha256": verified["bundle_manifest_sha256"],
            "signature_status": "verified_integrity_only",
            "trust_scope": PACKET_TRUST_SCOPE,
        },
        "source": source,
        "evidence": evidence,
        "candidates": candidates,
        "adapter": {"name": "nee-a2a-fact-vector-packet", "version": "1.0.0"},
        "extensions": extensions,
    }
    return seal_artifact(artifact)


def finalize_packages(
    *,
    bundle_path: Path,
    capture_manifest_path: Path,
    span_ledger_path: Path,
    selection_path: Path,
    output_dir: Path,
    audiences: list[str],
    runtime_root: Path | None = None,
    archive_root_id: str | None = None,
) -> dict[str, Any]:
    """Verify all bindings and produce immutable final candidate packages."""

    verified = read_and_verify_bundle(bundle_path)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError("--output-dir must not already exist")
    normalized_audiences = list(dict.fromkeys(value.strip() for value in audiences))
    if not normalized_audiences or any(not value for value in normalized_audiences):
        raise ValueError("--audience values must not be blank")
    capture_manifest_path = capture_manifest_path.resolve()
    capture_manifest, captures = _capture_index(capture_manifest_path, verified)
    data = verified["data"]
    source_rows = {row.get("source_id"): row for row in data["sources"]}
    if len(source_rows) != len(data["sources"]) or None in source_rows:
        raise ValueError("source inventory has duplicate or missing source IDs")
    _validate_capture_source_bindings(captures, source_rows)
    # Keep the bound path out of the serialized source data; it is used only
    # for independently validating the ledger hash.
    capture_manifest["_path"] = capture_manifest_path
    proposals = _validate_span_ledger(
        span_ledger_path.resolve(),
        verified=verified,
        capture_manifest=capture_manifest,
        captures=captures,
    )
    facts_by_id = {row.get("fact_id"): row for row in data["fact_candidates"]}
    if len(facts_by_id) != len(data["fact_candidates"]) or None in facts_by_id:
        raise ValueError("fact inventory has duplicate or missing fact IDs")
    guidance_pattern_by_id = {
        row.get("practice_id"): "guidance" for row in data["guidance_practices"]
    } | {row.get("pattern_id"): "pattern" for row in data["pattern_guidance"]}
    if (
        None in guidance_pattern_by_id
        or len(guidance_pattern_by_id)
        != len(data["guidance_practices"]) + len(data["pattern_guidance"])
    ):
        raise ValueError("guidance/pattern inventory has duplicate or missing item IDs")
    selection_by_fact, selection_by_guidance, selection_attestation = _selection_index(
        selection_path.resolve(),
        ledger_path=span_ledger_path.resolve(),
        facts_by_id=facts_by_id,
        guidance_pattern_by_id=guidance_pattern_by_id,
        proposals=proposals,
    )
    selection_digest = sha256_file(selection_path.resolve())
    accepted_by_source: dict[str, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = (
        defaultdict(list)
    )
    held_or_rejected: list[dict[str, Any]] = []
    for fact_id, fact in sorted(facts_by_id.items()):
        decision = selection_by_fact[fact_id]
        source_ids = fact.get("source_ids_json")
        if (
            not isinstance(source_ids, list)
            or len(source_ids) != 1
            or source_ids[0] not in source_rows
        ):
            raise ValueError(f"fact has invalid source lineage: {fact_id}")
        if decision["decision"] in {"CONFIRM", "REWRITE"}:
            proposal = proposals[decision["span_proposal_id"]]
            if proposal["source_id"] != source_ids[0]:
                raise ValueError("selected source span crosses a fact source boundary")
            accepted_by_source[source_ids[0]].append((fact, decision, proposal))
        else:
            held_or_rejected.append(
                {
                    "fact_id": fact_id,
                    "decision": decision["decision"],
                    "rationale": decision.get("rationale"),
                }
            )
    packets: list[tuple[str, dict[str, Any]]] = []
    for source_id in sorted(accepted_by_source):
        source = _source(
            source_rows[source_id],
            captures[source_id],
            audiences=normalized_audiences,
        )
        evidence_rows: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        provenance: dict[str, dict[str, Any]] = {}
        source_selection_rows: list[dict[str, Any]] = []
        for fact, decision, proposal in accepted_by_source[source_id]:
            evidence, span = _span_evidence(proposal=proposal, source=source)
            if evidence["evidence_id"] in provenance:
                raise ValueError("selected A2A source spans must be fact-scoped and unique")
            evidence_rows.append(evidence)
            provenance[evidence["evidence_id"]] = span
            candidates.append(
                _assertion_candidate(fact=fact, source=source, evidence=evidence, decision=decision)
            )
            candidates.append(
                _citation_candidate(source=source, evidence=evidence, quote=span["quote"])
            )
            source_selection_rows.append(
                {
                    "fact_id": fact["fact_id"],
                    "decision": decision["decision"],
                    "span_proposal_id": proposal["span_proposal_id"],
                    "evidence_id": evidence["evidence_id"],
                    "source_id": source["source_id"],
                    "rewritten_claim": decision.get("rewritten_claim"),
                    "rationale": decision["rationale"],
                    "quote_sha256": span["quote_sha256"],
                    "visible_start_offset": span["visible_start_offset"],
                    "visible_end_offset": span["visible_end_offset"],
                    "raw_response_sha256": span["raw_response_sha256"],
                    "visible_text_sha256": span["visible_text_sha256"],
                    "canonical_locator": evidence["locator"],
                }
            )
        fresh_capture = {
            "capture_id": capture_manifest["capture_id"],
            "capture_manifest_sha256": sha256_file(capture_manifest_path),
            "captured_at": captures[source_id]["captured_at"],
            "requested_url": captures[source_id]["requested_url"],
            "final_url": captures[source_id]["final_url"],
            "raw_response_sha256": captures[source_id]["raw_sha256"],
            "visible_text_sha256": captures[source_id]["visible_text_sha256"],
            "evidence_outcomes": {row["evidence_id"]: SPAN_OUTCOME for row in evidence_rows},
            "source_span_ledger_sha256": sha256_file(span_ledger_path.resolve()),
            "selection_ledger_sha256": selection_digest,
        }
        packet = _packet(
            verified=verified,
            source=source,
            evidence=evidence_rows,
            candidates=candidates,
            fresh_capture=fresh_capture,
            evidence_spans=provenance,
            selection_attestation=selection_attestation,
            selection_digest=selection_digest,
            selection_rows=source_selection_rows,
            lineage_kind="first_party_selected_current_span_candidate",
        )
        validate_artifact(packet)
        packets.append((f"{slug(source_id)}.reviewed.intake.json", packet))

    # Guidance and patterns are retained, but only in their distinct non-fact
    # retrieval family.  They never depend on a source-span factual decision.
    built_at = verified["manifest"].get("built_at")
    if not isinstance(built_at, str):
        raise ValueError("BUNDLE_MANIFEST has no built_at timestamp")
    guidance_source = _derived_source(
        source_id="SRC-NEE-A2A-GUIDANCE",
        title="Derived OpenAI and Anthropic A2A guidance from governed v3 packet",
        url="urn:nee:a2a-governed-ingestion-v3:guidance",
        bundle_sha256=verified["bundle_sha256"],
        built_at=built_at,
        audiences=normalized_audiences,
    )
    kept_guidance_rows = [
        row
        for row in data["guidance_practices"]
        if selection_by_guidance[row["practice_id"]]["decision"] == "KEEP_GUIDANCE"
    ]
    guidance_evidence = [
        _derived_evidence(
            source=guidance_source,
            item_id=row["practice_id"],
            text=row["embedding_text"],
            table="guidance_practices",
        )
        for row in kept_guidance_rows
    ]
    if guidance_evidence:
        guidance_packet = _packet(
            verified=verified,
            source=guidance_source,
            evidence=guidance_evidence,
            candidates=[
                _guidance_candidate(row, evidence["evidence_id"], guidance_source)
                for row, evidence in zip(kept_guidance_rows, guidance_evidence, strict=True)
            ],
            fresh_capture=None,
            evidence_spans=None,
            selection_attestation=None,
            selection_digest=None,
            selection_rows=None,
            lineage_kind="derived_guidance_nonfactual",
        )
        validate_artifact(guidance_packet)
        packets.append(("derived-guidance.intake.json", guidance_packet))
    pattern_source = _derived_source(
        source_id="SRC-NEE-A2A-PATTERN-GUIDANCE",
        title="Derived OpenAI and Anthropic A2A routing guidance from governed v3 packet",
        url="urn:nee:a2a-governed-ingestion-v3:pattern-guidance",
        bundle_sha256=verified["bundle_sha256"],
        built_at=built_at,
        audiences=normalized_audiences,
    )
    kept_pattern_rows = [
        row
        for row in data["pattern_guidance"]
        if selection_by_guidance[row["pattern_id"]]["decision"] == "KEEP_GUIDANCE"
    ]
    pattern_evidence = [
        _derived_evidence(
            source=pattern_source,
            item_id=row["pattern_id"],
            text=row["embedding_text"],
            table="pattern_guidance",
        )
        for row in kept_pattern_rows
    ]
    if pattern_evidence:
        pattern_packet = _packet(
            verified=verified,
            source=pattern_source,
            evidence=pattern_evidence,
            candidates=[
                _pattern_candidate(row, evidence["evidence_id"], pattern_source)
                for row, evidence in zip(kept_pattern_rows, pattern_evidence, strict=True)
            ],
            fresh_capture=None,
            evidence_spans=None,
            selection_attestation=None,
            selection_digest=None,
            selection_rows=None,
            lineage_kind="derived_pattern_guidance_nonfactual",
        )
        validate_artifact(pattern_packet)
        packets.append(("pattern-guidance.intake.json", pattern_packet))

    if (runtime_root is None) != (archive_root_id is None):
        raise ValueError(
            "finalization archive requires both runtime_root and archive_root_id"
        )
    archive = (
        stage_finalization_archive(
            runtime_root=runtime_root,
            archive_root_id=archive_root_id,
            bundle_path=bundle_path,
            capture_manifest_path=capture_manifest_path,
            span_ledger_path=span_ledger_path,
            selection_path=selection_path,
        )
        if runtime_root is not None
        else None
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    for filename, packet in packets:
        write_json_exclusive(output_dir / filename, packet)
    package_rows = [
        {
            "relative_path": filename,
            "file_sha256": sha256_file(output_dir / filename),
            "canonical_digest": packet["canonical_digest"]["value"],
            "artifact_id": packet["artifact_id"],
            "source_id": packet["source"]["source_id"],
        }
        for filename, packet in packets
    ]
    receipt = (
        create_finalization_receipt(
            source_bundle={
                "sha256": verified["bundle_sha256"],
                "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
            },
            capture={
                "capture_id": capture_manifest["capture_id"],
                "capture_manifest_sha256": sha256_file(capture_manifest_path),
                "span_ledger_sha256": sha256_file(span_ledger_path.resolve()),
                "selection_ledger_sha256": selection_digest,
            },
            archive=archive,
            packages=package_rows,
            audiences=normalized_audiences,
        )
        if archive is not None
        else None
    )
    if receipt is not None:
        write_json_exclusive(output_dir / "finalization_receipt.json", receipt)
    report = {
        "schema_version": "1.0.0",
        "status": "PASS" if packets else "NO_CONTENT",
        "terminal_disposition": (
            "CANDIDATE_PACKETS_READY" if packets else "ALL_SOURCE_ITEMS_HELD_OR_REJECTED"
        ),
        "source_bundle": {
            "path": str(verified["bundle_path"]),
            "sha256": verified["bundle_sha256"],
            "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
        },
        "capture": {
            "capture_id": capture_manifest["capture_id"],
            "capture_manifest_sha256": sha256_file(capture_manifest_path),
            "span_ledger_sha256": sha256_file(span_ledger_path.resolve()),
            "selection_ledger_sha256": selection_digest,
        },
        "source_facts": {
            "total": len(facts_by_id),
            "selected_candidate_count": sum(
                1 for row in selection_by_fact.values() if row["decision"] in {"CONFIRM", "REWRITE"}
            ),
            "held_or_rejected": held_or_rejected,
        },
        "guidance_patterns": {
            "total": len(guidance_pattern_by_id),
            "kept_count": sum(
                row["decision"] == "KEEP_GUIDANCE"
                for row in selection_by_guidance.values()
            ),
            "held_or_rejected": [
                {
                    "source_item_id": source_item_id,
                    "content_kind": row["content_kind"],
                    "decision": row["decision"],
                    "rationale": row["rationale"],
                }
                for source_item_id, row in sorted(selection_by_guidance.items())
                if row["decision"] != "KEEP_GUIDANCE"
            ],
        },
        "packages": [
            {
                "path": filename,
                "file_sha256": sha256_file(output_dir / filename),
                "artifact_id": packet["artifact_id"],
                "source_id": packet["source"]["source_id"],
                "canonical_digest": packet["canonical_digest"]["value"],
                "candidate_count": len(packet["candidates"]),
            }
            for filename, packet in packets
        ],
        "finalization_receipt": (
            {
                "path": "finalization_receipt.json",
                "receipt_id": receipt["receipt_id"],
                "canonical_digest": receipt["canonical_digest"]["value"],
                "file_sha256": sha256_file(output_dir / "finalization_receipt.json"),
                "archive_root_id": archive["root_id"],
            }
            if receipt is not None
            else None
        ),
        "promotion": {
            "automatic_fact_promotion": False,
            "individual_intake_hitl_still_required": True,
            "u0_graph_mutation_count": 0,
            "u0_embedding_generation_count": 0,
            "ingestion_noop_completion_required": not packets,
        },
    }
    write_json_exclusive(output_dir / "finalization_manifest.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--span-ledger", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--runtime-root",
        type=Path,
        required=True,
        help="Governed runtime root; archive is always created beneath source_captures/.",
    )
    parser.add_argument(
        "--archive-root-id",
        required=True,
        help="Safe source_captures child name used for the replay archive.",
    )
    parser.add_argument(
        "--audience",
        action="append",
        dest="audiences",
        default=["internal_consulting", "nee-engineering"],
    )
    args = parser.parse_args()
    try:
        report = finalize_packages(
            bundle_path=args.bundle,
            capture_manifest_path=args.capture_manifest,
            span_ledger_path=args.span_ledger,
            selection_path=args.selection,
            output_dir=args.output_dir,
            audiences=args.audiences,
            runtime_root=args.runtime_root,
            archive_root_id=args.archive_root_id,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
