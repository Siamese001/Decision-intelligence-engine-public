#!/usr/bin/env python3
"""Governed PRF speaker diarization qualification and evidence driver.

Binds recording, selected cache, model provenance, identity corrections,
and independent reference sets. Emits immutable per-wave receipts conforming
to contracts/dual_pipeline/speaker_quality_receipt.schema.json.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.qualify_prf_core.evidence import sha256_file, validate_previous_receipt
from tools.qualify_prf_core.metrics import (
    compute_alignment_metrics,
    verify_counterfactual_invariance,
)
from tools.qualify_prf_core.wave1 import SCHEMA_PATH, execute_wave_1_qualification
from tools.qualify_prf_core.wave2 import execute_wave_2_qualification
from tools.qualify_prf_core.wave3 import execute_wave_3_qualification
from tools.qualify_prf_core.wave4 import execute_wave_4_qualification
from tools.qualify_prf_core.wave5 import execute_wave_5_qualification


def main() -> int:
    parser = argparse.ArgumentParser(description="PRF Diarization Qualification and Evidence Driver")
    parser.add_argument("--wave", type=int, required=True, choices=[1, 2, 3, 4, 5], help="Implementation wave")
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "reports/prf_rca_evidence_manifest.json", help="Evidence manifest path")
    parser.add_argument("--sandbox-root", type=Path, default=Path("/private/tmp/prf_diarization_qualification"), help="Isolated sandbox root")
    parser.add_argument("--receipt", type=Path, required=True, help="Destination path for wave receipt")
    parser.add_argument("--previous-receipt", type=Path, default=None, help="Prerequisite previous-wave receipt path")
    parser.add_argument("--reference-manifest", type=Path, default=REPO_ROOT / "data/reference/prf_20261006_independent_reference_set.json", help="Reference manifest path")
    parser.add_argument("--identity-corrections", type=Path, default=REPO_ROOT / "data/reference/prf_20261006_identity_corrections.json", help="Identity corrections record path")

    args = parser.parse_args()

    if args.wave >= 2:
        if not args.previous_receipt:
            print(f"[FAIL] Wave {args.wave} requires --previous-receipt.", file=sys.stderr)
            return 1
        try:
            prev_val = validate_previous_receipt(args.previous_receipt, args.wave - 1)
            print(f"[PASS] Prerequisite receipt validated: {prev_val['previous_wave']} -> {prev_val['previous_terminal_outcome']}")
        except Exception as e:
            print(f"[FAIL] Prerequisite validation error: {e}", file=sys.stderr)
            return 1

    if args.wave == 1:
        return execute_wave_1_qualification(
            manifest_path=args.manifest,
            sandbox_root=args.sandbox_root,
            receipt_path=args.receipt,
            ref_manifest_path=args.reference_manifest,
            id_corrections_path=args.identity_corrections,
        )

    if args.wave == 2:
        return execute_wave_2_qualification(
            manifest_path=args.manifest,
            sandbox_root=args.sandbox_root,
            receipt_path=args.receipt,
            previous_receipt_path=args.previous_receipt,
            ref_manifest_path=args.reference_manifest,
            id_corrections_path=args.identity_corrections,
        )

    if args.wave == 3:
        return execute_wave_3_qualification(
            manifest_path=args.manifest,
            sandbox_root=args.sandbox_root,
            receipt_path=args.receipt,
            previous_receipt_path=args.previous_receipt,
            ref_manifest_path=args.reference_manifest,
            id_corrections_path=args.identity_corrections,
        )

    if args.wave == 4:
        return execute_wave_4_qualification(
            manifest_path=args.manifest,
            sandbox_root=args.sandbox_root,
            receipt_path=args.receipt,
            previous_receipt_path=args.previous_receipt,
            ref_manifest_path=args.reference_manifest,
            id_corrections_path=args.identity_corrections,
        )

    if args.wave == 5:
        return execute_wave_5_qualification(
            manifest_path=args.manifest,
            sandbox_root=args.sandbox_root,
            receipt_path=args.receipt,
            previous_receipt_path=args.previous_receipt,
            ref_manifest_path=args.reference_manifest,
            id_corrections_path=args.identity_corrections,
        )

    print(f"[INFO] Wave {args.wave} driver execution will be completed in Wave {args.wave}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
