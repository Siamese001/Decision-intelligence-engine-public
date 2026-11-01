#!/usr/bin/env python3
"""Convert an exact Markdown source into a source-bound high-signal intake package."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    seal_artifact,
    sha256_bytes,
    slug,
    stable_id,
    write_json_exclusive,
)
from tools.validate_dual_pipeline_contracts import ContractError  # noqa: E402

MARKDOWN_SOURCE_CONTRACT_ID = "urn:nee:dual-pipeline:markdown-source-intake:1.0.0"
AUTHORITY_BOUNDARY = (
    "External best-practice design reference; not approved NEE policy or "
    "deployed-capability evidence."
)
_POSTURE_CHOICES = [
    "authoritative_internal",
    "governed_internal",
    "derivative_internal",
    "working_notes",
    "external_first_party",
    "external_partner",
    "external_independent",
    "external_contextual",
]


def _section(source: str, heading: str) -> str:
    pattern = re.compile(rf"(?ms)^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)")
    match = pattern.search(source)
    if match is None:
        raise ValueError(f"required high-signal Markdown section is missing: {heading!r}")
    return match.group(0).strip()


def _metadata(source: str, field: str) -> str | None:
    pattern = rf"(?m)^\*\*{re.escape(field)}:\*\*[ \t]*(?:`(?P<quoted>[^`]+)`|(?P<plain>[^\r\n]+?))[ \t]*\r?$"
    match = re.search(pattern, source)
    return (match.group("quoted") or match.group("plain")).strip() if match else None


def _title(source: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", source)
    if match is None:
        raise ValueError("Markdown source must begin with a level-one title")
    return match.group(1).strip()


def _locator(value: str, quote: str) -> dict[str, str]:
    return {
        "kind": "section",
        "value": value,
        "quote_digest": sha256_bytes(quote.encode("utf-8")),
    }


def _chunk(
    chunk_id: str,
    title: str,
    text: str,
    locator_value: str,
    selection_rationale: str,
    *,
    materiality: str = "high",
) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError(f"high-signal chunk {chunk_id!r} is empty")
    if len(text) > 4000:
        raise ValueError(f"high-signal chunk {chunk_id!r} exceeds 4000 characters")
    return {
        "chunk_id": chunk_id,
        "title": title,
        "text": text,
        "locator": _locator(locator_value, text),
        "selection_rationale": selection_rationale,
        "materiality": materiality,
        "extraction_confidence": 1.0,
    }


def _high_signal_chunks(source: str) -> list[dict[str, Any]]:
    architecture = _section(source, "Architecture selection: prefer the simplest useful control model")
    core = _section(source, "Core protocol: a delegation is a contract, not a vague request")
    example_marker = "Example delegation:"
    if example_marker not in core:
        raise ValueError("delegation-contract section has no retained example")
    contract_fields, example = core.split(example_marker, 1)
    specs = [
        ("chunk-a2a-architecture-selection", "Architecture selection", architecture, "Architecture selection: prefer the simplest useful control model", "Retains the decision rule for choosing single-agent, workflow, manager-worker, peer handoff, or no parallelism.", "high"),
        ("chunk-a2a-delegation-contract-fields", "Delegation contract fields", contract_fields, "Core protocol: a delegation is a contract, not a vague request#contract-fields", "Preserves the exact required delegation fields that users are likely to query by name.", "high"),
        ("chunk-a2a-delegation-contract-example", "Delegation contract example", example, "Core protocol: a delegation is a contract, not a vague request#example", "Keeps the executable contract example that grounds the abstract field list in a concrete task.", "high"),
        ("chunk-a2a-structured-messages", "Structured message protocol", _section(source, "Message types and minimum payloads"), "Message types and minimum payloads", "Retains the message vocabulary, minimum payloads, and anti-polling coordination rule.", "high"),
        ("chunk-a2a-evidence-provenance", "Evidence and provenance protocol", _section(source, "Evidence protocol: preserve provenance and uncertainty"), "Evidence protocol: preserve provenance and uncertainty", "Retains the fact-inference-assumption-unknown taxonomy and artifact-backed evidence handoff rules.", "high"),
        ("chunk-a2a-state-write-ownership", "State and write ownership", _section(source, "State and write ownership"), "State and write ownership", "Retains the single-writer rule, change-proposal workflow, and durable-state requirement.", "high"),
        ("chunk-a2a-safety-approval", "Safety and approval boundaries", _section(source, "Safety and approval boundaries"), "Safety and approval boundaries", "Retains least-privilege tools, explicit approval boundaries, and untrusted-content handling.", "high"),
        ("chunk-a2a-execution-stopping", "Execution control and stopping rules", _section(source, "Execution control and stopping rules"), "Execution control and stopping rules", "Retains root ownership, bounded concurrency, retry limits, and structured stopping conditions.", "high"),
        ("chunk-a2a-evaluation-observability", "Quality, evaluation, and observability", _section(source, "Quality, evaluation, and observability"), "Quality, evaluation, and observability", "Retains outcome-oriented evaluation dimensions, trial records, adversarial coverage, and human review.", "high"),
        ("chunk-a2a-retrieval-unit-design", "Retrieval-unit design", _section(source, "Retrieval and embedding guidance"), "Retrieval and embedding guidance", "Retains the source's own high-signal chunking guidance, field-name anchors, and exclusions for this retrieval corpus.", "medium"),
    ]
    return [_chunk(cid, title, txt, loc, rat, materiality=mat) for cid, title, txt, loc, rat, mat in specs]


def _section_chunks(source: str, sections: list[str]) -> list[dict[str, Any]]:
    """Select exact Markdown sections for a general source-bound capture."""
    normalized = [section.strip() for section in sections]
    if not normalized or any(not section for section in normalized):
        raise ValueError("--chunk-section values must not be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("--chunk-section values must be unique")
    rationale = "Retains the source-attributed section for bounded, candidate-only retrieval."
    return [
        _chunk(f"chunk-{index:02d}-{slug(heading)}", heading, _section(source, heading), heading, rationale)
        for index, heading in enumerate(normalized, start=1)
    ]


def _default_source_location(source_path: Path) -> str:
    try:
        return source_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return source_path.resolve().as_posix()


def _decode_utf8_markdown(source_path: Path) -> tuple[bytes, str]:
    raw_bytes = source_path.read_bytes()
    if not raw_bytes:
        raise ValueError("--source must identify a non-empty Markdown file")
    try:
        raw_markdown = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("--source must be canonical UTF-8 Markdown") from exc
    if raw_markdown.encode("utf-8") != raw_bytes:
        raise ValueError("--source must be canonical UTF-8 without a byte-order mark")
    return raw_bytes, raw_markdown


def _resolve_required_source_meta(
    raw_markdown: str,
    source_id: str | None,
    source_date: str | None,
    audiences: list[str],
    anchor_entity: str,
    anchor_stable_identifier: str,
    anchor_context: str,
) -> tuple[str, str, list[str]]:
    resolved_id = (source_id or _metadata(raw_markdown, "Source ID") or "").strip()
    if not resolved_id:
        raise ValueError("provide --source-id or include a backticked Source ID in the Markdown")
    resolved_date = (source_date or _metadata(raw_markdown, "Source date") or "").strip()
    if not resolved_date:
        raise ValueError("provide --source-date or include a backticked Source date in the Markdown")
    normalized_audiences = list(dict.fromkeys(value.strip() for value in audiences))
    if not normalized_audiences or any(not value for value in normalized_audiences):
        raise ValueError("--audience values must not be blank")
    if not (anchor_entity.strip() and anchor_stable_identifier.strip() and anchor_context.strip()):
        raise ValueError("the anchor entity, stable identifier, and authority context must not be blank")
    return resolved_id, resolved_date, normalized_audiences


def _build_extensions(
    chunk_sections: list[str] | None,
    anchor_context: str,
    additional_extensions: dict[str, Any] | None,
) -> dict[str, Any]:
    has_sections = chunk_sections is not None
    extensions: dict[str, Any] = {
        "com.partner_advisory.nee.chunk-selection": {
            "profile": "section-selection-v1" if has_sections else "a2a-protocol-high-signal-v1",
            "excluded_sections": [] if has_sections else ["Purpose", "Primary sources"],
            "authority_boundary": anchor_context.strip(),
        }
    }
    if additional_extensions is not None:
        if not isinstance(additional_extensions, dict) or not additional_extensions:
            raise ValueError("additional Markdown extensions must be a nonempty object")
        overlap = set(extensions) & set(additional_extensions)
        if overlap:
            raise ValueError(f"additional Markdown extensions cannot replace built-in controls: {sorted(overlap)}")
        extensions.update(additional_extensions)
    return extensions


def prepare_package(
    source_path: Path,
    output_path: Path,
    *,
    source_id: str | None = None,
    source_date: str | None = None,
    source_location: str | None = None,
    classification: str,
    audiences: list[str],
    source_posture: str,
    authority_rank: int,
    anchor_entity: str,
    anchor_stable_identifier: str,
    anchor_context: str = AUTHORITY_BOUNDARY,
    chunk_sections: list[str] | None = None,
    additional_extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind canonical UTF-8 Markdown bytes and write one validated package."""
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    raw_bytes, raw_markdown = _decode_utf8_markdown(source_path)
    resolved_id, resolved_date, normalized_audiences = _resolve_required_source_meta(
        raw_markdown, source_id, source_date, audiences, anchor_entity, anchor_stable_identifier, anchor_context
    )
    source_hash = sha256_bytes(raw_bytes)
    extensions = _build_extensions(chunk_sections, anchor_context, additional_extensions)
    chunks = _section_chunks(raw_markdown, chunk_sections) if chunk_sections is not None else _high_signal_chunks(raw_markdown)
    package = {
        "contract_id": MARKDOWN_SOURCE_CONTRACT_ID,
        "schema_version": "1.0.0",
        "artifact_id": f"markdown-source-{slug(resolved_id)}",
        "source": {
            "source_id": resolved_id,
            "source_version_id": stable_id("source-version", resolved_id, source_hash, length=28),
            "title": _title(raw_markdown),
            "source_location": (source_location or _default_source_location(source_path)).strip(),
            "raw_markdown": raw_markdown,
            "source_file_sha256": source_hash,
            "source_date": resolved_date,
            "classification": classification,
            "audiences": normalized_audiences,
            "source_posture": source_posture,
            "authority_rank": authority_rank,
            "anchor_entity": {
                "reported_text": anchor_entity.strip(),
                "stable_identifier": anchor_stable_identifier.strip(),
                "context": anchor_context.strip(),
            },
        },
        "chunks": chunks,
        "adapter": {"name": "nee-markdown-source-intake", "version": "1.0.0"},
        "extensions": extensions,
    }
    sealed = seal_artifact(package)
    validate_artifact(sealed)
    write_json_exclusive(output_path, sealed)
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-id")
    parser.add_argument("--source-date")
    parser.add_argument("--source-location")
    parser.add_argument("--classification", choices=["public", "internal", "confidential", "restricted"], required=True)
    parser.add_argument("--audience", action="append", dest="audiences", required=True)
    parser.add_argument("--source-posture", choices=_POSTURE_CHOICES, default="derivative_internal")
    parser.add_argument("--authority-rank", type=int, default=5)
    parser.add_argument("--anchor-entity", required=True)
    parser.add_argument("--anchor-stable-identifier", required=True)
    parser.add_argument("--anchor-context", default=AUTHORITY_BOUNDARY, help="Authority boundary carried into candidate retrieval text")
    parser.add_argument("--chunk-section", action="append", dest="chunk_sections", help="Exact level-two Markdown section heading to retain.")
    args = parser.parse_args()
    try:
        package = prepare_package(
            args.source,
            args.output,
            source_id=args.source_id,
            source_date=args.source_date,
            source_location=args.source_location,
            classification=args.classification,
            audiences=args.audiences,
            source_posture=args.source_posture,
            authority_rank=args.authority_rank,
            anchor_entity=args.anchor_entity,
            anchor_stable_identifier=args.anchor_stable_identifier,
            anchor_context=args.anchor_context,
            chunk_sections=args.chunk_sections,
        )
    except (OSError, ValueError, json.JSONDecodeError, ContractError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": "PASS",
                "output": str(args.output.resolve()),
                "artifact_id": package["artifact_id"],
                "source_file_sha256": package["source"]["source_file_sha256"],
                "chunk_count": len(package["chunks"]),
                "canonical_digest": package["canonical_digest"]["value"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
