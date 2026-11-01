"""Span proposal construction, validation, and runtime A2A verification."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dual_pipeline.core import (
    assert_safe_mutable_path,
    normalize_space,
    sha256_bytes,
    sha256_file,
    stable_id,
)

from .constants import CONTEXT_CHARACTERS, MAX_QUOTE_CHARACTERS, SELECTION_METHOD
from .extractor import (
    _anchor_hint_matches,
    _candidate_spans,
    _heading_path,
    _score_span,
    extract_visible_document,
    sections_for_evidence,
)
from .models import SourceSpanError, VisibleDocument


def validate_capture_document(capture: dict[str, Any], raw: bytes) -> VisibleDocument:
    """Verify raw and visible-text hashes before any source span can be proposed."""
    raw_digest = sha256_bytes(raw)
    expected_raw_digest = capture.get("raw_sha256")
    if not isinstance(expected_raw_digest, str) or raw_digest != expected_raw_digest:
        raise SourceSpanError("captured raw response hash does not match capture manifest")
    expected_bytes = capture.get("raw_bytes")
    if not isinstance(expected_bytes, int) or len(raw) != expected_bytes:
        raise SourceSpanError("captured raw response byte count does not match capture manifest")
    headers = capture.get("response_headers")
    content_type = headers.get("Content-Type") if isinstance(headers, dict) else None
    if content_type is not None and not isinstance(content_type, str):
        raise SourceSpanError("captured response Content-Type is invalid")
    document = extract_visible_document(raw, content_type)
    expected_visible_digest = capture.get("visible_text_sha256")
    actual_visible_digest = sha256_bytes(document.visible_text.encode("utf-8"))
    if (
        not isinstance(expected_visible_digest, str)
        or actual_visible_digest != expected_visible_digest
    ):
        raise SourceSpanError("captured visible-text hash does not match capture manifest")
    expected_characters = capture.get("visible_text_characters")
    if (
        not isinstance(expected_characters, int)
        or len(document.visible_text) != expected_characters
    ):
        raise SourceSpanError(
            "captured visible-text character count does not match capture manifest"
        )
    return document


def build_span_proposals(
    *,
    document: VisibleDocument,
    fact: dict[str, Any],
    evidence: dict[str, Any],
    capture: dict[str, Any],
    capture_id: str,
    capture_manifest_sha256: str,
    raw_relative_path: str,
    max_proposals: int,
) -> list[dict[str, Any]]:
    """Create ranked, immutable-provenance proposals for one fact/evidence pair.

    This function intentionally emits ``PROPOSED_FOR_HITL`` only. A caller
    must validate raw bytes with :func:`validate_capture_document` before use.
    """
    if not isinstance(max_proposals, int) or max_proposals < 1 or max_proposals > 10:
        raise SourceSpanError("max_proposals must be an integer from 1 through 10")
    fact_id = fact.get("fact_id")
    evidence_id = evidence.get("evidence_id")
    source_id = evidence.get("source_id")
    if not all(isinstance(value, str) and value for value in (fact_id, evidence_id, source_id)):
        raise SourceSpanError("fact/evidence lacks a stable identifier")
    preview_value = evidence.get("excerpt_preview")
    preview = normalize_space(preview_value) if isinstance(preview_value, str) else None
    sections, historical_heading_match, historical_anchor_match = sections_for_evidence(
        document, evidence
    )
    scored: list[tuple[int, int, int, Any, bool, list[str], list[str]]] = []
    for start, end, section, exact_preview in _candidate_spans(document, sections, preview):
        quote = document.visible_text[start:end]
        if not quote or len(quote) > MAX_QUOTE_CHARACTERS:
            continue
        score, matched_terms, locator_matched_terms = _score_span(
            quote,
            fact,
            preview,
            evidence["heading"],
            section.heading.text,
            exact_preview,
        )
        scored.append(
            (score, start, end, section, exact_preview, matched_terms, locator_matched_terms)
        )
    if not scored:
        raise SourceSpanError(f"no bounded candidate span found for fact {fact_id}")
    scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3].heading.anchor))
    selected = scored[:max_proposals]
    raw_digest = capture["raw_sha256"]
    visible_digest = sha256_bytes(document.visible_text.encode("utf-8"))
    final_url = capture.get("final_url")
    requested_url = capture.get("requested_url")
    captured_at = capture.get("captured_at")
    if not all(
        isinstance(value, str) and value for value in (final_url, requested_url, captured_at)
    ):
        raise SourceSpanError("capture does not contain complete source-version provenance")
    proposals: list[dict[str, Any]] = []
    for (
        rank,
        (score, start, end, section, exact_preview, matched_terms, locator_matched_terms),
    ) in enumerate(selected, start=1):
        quote = document.visible_text[start:end]
        quote_digest = sha256_bytes(quote.encode("utf-8"))
        before = document.visible_text[max(0, start - CONTEXT_CHARACTERS) : start]
        after = document.visible_text[
            end : min(len(document.visible_text), end + CONTEXT_CHARACTERS)
        ]
        proposal = {
            "span_proposal_id": stable_id(
                "span-proposal",
                capture_id,
                fact_id,
                evidence_id,
                raw_digest,
                start,
                end,
                quote_digest,
                length=32,
            ),
            "status": "PROPOSED_FOR_HITL",
            "review_state": "PROPOSED_FOR_HITL",
            "fact_id": fact_id,
            "evidence_id": evidence_id,
            "source_id": source_id,
            "capture_binding": {
                "capture_id": capture_id,
                "capture_manifest_sha256": capture_manifest_sha256,
                "raw_relative_path": raw_relative_path,
            },
            "source_version": {
                "requested_url": requested_url,
                "final_url": final_url,
                "captured_at": captured_at,
                "raw_response_sha256": raw_digest,
                "raw_response_bytes": capture["raw_bytes"],
                "visible_text_sha256": visible_digest,
                "visible_text_characters": len(document.visible_text),
            },
            "evidence_locator": {
                "heading": evidence["heading"],
                "anchor_hint": evidence.get("anchor_hint"),
                "original_locator_type": evidence.get("locator_type"),
            },
            "heading_path": _heading_path(section.heading),
            "anchor": section.heading.anchor,
            "visible_start_offset": start,
            "visible_end_offset": end,
            "quote": quote,
            "quote_sha256": quote_digest,
            "quote_character_count": len(quote),
            "context_before_sha256": sha256_bytes(before.encode("utf-8")),
            "context_after_sha256": sha256_bytes(after.encode("utf-8")),
            "section_visible_text_sha256": sha256_bytes(
                document.visible_text[
                    section.visible_start_offset : section.visible_end_offset
                ].encode("utf-8")
            ),
            "selection": {
                "method": SELECTION_METHOD,
                "rank": rank,
                "score": score,
                "matched_terms": matched_terms,
                "matched_locator_terms": locator_matched_terms,
                "historic_preview_exact_match": exact_preview,
                "historic_heading_exact_match": historical_heading_match,
                "historic_anchor_match": historical_anchor_match,
                "locator_reconciliation_required": not (
                    historical_heading_match or historical_anchor_match
                ),
                "max_proposals_per_fact": max_proposals,
            },
            "validation": {
                "raw_hash_valid": True,
                "visible_hash_valid": True,
                "quote_at_offsets_valid": True,
                "selected_heading_structural_valid": True,
                "anchor_hint_matches": _anchor_hint_matches(
                    evidence.get("anchor_hint"), section.heading
                ),
                "context_hashes_valid": True,
            },
            "promotion_effect": "none; proposal requires individual HITL and later UWG approval",
        }
        validate_span_proposal(proposal, document, evidence)
        proposals.append(proposal)
    return proposals


def validate_span_proposal(
    proposal: dict[str, Any], document: VisibleDocument, evidence: dict[str, Any]
) -> None:
    """Fail closed when a proposal cannot be reconstructed from the frozen capture."""
    start = proposal.get("visible_start_offset")
    end = proposal.get("visible_end_offset")
    quote = proposal.get("quote")
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(quote, str):
        raise SourceSpanError("proposal has invalid quote offsets")
    if start < 0 or end <= start or end > len(document.visible_text):
        raise SourceSpanError("proposal quote offsets are outside canonical visible text")
    if document.visible_text[start:end] != quote:
        raise SourceSpanError("proposal quote does not match its canonical visible-text offsets")
    if proposal.get("quote_sha256") != sha256_bytes(quote.encode("utf-8")):
        raise SourceSpanError("proposal quote hash is invalid")
    before = document.visible_text[max(0, start - CONTEXT_CHARACTERS) : start]
    after = document.visible_text[end : min(len(document.visible_text), end + CONTEXT_CHARACTERS)]
    if proposal.get("context_before_sha256") != sha256_bytes(before.encode("utf-8")):
        raise SourceSpanError("proposal before-context hash is invalid")
    if proposal.get("context_after_sha256") != sha256_bytes(after.encode("utf-8")):
        raise SourceSpanError("proposal after-context hash is invalid")
    path = proposal.get("heading_path")
    if not isinstance(path, list) or not path:
        raise SourceSpanError("proposal has no heading path")
    final_heading = path[-1]
    if not isinstance(final_heading, dict):
        raise SourceSpanError("proposal has an invalid selected heading")
    anchor = proposal.get("anchor")
    if not isinstance(anchor, str) or not anchor.strip():
        raise SourceSpanError("proposal has no selected structural anchor")
    if anchor != final_heading.get("anchor"):
        raise SourceSpanError("proposal anchor does not match its selected structural heading")
    if not any(path == _heading_path(heading) for heading in document.headings):
        raise SourceSpanError("proposal heading path is not structural source content")
    selection = proposal.get("selection")
    if not isinstance(selection, dict):
        raise SourceSpanError("proposal has no selection record")
    historical_match = selection.get("historic_heading_exact_match")
    if historical_match is True and final_heading.get("text") != evidence.get("heading"):
        raise SourceSpanError("proposal historic-heading match is inconsistent")
    if selection.get("locator_reconciliation_required") is not (
        not (historical_match is True or selection.get("historic_anchor_match") is True)
    ):
        raise SourceSpanError("proposal locator-reconciliation status is inconsistent")
    source_version = proposal.get("source_version")
    if not isinstance(source_version, dict) or source_version.get(
        "visible_text_sha256"
    ) != sha256_bytes(document.visible_text.encode("utf-8")):
        raise SourceSpanError("proposal visible-text source binding is invalid")


def validate_finalized_span(span: dict[str, Any], document: VisibleDocument) -> None:
    """Reconstruct a finalized selected span from the trusted frozen capture."""
    start = span.get("visible_start_offset")
    end = span.get("visible_end_offset")
    quote = span.get("quote")
    quote_digest = span.get("quote_sha256")
    anchor = span.get("anchor")
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(quote, str)
        or not isinstance(quote_digest, str)
        or not isinstance(anchor, str)
        or not anchor.strip()
    ):
        raise SourceSpanError("finalized source span has invalid structural fields")
    if start < 0 or end <= start or end > len(document.visible_text):
        raise SourceSpanError("finalized source span offsets are outside canonical visible text")
    if document.visible_text[start:end] != quote:
        raise SourceSpanError("finalized source span quote does not match canonical visible text")
    if quote_digest != sha256_bytes(quote.encode("utf-8")):
        raise SourceSpanError("finalized source span quote hash is invalid")
    matching_indices = [
        index for index, heading in enumerate(document.headings) if heading.anchor == anchor
    ]
    if len(matching_indices) != 1:
        raise SourceSpanError("finalized source span anchor is not uniquely structural source content")
    index = matching_indices[0]
    heading = document.headings[index]
    section_end = len(document.visible_text)
    for later in document.headings[index + 1 :]:
        if later.level <= heading.level:
            section_end = later.visible_start_offset
            break
    if start < heading.visible_start_offset or end > section_end:
        raise SourceSpanError("finalized source span is outside its structural heading section")


def verify_runtime_a2a_capture(
    package: dict[str, Any], *, capture_root: Path
) -> dict[str, Any] | None:
    """Verify claimed A2A fresh-capture bytes against the trusted runtime archive."""
    extensions = package.get("extensions")
    if not isinstance(extensions, dict):
        raise SourceSpanError("A2A package has invalid extensions")
    fresh = extensions.get("com.partner_advisory.nee.a2a-fresh-capture")
    if fresh is None:
        return None
    if not isinstance(fresh, dict):
        raise SourceSpanError("A2A package has an invalid fresh-capture binding")
    source = package.get("source")
    packet = package.get("packet")
    source_id = source.get("source_id") if isinstance(source, dict) else None
    vendor = source.get("vendor") if isinstance(source, dict) else None
    required_fresh = (
        "capture_id",
        "capture_manifest_sha256",
        "captured_at",
        "requested_url",
        "final_url",
        "raw_response_sha256",
        "visible_text_sha256",
    )
    if (
        not isinstance(source_id, str)
        or not source_id
        or not isinstance(vendor, str)
        or not vendor
        or not isinstance(packet, dict)
        or any(not isinstance(fresh.get(key), str) or not fresh[key] for key in required_fresh)
    ):
        raise SourceSpanError("A2A package has an invalid fresh-capture source binding")
    expected_manifest_digest = fresh["capture_manifest_sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_digest):
        raise SourceSpanError("A2A package has an invalid fresh-capture manifest digest")

    archive_root = Path(capture_root).expanduser().absolute()
    if not archive_root.is_dir():
        raise SourceSpanError("A2A runtime capture archive is unavailable")
    archive_root = assert_safe_mutable_path(
        archive_root, archive_root, label="A2A runtime capture archive"
    )
    matching_manifests: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in sorted(archive_root.rglob("capture_manifest.json")):
        if not manifest_path.is_file():
            continue
        safe_manifest = assert_safe_mutable_path(
            archive_root, manifest_path, label="A2A runtime capture manifest"
        )
        if sha256_file(safe_manifest) != expected_manifest_digest:
            continue
        try:
            manifest = json.loads(safe_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceSpanError("A2A runtime capture manifest is unreadable") from exc
        matching_manifests.append((safe_manifest, manifest))
    if len(matching_manifests) != 1:
        raise SourceSpanError("A2A runtime capture archive has no unique bound manifest")

    manifest_path, manifest = matching_manifests[0]
    bundle = manifest.get("bundle") if isinstance(manifest, dict) else None
    captures = manifest.get("captures") if isinstance(manifest, dict) else None
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("status") != "PASS"
        or manifest.get("capture_id") != fresh["capture_id"]
        or not isinstance(bundle, dict)
        or bundle.get("sha256") != packet.get("source_zip_sha256")
        or bundle.get("bundle_manifest_sha256") != packet.get("packet_manifest_sha256")
        or not isinstance(captures, list)
    ):
        raise SourceSpanError("A2A runtime capture manifest does not bind this source package")
    matching_captures = [
        item for item in captures if isinstance(item, dict) and item.get("source_id") == source_id
    ]
    if len(matching_captures) != 1:
        raise SourceSpanError("A2A runtime capture manifest has no unique source capture")
    capture = matching_captures[0]
    required_capture = (
        "captured_at",
        "requested_url",
        "final_url",
        "raw_sha256",
        "visible_text_sha256",
        "raw_path",
        "raw_bytes",
        "response_headers",
        "vendor",
    )
    if (
        capture.get("status") != "CAPTURED"
        or any(key not in capture for key in required_capture)
        or capture.get("vendor") != vendor
        or any(
            capture.get(capture_key) != fresh[fresh_key]
            for capture_key, fresh_key in (
                ("captured_at", "captured_at"),
                ("requested_url", "requested_url"),
                ("final_url", "final_url"),
                ("raw_sha256", "raw_response_sha256"),
                ("visible_text_sha256", "visible_text_sha256"),
            )
        )
        or not isinstance(capture.get("raw_path"), str)
        or Path(capture["raw_path"]).is_absolute()
    ):
        raise SourceSpanError("A2A fresh capture does not match the trusted archive")
    raw_path = assert_safe_mutable_path(
        archive_root,
        manifest_path.parent / capture["raw_path"],
        label="A2A captured raw response",
    )
    if not raw_path.is_file():
        raise SourceSpanError("A2A captured raw response is unavailable")
    try:
        document = validate_capture_document(capture, raw_path.read_bytes())
    except (OSError, SourceSpanError) as exc:
        raise SourceSpanError("A2A fresh capture cannot be reconstructed from raw bytes") from exc

    spans = extensions.get("com.partner_advisory.nee.a2a-evidence-spans")
    if spans is not None:
        if not isinstance(spans, dict):
            raise SourceSpanError("A2A selected source spans are invalid")
        for evidence_id, span in spans.items():
            if not isinstance(evidence_id, str) or not evidence_id or not isinstance(span, dict):
                raise SourceSpanError("A2A selected source spans are invalid")
            if (
                span.get("source_id") != source_id
                or span.get("capture_id") != fresh["capture_id"]
                or span.get("raw_response_sha256") != fresh["raw_response_sha256"]
                or span.get("visible_text_sha256") != fresh["visible_text_sha256"]
            ):
                raise SourceSpanError("A2A selected source span is not capture-bound")
            validate_finalized_span(span, document)
    return {
        "capture_id": fresh["capture_id"],
        "capture_manifest_sha256": expected_manifest_digest,
        "source_id": source_id,
        "raw_response_sha256": fresh["raw_response_sha256"],
        "visible_text_sha256": fresh["visible_text_sha256"],
        "selected_span_count": len(spans) if isinstance(spans, dict) else 0,
        "verification_state": "runtime_capture_reconstructed",
    }
