#!/usr/bin/env python3
"""
tools/run_5_agent_doc_intelligence.py

CLI entry point for running the 5-Agent Document Intelligence Panel.
Decomposes multi-role and complex documents into distinct nodes, eliminating primacy bias.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    MultiAgentDocumentIntelligenceReport,
    build_intelligence_prior_context,
    run_5_agent_document_intelligence,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 5-Agent Document Intelligence Panel.")
    parser.add_argument("--input", "-i", required=True, help="Path to document file.")
    parser.add_argument("--title", "-t", default="", help="Document title.")
    parser.add_argument("--output", "-o", default="", help="Optional JSON output receipt path.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        return 1

    text = input_path.read_text(encoding="utf-8")
    title = args.title or input_path.stem.replace("_", " ").title()

    print("=== 5-AGENT DOCUMENT INTELLIGENCE PANEL ===")
    print(f"Document: {title} ({len(text)} bytes)\n")

    report: MultiAgentDocumentIntelligenceReport = run_5_agent_document_intelligence(
        document_text=text,
        document_title=title,
    )

    print("--- PANEL FINDINGS ---")
    print(f"Total Entities Decomposed: {report.total_entities_detected}")
    print(f"Coverage Score: {report.coverage_score * 100:.1f}%")
    print(f"Primacy Bias Detected: {'YES (FLAGGED)' if report.primacy_bias_detected else 'NO (CLEAN)'}")
    print(f"Arbiter Verdict: {report.arbiter_verdict}")
    print(f"Arbiter Notes: {report.arbiter_notes}\n")

    print("Decomposed Entities:")
    for ent in report.entities:
        print(f"  [{ent.get('type')}] {ent.get('name')} ({ent.get('id')})")

    print("\nDiscovered Relationships:")
    for rel in report.relationships:
        print(f"  {rel.get('from_id')} --[{rel.get('predicate')}]--> {rel.get('to_id')}: {rel.get('relationship_context')}")

    if args.output:
        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "title": report.document_title,
            "total_entities": report.total_entities_detected,
            "coverage_score": report.coverage_score,
            "primacy_bias_detected": report.primacy_bias_detected,
            "arbiter_verdict": report.arbiter_verdict,
            "notes": report.arbiter_notes,
            "entities": report.entities,
            "relationships": report.relationships,
        }
        out_p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nWrote intelligence report to: {out_p}")

    return 0 if report.arbiter_verdict == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
