#!/usr/bin/env python3
"""tools/verify_graphdb_grounding.py

Governed Command-Line Verifier for GraphDB Sourcing and Anti-Hallucination Delivery.
Enforces:
1. Exact payload SHA-256 binding against ClaimManifest.
2. Strict deterministic word count limits (< max_words, non-empty).
3. Persona title grounding (blocks invented executive/consulting titles in signatures).
4. Full claim assertion eligibility and contradiction detection in ReadOnlyGraphSnapshot.
5. Snapshot generation identity re-verification to eliminate mid-flight mutation races.
6. Emission of sealed, schema-valid DeliveryReceipt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.grounding.contracts import (
    ClaimManifest,
    DeliveryReceipt,
)
from dual_pipeline.grounding.delivery_boundary import verify_grounded_delivery


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify delivery payload against ClaimManifest and GraphDB snapshot."
    )
    parser.add_argument("--payload", type=Path, required=True, help="Path to text payload file")
    parser.add_argument("--manifest", type=Path, required=True, help="Path to ClaimManifest JSON file")
    parser.add_argument("--runtime-root", type=Path, required=True, help="Path to GraphDB runtime root")
    parser.add_argument("--max-words", type=int, default=500, help="Strict word count limit (count < max)")
    parser.add_argument("--audience", type=str, default="CLIENT_SAFE", help="Target audience")
    parser.add_argument("--delivery-surface", type=str, default="EMAIL_BODY", help="Delivery surface name")
    parser.add_argument("--prompt-text", type=str, default=None, help="User prompt text for prompt-echo and dynamic ceiling evaluation")
    parser.add_argument("--enforce-claim-coverage", action="store_true", help="Enforce that all payload sentences are covered by verified claims")
    parser.add_argument("--output-receipt", type=Path, default=None, help="Path to write sealed DeliveryReceipt")
    args = parser.parse_args(argv)

    if not args.payload.is_file():
        print(f"ERROR: Payload file not found: {args.payload}", file=sys.stderr)
        return 1
    if not args.manifest.is_file():
        print(f"ERROR: Manifest file not found: {args.manifest}", file=sys.stderr)
        return 1

    payload_text = args.payload.read_text(encoding="utf-8")
    manifest_data = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest = ClaimManifest.from_dict(manifest_data)

    passed, receipt, reasons = verify_grounded_delivery(
        payload_text=payload_text,
        manifest=manifest,
        runtime_root=args.runtime_root,
        max_words=args.max_words,
        audience=args.audience,
        delivery_surface=args.delivery_surface,
        prompt_text=args.prompt_text,
        enforce_claim_coverage=args.enforce_claim_coverage,
    )

    if args.output_receipt:
        args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
        args.output_receipt.write_text(json.dumps(receipt.to_dict(), indent=2) + "\n", encoding="utf-8")

    print("=======================================================")
    print(f" GROUNDED DELIVERY VERIFICATION: {receipt.status}")
    print(f" Reason Code:     {receipt.reason_code}")
    print(f" Word Count:      {receipt.word_count} (< {args.max_words})")
    print(f" Claims:          {receipt.supported_claims_count}/{receipt.total_claims_count} supported")
    print(f" Contradictions:  {receipt.contradicted_claims_count}")
    if reasons:
        print(" Violations:")
        for r in reasons:
            print(f"  - {r}")
    print("=======================================================")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
