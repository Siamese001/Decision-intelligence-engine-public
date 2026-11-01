#!/usr/bin/env python3
"""Governed CLI Runner for Project LENS Single-Turbine Threshold Evaluation.

Compares Strict Baseline vs. Proposed Relaxed dual-model consensus thresholds,
computes explicit denominators, segregates Stage 0 capture defects, and generates
client-facing reports and machine-readable execution receipts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.lens_evaluation.evaluator import SingleTurbineEvaluator
from dual_pipeline.lens_evaluation.models import TurbinePhotoRecord
from dual_pipeline.lens_evaluation.reporter import SingleTurbineReportGenerator
from dual_pipeline.lens_evaluation.thresholds import (
    PROPOSED_RELAXED,
    STRICT_BASELINE,
)


def load_records(path: Path) -> List[TurbinePhotoRecord]:
    """Loads and validates turbine photographic input records."""
    if not path.exists():
        raise FileNotFoundError(f"Input dataset file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list of records in {path}")

    return [TurbinePhotoRecord.from_dict(item) for item in data]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Project LENS component extraction under strict vs. relaxed thresholds."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=Path("tests/fixtures/lens/single_turbine_200_photos.json"),
        help="Path to JSON file containing turbine photo records.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("reports"),
        help="Output directory for reports and receipts.",
    )
    parser.add_argument(
        "--turbine-id",
        type=str,
        default="WTG_04",
        help="Identifier of the turbine under evaluation.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console output.",
    )

    args = parser.parse_args()

    if not args.quiet:
        print("=== Project LENS Single-Turbine Evaluation Harness ===")
        print(f"Input Dataset: {args.input}")
        print(f"Turbine Target: {args.turbine_id}")

    try:
        records = load_records(args.input)
    except Exception as e:
        print(f"Error loading records: {e}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Loaded {len(records)} photographic component records.")

    # 1. Evaluate Strict Baseline
    metrics_strict, _ = SingleTurbineEvaluator.evaluate_batch(records, STRICT_BASELINE)

    # 2. Evaluate Proposed Relaxed
    metrics_relaxed, _ = SingleTurbineEvaluator.evaluate_batch(records, PROPOSED_RELAXED)

    # 3. Generate Reports & Receipts
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_md = SingleTurbineReportGenerator.generate_markdown_report(
        metrics_strict, metrics_relaxed, turbine_id=args.turbine_id
    )
    receipt_json = SingleTurbineReportGenerator.generate_json_receipt(
        metrics_strict, metrics_relaxed, str(args.input), turbine_id=args.turbine_id
    )

    # Write primary artifacts to output-dir
    report_file = args.output_dir / "LENS_SINGLE_TURBINE_THRESHOLD_COMPARISON_REPORT.md"
    receipt_file = args.output_dir / "lens_single_turbine_threshold_comparison_receipt.json"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_md)

    with open(receipt_file, "w", encoding="utf-8") as f:
        json.dump(receipt_json, f, indent=2)

    if not args.quiet:
        print("\nEvaluation Results:")
        print(f"  • Total Photographs: {metrics_strict.total_photos}")
        print(f"  • Quarantined Capture Defects: {metrics_strict.capture_defects} ({metrics_strict.defect_rate}%)")
        print(f"  • Legible Frames: {metrics_strict.legible_photos}")
        print("  -------------------------------------------------------------")
        print(f"  • Strict Baseline STP: {metrics_strict.stp_rate}% (Review: {metrics_strict.review_volume_rate}%, Correctness: {metrics_strict.checked_correctness_rate}%)")
        print(f"  • Proposed Relaxed STP: {metrics_relaxed.stp_rate}% (Review: {metrics_relaxed.review_volume_rate}%, Correctness: {metrics_relaxed.checked_correctness_rate}%)")
        print(f"  • Trade-off Delta: +{metrics_relaxed.stp_rate - metrics_strict.stp_rate:.2f}% STP for {metrics_relaxed.false_acceptances} false accepts ({metrics_relaxed.false_acceptance_rate}% error rate)")
        print("\nArtifacts emitted:")
        print(f"  • Markdown Report: {report_file}")
        print(f"  • Audit Receipt:   {receipt_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
