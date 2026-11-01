"""Document and presentation source adapters emitting source-bound discrete candidates."""
from __future__ import annotations

import base64
import re
from typing import Any

from ..contracts import create_artifact, validate_artifact
from ..core import digest, normalize_space, sha256_bytes, stable_id, trace_id
from ..docx_source import extract_docx_markdown
from ..html_source import extract_html_document
from ..pdf_source import extract_pdf_pages, render_pdf_markdown, render_pdf_range
from ..pptx_source import extract_pptx_markdown
from ..project_status import (
    PROJECT_STATUS_CANDIDATE_PROFILES_KEY,
    PROJECT_STATUS_SNAPSHOT_KEY,
    validate_snapshot_profile,
)
from ..strategic_ingestion import (
    STRATEGIC_CANDIDATE_PROFILES_KEY,
    source_chunk_profiles,
)
from .constants import SEMANTIC_CORRECTION_CANDIDATE_KEY
from .meeting import (
    _context_extension,
    _mention,
)
from .meeting_entity_helpers import _markdown_semantic_correction


def _render_markdown_for_retrieval(value: str) -> str:
    """Keep semantic text while stripping Markdown-only table and fence syntax."""

    headers: list[str] | None = None
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if headers is None:
                headers = cells
                continue
            if len(headers) == len(cells):
                lines.append("; ".join(f"{key}: {cell}" for key, cell in zip(headers, cells, strict=False)))
                continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        lines.append(line)
    return normalize_space(" ".join(lines))


def _normalize_rendered_source(
    package: dict[str, Any],
    *,
    run_context: str,
    rendered_markdown: str,
    source_sha256: str,
    source_kind: str,
    source_label: str,
) -> dict[str, Any]:
    """Emit candidate chunks whose rendered text is bound to the source bytes."""

    source = package["source"]
    raw_entity_profile = package.get("extensions", {}).get(
        "com.partner_advisory.nee.anchor-entity-profile"
    )
    if raw_entity_profile is None:
        entity_profile = {
            "stable_identifier": source["anchor_entity"]["stable_identifier"],
            "entity_intent": "unknown",
            "node_type": None,
            "declaration_status": "MISSING",
            "source_profile_digest": digest(
                {
                    "profile": None,
                    "source_id": source["source_id"],
                    "stable_identifier": source["anchor_entity"]["stable_identifier"],
                }
            ),
        }
    else:
        if not isinstance(raw_entity_profile, dict) or set(raw_entity_profile) != {
            "stable_identifier",
            "entity_intent",
            "node_type",
        }:
            raise ValueError("anchor entity profile has an invalid source-bound shape")
        stable_identifier = raw_entity_profile["stable_identifier"]
        entity_intent = raw_entity_profile["entity_intent"]
        node_type = raw_entity_profile["node_type"]
        if stable_identifier != source["anchor_entity"]["stable_identifier"]:
            raise ValueError("anchor entity profile does not match the source anchor")
        if entity_intent not in {"source_only", "resolve_or_create"}:
            raise ValueError("anchor entity profile has an unsupported entity intent")
        if entity_intent == "source_only" and node_type is not None:
            raise ValueError("source_only anchor entity profile must not declare a node type")
        if entity_intent == "resolve_or_create" and (
            not isinstance(node_type, str) or not node_type.strip()
        ):
            raise ValueError("resolve_or_create anchor entity profile requires a node type")
        entity_profile = {
            "stable_identifier": stable_identifier,
            "entity_intent": entity_intent,
            "node_type": node_type.strip() if isinstance(node_type, str) else None,
            "declaration_status": "DECLARED",
            "source_profile_digest": digest(raw_entity_profile),
        }
    chunk_ids = [chunk["chunk_id"] for chunk in package["chunks"]]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError(f"{source_label} package has duplicate chunk IDs")
    for chunk in package["chunks"]:
        text = chunk["text"]
        locator = chunk["locator"]
        if text not in rendered_markdown:
            raise ValueError(
                f"{source_label} package chunk is not an exact substring of rendered text: {chunk['chunk_id']}"
            )
        if locator.get("quote_digest") != sha256_bytes(text.encode("utf-8")):
            raise ValueError(
                f"{source_label} package locator quote digest does not bind chunk text: {chunk['chunk_id']}"
            )

    package_digest = package["canonical_digest"]["value"]
    intake_id = stable_id("intake", package["artifact_id"], package_digest, length=24)
    run_id = stable_id(
        "run-intake", package["artifact_id"], package_digest, run_context, length=24
    )
    current_trace_id = trace_id(run_id, package_digest)
    # The source-bound anchor context carries the authority posture for this
    # package.  Older protocol packages use the original default verbatim;
    # public product and offering captures can state their narrower boundary.
    authority_boundary = source["anchor_entity"]["context"]
    replaces_run_id, correction_controls = _markdown_semantic_correction(package)
    strategic_chunk_profiles = source_chunk_profiles(
        package,
        chunk_ids=chunk_ids,
    )
    status_snapshot = package.get("extensions", {}).get(PROJECT_STATUS_SNAPSHOT_KEY)
    status_snapshot_profile = (
        validate_snapshot_profile(status_snapshot)
        if status_snapshot is not None
        else None
    )
    status_candidate_profiles: dict[str, dict[str, Any]] = {}
    correction_candidate_governance: dict[str, dict[str, Any]] = {}
    strategic_candidate_profiles: dict[str, dict[str, str]] = {}
    entity_intent_profiles: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for chunk in package["chunks"]:
        chunk_id = chunk["chunk_id"]
        text = chunk["text"]
        evidence_id = stable_id(
            "evidence", source["source_version_id"], chunk_id, chunk["locator"], length=26
        )
        evidence.append(
            {
                "evidence_id": evidence_id,
                "source_id": source["source_id"],
                "source_version_id": source["source_version_id"],
                "locator": chunk["locator"],
                "relationship": "supports",
                "directness": "derivative",
                "authority_rank": source["authority_rank"],
                "content_digest": digest(text),
            }
        )
        candidate_id = stable_id("candidate", run_id, chunk_id, length=26)
        if status_snapshot_profile is not None:
            status_candidate_profiles[candidate_id] = status_snapshot_profile
        correction = correction_controls.get(chunk_id)
        strategic_profile = strategic_chunk_profiles.get(chunk_id)
        # A governed semantic replacement may also add an orthogonal strategic
        # profile.  The correction controls bind which assertion is replaced;
        # the strategic profile controls only how the replacement may be used
        # by strategic retrieval.  Both remain source-bound and force
        # individual HITL review downstream.
        anchor = (
            correction["anchor_entity"]
            if correction is not None
            else source["anchor_entity"]
        )
        candidate_type = (
            correction["candidate_type"]
            if correction is not None
            else "assertion"
            if strategic_profile is not None
            else "source_chunk"
        )
        assertion_type = (
            correction["assertion_type"]
            if correction is not None
            else "candidate_assertion"
        )
        retrieval_text = normalize_space(
            f"{authority_boundary} {source['anchor_entity']['reported_text']} — "
            f"{chunk['title']}. {_render_markdown_for_retrieval(text)}"
        )
        if len(retrieval_text) > 4000:
            raise ValueError(f"{source_label} candidate exceeds 4000 characters: {chunk_id}")
        mention = _mention(
            candidate_id,
            1,
            anchor["reported_text"],
            stable_identifier=anchor["stable_identifier"],
            context=anchor["context"],
        )
        if anchor["stable_identifier"] == source["anchor_entity"]["stable_identifier"]:
            entity_intent_profiles[mention["mention_id"]] = dict(entity_profile)
        else:
            entity_intent_profiles[mention["mention_id"]] = {
                "stable_identifier": anchor["stable_identifier"],
                "entity_intent": "unknown",
                "node_type": None,
                "declaration_status": "MISSING",
                "source_profile_digest": digest(
                    {
                        "profile": None,
                        "source_id": source["source_id"],
                        "stable_identifier": anchor["stable_identifier"],
                    }
                ),
            }
        candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_type": candidate_type,
                "assertion_type": assertion_type,
                "normalized_text": retrieval_text,
                "materiality": chunk["materiality"],
                "entity_mentions": [mention],
                "evidence_ids": [evidence_id],
                "source_locator": chunk["locator"],
                "classification": source["classification"],
                "audiences": source["audiences"],
                "extraction_confidence": chunk["extraction_confidence"],
            }
        )
        if correction is not None:
            correction_candidate_governance[candidate_id] = {
                "chunk_id": chunk_id,
                "force_hitl": True,
                "replaces_run_id": replaces_run_id,
                "retire_only": correction["retire_only"],
                "supersedes_assertion_id": correction["supersedes_assertion_id"],
            }
        if strategic_profile is not None:
            strategic_candidate_profiles[candidate_id] = strategic_profile
    return create_artifact(
        "normalized-candidate-bundle",
        stable_id("candidate-bundle", run_id, length=26),
        intake_id=intake_id,
        run_id=run_id,
        trace_id=current_trace_id,
        source_id=source["source_id"],
        source_version_id=source["source_version_id"],
        evidence=evidence,
        candidates=candidates,
        extensions=_context_extension(
            source_kind=source_kind,
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
                "source_file_sha256": source_sha256,
            },
            "com.partner_advisory.nee.entity-intent-profiles": entity_intent_profiles,
        }
        | (
            {
                SEMANTIC_CORRECTION_CANDIDATE_KEY: correction_candidate_governance,
            }
            if correction_candidate_governance
            else {}
        )
        | (
            {STRATEGIC_CANDIDATE_PROFILES_KEY: strategic_candidate_profiles}
            if strategic_candidate_profiles
            else {}
        )
        | (
            {PROJECT_STATUS_CANDIDATE_PROFILES_KEY: status_candidate_profiles}
            if status_candidate_profiles
            else {}
        ),
    )


def normalize_markdown_source(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Validate a source-bound Markdown package and emit selected candidates."""

    validate_artifact(package)
    if not package["contract_id"].endswith(":markdown-source-intake:1.0.0"):
        raise ValueError("expected a markdown-source intake contract")
    if package.get("adapter") != {
        "name": "nee-markdown-source-intake",
        "version": "1.0.0",
    }:
        raise ValueError("Markdown package has an unsupported adapter binding")
    source = package["source"]
    raw_markdown = source["raw_markdown"]
    if sha256_bytes(raw_markdown.encode("utf-8")) != source["source_file_sha256"]:
        raise ValueError("Markdown package source_file_sha256 does not bind raw_markdown")
    return _normalize_rendered_source(
        package,
        run_context=run_context,
        rendered_markdown=raw_markdown,
        source_sha256=source["source_file_sha256"],
        source_kind="markdown_source",
        source_label="Markdown",
    )


def normalize_docx_source(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Verify original DOCX bytes then emit deterministic source chunks."""

    validate_artifact(package)
    if not package["contract_id"].endswith(":docx-source-intake:1.0.0"):
        raise ValueError("expected a docx-source intake contract")
    if package.get("adapter") != {
        "name": "nee-docx-source-intake",
        "version": "1.0.0",
    }:
        raise ValueError("DOCX package has an unsupported adapter binding")
    source = package["source"]
    try:
        raw_docx = base64.b64decode(source["raw_docx_base64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("DOCX package raw_docx_base64 is invalid") from exc
    if sha256_bytes(raw_docx) != source["docx_file_sha256"]:
        raise ValueError("DOCX package docx_file_sha256 does not bind raw_docx_base64")
    rendered_markdown = extract_docx_markdown(raw_docx)
    if rendered_markdown != source["extracted_markdown"]:
        raise ValueError("DOCX package extracted_markdown is not reproducible from raw DOCX")
    if (
        sha256_bytes(rendered_markdown.encode("utf-8"))
        != source["extracted_markdown_sha256"]
    ):
        raise ValueError("DOCX package extracted_markdown_sha256 does not bind rendered text")
    return _normalize_rendered_source(
        package,
        run_context=run_context,
        rendered_markdown=rendered_markdown,
        source_sha256=source["docx_file_sha256"],
        source_kind="docx_source",
        source_label="DOCX",
    )


def normalize_pptx_source(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Verify original PPTX bytes then emit deterministic source chunks."""

    validate_artifact(package)
    if not package["contract_id"].endswith(":pptx-source-intake:1.0.0"):
        raise ValueError("expected a pptx-source intake contract")
    if package.get("adapter") != {
        "name": "nee-pptx-source-intake",
        "version": "1.0.0",
    }:
        raise ValueError("PPTX package has an unsupported adapter binding")
    source = package["source"]
    try:
        raw_pptx = base64.b64decode(source["raw_pptx_base64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("PPTX package raw_pptx_base64 is invalid") from exc
    if sha256_bytes(raw_pptx) != source["pptx_file_sha256"]:
        raise ValueError("PPTX package pptx_file_sha256 does not bind raw_pptx_base64")
    rendered_markdown = extract_pptx_markdown(raw_pptx)
    if rendered_markdown != source["extracted_markdown"]:
        raise ValueError("PPTX package extracted_markdown is not reproducible from raw PPTX")
    if (
        sha256_bytes(rendered_markdown.encode("utf-8"))
        != source["extracted_markdown_sha256"]
    ):
        raise ValueError("PPTX package extracted_markdown_sha256 does not bind rendered text")
    return _normalize_rendered_source(
        package,
        run_context=run_context,
        rendered_markdown=rendered_markdown,
        source_sha256=source["pptx_file_sha256"],
        source_kind="pptx_source",
        source_label="PPTX",
    )


def normalize_html_source(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Verify original HTML bytes and deterministic visible-text rendering."""

    validate_artifact(package)
    if not package["contract_id"].endswith(":html-source-intake:1.0.0"):
        raise ValueError("expected a html-source intake contract")
    if package.get("adapter") != {
        "name": "nee-html-source-intake",
        "version": "1.0.0",
    }:
        raise ValueError("HTML package has an unsupported adapter binding")
    source = package["source"]
    try:
        raw_html = base64.b64decode(source["raw_html_base64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("HTML package raw_html_base64 is invalid") from exc
    if sha256_bytes(raw_html) != source["html_file_sha256"]:
        raise ValueError("HTML package html_file_sha256 does not bind raw_html_base64")
    title, rendered_markdown = extract_html_document(raw_html)
    if source["title"] != title:
        raise ValueError("HTML package title is not reproducible from raw HTML")
    if source["extracted_markdown"] != rendered_markdown:
        raise ValueError("HTML package extracted_markdown is not reproducible from raw HTML")
    if (
        sha256_bytes(rendered_markdown.encode("utf-8"))
        != source["extracted_markdown_sha256"]
    ):
        raise ValueError("HTML package extracted_markdown_sha256 does not bind rendered text")
    return _normalize_rendered_source(
        package,
        run_context=run_context,
        rendered_markdown=rendered_markdown,
        source_sha256=source["html_file_sha256"],
        source_kind="html_source",
        source_label="HTML",
    )


def normalize_pdf_source(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Verify original PDF bytes and selected page-range text."""

    validate_artifact(package)
    if not package["contract_id"].endswith(":pdf-source-intake:1.0.0"):
        raise ValueError("expected a pdf-source intake contract")
    if package.get("adapter") != {
        "name": "nee-pdf-source-intake",
        "version": "1.0.0",
    }:
        raise ValueError("PDF package has an unsupported adapter binding")
    source = package["source"]
    try:
        raw_pdf = base64.b64decode(source["raw_pdf_base64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("PDF package raw_pdf_base64 is invalid") from exc
    if sha256_bytes(raw_pdf) != source["pdf_file_sha256"]:
        raise ValueError("PDF package pdf_file_sha256 does not bind raw_pdf_base64")
    pages = extract_pdf_pages(raw_pdf)
    if len(pages) != source["page_count"]:
        raise ValueError("PDF package page_count is not reproducible from raw PDF")
    for chunk in package["chunks"]:
        locator = chunk["locator"]
        if locator["kind"] != "page":
            raise ValueError("PDF package chunks require page locators")
        reproduced = render_pdf_range(pages, locator["value"])
        if chunk["text"] != reproduced:
            raise ValueError(
                "PDF package chunk text is not reproducible from raw PDF: "
                f"{chunk['chunk_id']}"
            )
    rendered_markdown = render_pdf_markdown(pages, title=source["title"])
    return _normalize_rendered_source(
        package,
        run_context=run_context,
        rendered_markdown=rendered_markdown,
        source_sha256=source["pdf_file_sha256"],
        source_kind="pdf_source",
        source_label="PDF",
    )
