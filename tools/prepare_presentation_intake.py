#!/usr/bin/env python3
"""
tools/prepare_presentation_intake.py — Governed PowerPoint (.pptx) Intake Packager.

Converts an authoritative .pptx presentation into a contract-valid
`urn:nee:dual-pipeline:pptx-source-intake:1.0.0` intake package with exact
byte bindings and SHA-256 provenance.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.core import sha256_bytes  # noqa: E402
from dual_pipeline.pptx_source import extract_pptx_presentation  # noqa: E402
from tools.validate_dual_pipeline_contracts import canonical_artifact_digest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("prepare_presentation_intake")


def build_pptx_intake_package(
    pptx_path: Path,
    *,
    title: str = "",
    date_str: str = "2026-09-11",
    classification: str = "confidential",
    audiences: list[str] = None,
    authority_rank: int = 5,
    anchor_identifier: str = "",
) -> Dict[str, Any]:
    """Package a .pptx file into a sealed pptx-source-intake package."""
    raw_bytes = pptx_path.read_bytes()
    raw_b64 = base64.b64encode(raw_bytes).decode("utf-8")
    pptx_hash = sha256_bytes(raw_bytes)

    pres = extract_pptx_presentation(raw_bytes)
    resolved_title = title or pres.title or pptx_path.stem.replace("_", " ")
    anchor_id = anchor_identifier or pptx_path.stem.lower().replace(" ", "_").replace("-", "_")

    # Generate chunks: one chunk per key slide or section (first 10 key slides or all if <= 25)
    chunks = []
    for s in pres.slides:
        s_md = s.to_markdown()
        if not s_md.strip():
            continue
        # Ensure chunk text is an exact substring of extracted_markdown
        if s_md in pres.extracted_markdown:
            chunk_text = s_md
        else:
            # Fallback to heading
            chunk_text = f"## Slide {s.slide_number}: {s.title}" if s.title else f"## Slide {s.slide_number}"
            if chunk_text not in pres.extracted_markdown:
                continue

        chunks.append({
            "chunk_id": f"chunk-{s.slide_number:02d}-slide",
            "title": f"Slide {s.slide_number}: {s.title}" if s.title else f"Slide {s.slide_number}",
            "text": chunk_text,
            "locator": {
                "kind": "section",
                "value": f"Slide {s.slide_number}",
                "quote_digest": sha256_bytes(chunk_text.encode("utf-8")),
            },
            "selection_rationale": f"Retains Slide {s.slide_number} structural intelligence for bounded retrieval.",
            "materiality": "high",
            "extraction_confidence": 1.0,
        })

    if not chunks:
        # Fallback chunk to first line of markdown
        first_line = pres.extracted_markdown.splitlines()[0]
        chunks.append({
            "chunk_id": "chunk-01-title",
            "title": resolved_title,
            "text": first_line,
            "locator": {
                "kind": "section",
                "value": "Slide 1",
                "quote_digest": sha256_bytes(first_line.encode("utf-8")),
            },
            "selection_rationale": "Retains presentation title context.",
            "materiality": "high",
            "extraction_confidence": 1.0,
        })

    from dual_pipeline.core import normalize_iso_date
    iso_date = normalize_iso_date(date_str) or "2026-09-11"

    package: Dict[str, Any] = {
        "contract_id": "urn:nee:dual-pipeline:pptx-source-intake:1.0.0",
        "schema_version": "1.0.0",
        "artifact_id": f"pptx-source-{anchor_id}",
        "source": {
            "source_id": anchor_id,
            "source_version_id": f"source-version-{anchor_id}-v1",
            "title": resolved_title,
            "source_location": str(pptx_path),
            "raw_pptx_base64": raw_b64,
            "pptx_file_sha256": pptx_hash,
            "extracted_markdown": pres.extracted_markdown,
            "extracted_markdown_sha256": pres.extracted_markdown_sha256,
            "slide_count": pres.slide_count,
            "source_date": iso_date,
            "classification": classification,
            "audiences": audiences or ["internal_consulting"],
            "source_posture": "derivative_internal",
            "authority_rank": authority_rank,
            "anchor_entity": {
                "reported_text": resolved_title,
                "stable_identifier": anchor_id,
                "context": f"Executive presentation and strategy deck: {resolved_title}.",
            },
        },
        "chunks": chunks,
        "adapter": {
            "name": "nee-pptx-source-intake",
            "version": "1.0.0",
        },
        "extensions": {
            "com.partner_advisory.nee.pptx-extraction": {
                "profile": "visible-shape-table-markdown-v1",
                "source_byte_binding": "raw_pptx_base64+pptx_file_sha256",
            }
        },
    }

    # Seal canonical digest
    c_digest = canonical_artifact_digest(package)
    package["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": c_digest,
    }

    # Validate against contract schema
    validate_artifact(package)
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description="Package a PowerPoint (.pptx) file into a governed intake package.")
    parser.add_argument("--pptx", type=Path, required=True, help="Path to input .pptx file")
    parser.add_argument("--output", type=Path, required=True, help="Output path for intake JSON or output directory")
    parser.add_argument("--title", type=str, default="", help="Optional presentation title override")
    parser.add_argument("--date", type=str, default="2026-09-11", help="Presentation date (YYYY-MM-DD)")
    parser.add_argument("--classification", type=str, default="confidential", choices=["internal", "confidential", "restricted"])
    parser.add_argument("--export-markdown", action="store_true", help="Also export extracted Markdown alongside JSON")
    args = parser.parse_args()

    if not args.pptx.exists():
        logger.error(f"File not found: {args.pptx}")
        return 1

    package = build_pptx_intake_package(
        args.pptx,
        title=args.title,
        date_str=args.date,
        classification=args.classification,
    )

    out_path = args.output
    if out_path.is_dir() or not out_path.suffix:
        out_path.mkdir(parents=True, exist_ok=True)
        out_file = out_path / f"{args.pptx.stem}.intake.json"
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_file = out_path

    out_file.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(f"Sealed PPTX intake package: {out_file}")
    logger.info(f"Canonical digest: {package['canonical_digest']['value']}")
    logger.info(f"Extracted {package['source']['slide_count']} slides across {len(package['chunks'])} chunks.")

    if args.export_markdown:
        md_file = out_file.with_suffix(".md")
        md_file.write_text(package["source"]["extracted_markdown"], encoding="utf-8")
        logger.info(f"Exported clean Markdown: {md_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
