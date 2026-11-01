#!/usr/bin/env python3
"""Wave Receipt Driver and Verifier for Speaker-Turn & Complete-Thought Chunking Refactor.

Supports sequential wave verification (Waves 1 to 6):
- Wave 1: Baseline reconciliation, schema contract validation, pure contract tests.
- Wave 2: Context-preserving audio windows, structured cache parity.
- Wave 3: Speaker boundaries and complete-thought assembly.
- Wave 4: Grouped human review and denominator migration.
- Wave 5: Independent real-audio quality and workload evaluation.
- Wave 6: End-to-end integration and reversible rollout package.

Enforces:
- Hard requirement of prior wave receipt (--previous-receipt) for Waves > 1.
- Progress bar execution via tools/run_tests_with_progress.py with --timeout=60.
- Bounded disposable run root (--run-root).
- Full cryptographic binding (git HEAD, uncommitted state, manifests, schema digests).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.transcript_chunking_verifier_core import (
    verify_wave_1,
    verify_wave_2,
    verify_wave_3,
    verify_wave_4,
    verify_wave_5,
    verify_wave_6,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_transcript_chunking")


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcript Chunking Refactor Wave Verifier")
    parser.add_argument("--wave", type=int, required=True, help="Wave number (1-6)")
    parser.add_argument("--input-manifest", type=str, required=True, help="Path to input manifest")
    parser.add_argument("--run-root", type=str, required=True, help="Disposable execution directory")
    parser.add_argument("--receipt", type=str, required=True, help="Path to write receipt")
    parser.add_argument(
        "--previous-receipt", type=str, default=None, help="Prior wave receipt (required for wave > 1)"
    )

    args = parser.parse_args()

    manifest_path = Path(args.input_manifest).resolve()
    run_root = Path(args.run_root).resolve()
    receipt_path = Path(args.receipt).resolve()
    prev_receipt_path = Path(args.previous_receipt).resolve() if args.previous_receipt else None

    if args.wave > 1 and not prev_receipt_path:
        logger.error("Error: --previous-receipt is strictly required for Wave > 1")
        sys.exit(1)

    if args.wave == 1:
        code = verify_wave_1(manifest_path, run_root, receipt_path)
        sys.exit(code)
    elif args.wave == 2:
        code = verify_wave_2(manifest_path, run_root, receipt_path, prev_receipt_path)
        sys.exit(code)
    elif args.wave == 3:
        code = verify_wave_3(manifest_path, run_root, receipt_path, prev_receipt_path)
        sys.exit(code)
    elif args.wave == 4:
        code = verify_wave_4(manifest_path, run_root, receipt_path, prev_receipt_path)
        sys.exit(code)
    elif args.wave == 5:
        code = verify_wave_5(manifest_path, run_root, receipt_path, prev_receipt_path)
        sys.exit(code)
    elif args.wave == 6:
        code = verify_wave_6(manifest_path, run_root, receipt_path, prev_receipt_path)
        sys.exit(code)
    else:
        logger.error("Wave %d is not yet implemented (sequential wave progression).", args.wave)
        sys.exit(1)


if __name__ == "__main__":
    main()
