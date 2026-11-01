#!/usr/bin/env python3
"""Create immutable, proposal-only source spans from a frozen A2A capture.

The output ledger has this stable shape::

    {
      "schema_version": "1.0.0",
      "status": "PASS",
      "capture_manifest": {"capture_id": "...", "sha256": "..."},
      "source_bundle": {"sha256": "...", "bundle_manifest_sha256": "..."},
      "extractor": {"name": "...", "version": "..."},
      "proposals": [
        {
          "span_proposal_id": "...",
          "status": "PROPOSED_FOR_HITL",
          "fact_id": "...", "evidence_id": "...", "source_id": "...",
          "capture_binding": {"raw_relative_path": "...", ...},
          "source_version": {"raw_response_sha256": "...", ...},
          "heading_path": [...], "anchor": "...",
          "visible_start_offset": 0, "visible_end_offset": 1,
          "quote": "...", "quote_sha256": "...",
          "context_before_sha256": "...", "context_after_sha256": "...",
          "selection": {"method": "deterministic_lexical_v1", ...},
          "validation": {...}
        }
      ]
    }

It never decides semantic entailment, creates a package, writes a graph, or
promotes an embedding.  It fails before publishing a ledger if any frozen raw
capture, canonical visible text, heading, quotation, or offset is invalid.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import seal_artifact, sha256_file, write_json_exclusive  # noqa: E402
from dual_pipeline.source_spans import (  # noqa: E402
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    SELECTION_METHOD,
    SourceSpanError,
    build_span_proposals,
    validate_capture_document,
    validate_span_proposal,
)
from tools.prepare_a2a_fact_vector_packet_intake import read_and_verify_bundle  # noqa: E402

LEDGER_SCHEMA_VERSION = "1.0.0"
LEDGER_KIND = "first_party_evidence_span_proposals"


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceSpanError(f"{description} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SourceSpanError(f"{description} must be a JSON object")
    return value


def _inside(parent: Path, child: Path) -> None:
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise SourceSpanError("capture raw path escapes its capture manifest directory") from exc


def load_capture_manifest(
    path: Path, verified_bundle: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load a complete capture manifest and bind every raw response to its path."""

    path = path.resolve()
    if not path.is_file():
        raise SourceSpanError("--capture-manifest must identify an existing file")
    manifest = _read_json(path, "capture manifest")
    bundle = manifest.get("bundle")
    capture_id = manifest.get("capture_id")
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("status") != "PASS"
        or not isinstance(bundle, dict)
        or bundle.get("sha256") != verified_bundle["bundle_sha256"]
        or bundle.get("bundle_manifest_sha256") != verified_bundle["bundle_manifest_sha256"]
        or not isinstance(capture_id, str)
        or not capture_id
    ):
        raise SourceSpanError("capture manifest is not bound to the verified A2A bundle")
    expected_sources = {row.get("source_id") for row in verified_bundle["data"]["sources"]}
    expected_evidence = {
        row.get("evidence_id") for row in verified_bundle["data"]["evidence_records"]
    }
    if None in expected_sources or None in expected_evidence:
        raise SourceSpanError("verified A2A bundle has invalid source/evidence identifiers")
    captures = manifest.get("captures")
    if not isinstance(captures, list):
        raise SourceSpanError("capture manifest has no capture list")
    capture_by_source: dict[str, dict[str, Any]] = {}
    found_evidence: set[str] = set()
    root = path.parent.resolve()
    for capture in captures:
        source_id = capture.get("source_id") if isinstance(capture, dict) else None
        raw_relative_path = capture.get("raw_path") if isinstance(capture, dict) else None
        if (
            not isinstance(capture, dict)
            or not isinstance(source_id, str)
            or source_id not in expected_sources
            or source_id in capture_by_source
            or capture.get("status") != "CAPTURED"
            or not isinstance(raw_relative_path, str)
            or not raw_relative_path
            or Path(raw_relative_path).is_absolute()
        ):
            raise SourceSpanError("capture manifest has an invalid source capture")
        raw_path = (root / raw_relative_path).resolve()
        _inside(root, raw_path)
        if not raw_path.is_file():
            raise SourceSpanError("capture raw response is missing")
        capture["_raw_path"] = raw_path
        evidence_rows = capture.get("evidence")
        if not isinstance(evidence_rows, list):
            raise SourceSpanError("capture manifest source has no evidence result list")
        for row in evidence_rows:
            evidence_id = row.get("evidence_id") if isinstance(row, dict) else None
            if not isinstance(evidence_id, str) or evidence_id not in expected_evidence:
                raise SourceSpanError("capture manifest contains an unknown evidence identifier")
            if evidence_id in found_evidence:
                raise SourceSpanError("capture manifest repeats an evidence identifier")
            found_evidence.add(evidence_id)
        capture_by_source[source_id] = capture
    if set(capture_by_source) != expected_sources:
        raise SourceSpanError("capture manifest does not cover every A2A source")
    if found_evidence != expected_evidence:
        raise SourceSpanError("capture manifest does not cover every A2A evidence record")
    return manifest, capture_by_source


def _fact_evidence_pairs(
    verified_bundle: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    evidence_by_id = {
        row.get("evidence_id"): row for row in verified_bundle["data"]["evidence_records"]
    }
    if None in evidence_by_id or len(evidence_by_id) != len(
        verified_bundle["data"]["evidence_records"]
    ):
        raise SourceSpanError("verified A2A bundle has duplicate or invalid evidence identifiers")
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    fact_ids: set[str] = set()
    for fact in verified_bundle["data"]["fact_candidates"]:
        fact_id = fact.get("fact_id")
        source_ids = fact.get("source_ids_json")
        evidence_ids = fact.get("evidence_ids_json")
        if (
            not isinstance(fact_id, str)
            or fact_id in fact_ids
            or not isinstance(source_ids, list)
            or len(source_ids) != 1
            or not isinstance(source_ids[0], str)
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or fact.get("eligible_for_fact_review") is not True
            or fact.get("individual_review_required") is not True
        ):
            raise SourceSpanError("verified A2A bundle has an invalid fact-review candidate")
        fact_ids.add(fact_id)
        for evidence_id in evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if not isinstance(evidence, dict) or evidence.get("source_id") != source_ids[0]:
                raise SourceSpanError(
                    "fact evidence does not bind to its declared first-party source"
                )
            pairs.append((fact, evidence))
    if not pairs:
        raise SourceSpanError("verified A2A bundle has no fact/evidence pairs")
    return pairs


def _rank_fact_proposals(
    proposals: list[dict[str, Any]], max_proposals: int
) -> list[dict[str, Any]]:
    """Apply one deterministic cap across all evidence records for a single fact."""

    ordered = sorted(
        proposals,
        key=lambda item: (
            -item["selection"]["score"],
            item["visible_start_offset"],
            item["visible_end_offset"],
            item["evidence_id"],
        ),
    )[:max_proposals]
    for rank, proposal in enumerate(ordered, start=1):
        proposal["selection"]["rank"] = rank
        proposal["selection"]["max_proposals_per_fact"] = max_proposals
    return ordered


def build_ledger(
    *, bundle_path: Path, capture_manifest_path: Path, max_proposals: int
) -> dict[str, Any]:
    """Build a validated ledger in memory; callers publish it with exclusive creation."""

    if not isinstance(max_proposals, int) or max_proposals < 1 or max_proposals > 10:
        raise SourceSpanError("--max-proposals-per-fact must be an integer from 1 through 10")
    verified = read_and_verify_bundle(bundle_path)
    manifest, capture_by_source = load_capture_manifest(capture_manifest_path, verified)
    manifest_digest = sha256_file(capture_manifest_path.resolve())
    documents: dict[str, Any] = {}
    for source_id, capture in sorted(capture_by_source.items()):
        raw = capture["_raw_path"].read_bytes()
        documents[source_id] = validate_capture_document(capture, raw)
    by_fact: dict[str, list[dict[str, Any]]] = {}
    for fact, evidence in _fact_evidence_pairs(verified):
        source_id = evidence["source_id"]
        capture = capture_by_source[source_id]
        raw_relative_path = capture["raw_path"]
        proposals = build_span_proposals(
            document=documents[source_id],
            fact=fact,
            evidence=evidence,
            capture=capture,
            capture_id=manifest["capture_id"],
            capture_manifest_sha256=manifest_digest,
            raw_relative_path=raw_relative_path,
            max_proposals=10,
        )
        by_fact.setdefault(fact["fact_id"], []).extend(proposals)
    selected: list[dict[str, Any]] = []
    for fact_id in sorted(by_fact):
        ranked = _rank_fact_proposals(by_fact[fact_id], max_proposals)
        if not ranked:
            raise SourceSpanError(f"fact {fact_id} has no source-span proposal")
        for proposal in ranked:
            source_id = proposal["source_id"]
            evidence = next(
                row
                for row in verified["data"]["evidence_records"]
                if row["evidence_id"] == proposal["evidence_id"]
            )
            validate_span_proposal(proposal, documents[source_id], evidence)
        selected.extend(ranked)
    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "ledger_kind": LEDGER_KIND,
        "status": "PASS",
        "source_bundle": {
            "path": str(verified["bundle_path"]),
            "sha256": verified["bundle_sha256"],
            "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
            "upstream_packet_id": verified["upstream"]["packet_id"],
            "upstream_packet_manifest_sha256": verified["upstream"]["packet_manifest_sha256"],
        },
        "capture_manifest": {
            "capture_id": manifest["capture_id"],
            "sha256": manifest_digest,
            "schema_version": manifest["schema_version"],
            "source_count": len(capture_by_source),
        },
        "extractor": {
            "name": EXTRACTOR_NAME,
            "version": EXTRACTOR_VERSION,
            "selection_method": SELECTION_METHOD,
            "semantic_entailment_decision": "not_performed",
        },
        "proposals": selected,
        "summary": {
            "fact_count": len(by_fact),
            "proposal_count": len(selected),
            "source_count": len(capture_by_source),
            "individual_hitl_required": True,
            "automatic_fact_promotion": False,
        },
        "promotion_effect": "none; ledger is immutable proposal evidence only",
    }
    return seal_artifact(ledger)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-proposals-per-fact", type=int, default=5)
    args = parser.parse_args()
    try:
        ledger = build_ledger(
            bundle_path=args.bundle,
            capture_manifest_path=args.capture_manifest,
            max_proposals=args.max_proposals_per_fact,
        )
        write_json_exclusive(args.output, ledger)
    except (OSError, SourceSpanError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(ledger["summary"] | {"status": ledger["status"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
