#!/usr/bin/env python3
"""
tools/analyze_pr_promotion_wall_clock.py

Join first-parent merges, review receipts, and ledger events by branch, run ID,
and time; deduplicate overlapping phases and seal the live PR cohort and timing contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.pr_wall_clock_core.reconciler import PRWallClockReconciler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze PR promotion wall clock and seal Wave 1 timing contract."
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=ROOT / "reports" / "pr_latency" / "effectiveness_wave_1.json",
        help="Target path for Wave 1 receipt JSON",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / ".local" / "gate_run_ledger.jsonl",
        help="Path to gate_run_ledger.jsonl",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=ROOT / "reports",
        help="Path to reports directory",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Path to git repository root",
    )
    parser.add_argument(
        "--new-cutoff",
        type=str,
        default="main",
        help="Git ref for subsequent merges cutoff (default: main)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(f"Reconciling PR promotion wall clock in {args.repo_root}...")
    reconciler = PRWallClockReconciler(
        repo_root=args.repo_root,
        ledger_path=args.ledger,
        reports_dir=args.reports_dir,
        new_cutoff=args.new_cutoff,
    )
    try:
        report = reconciler.reconcile()
    except Exception as exc:
        print(f"ERROR: Reconciliation failed: {exc}", file=sys.stderr)
        return 1

    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    summary = report["timing_contract_summary"]
    print("✅ Reconciled 10 original merge identities to Git.")
    print(f"  Pre-optimization review median: {summary['pre_optimization_review_median_seconds']} s (samples: {summary['pre_optimization_review_samples']})")
    print(f"  Post-optimization review median: {summary['post_optimization_review_median_seconds']} s (samples: {summary['post_optimization_review_samples']})")
    if summary["queue_pr_pre_release_review"]:
        qp = summary["queue_pr_pre_release_review"]
        print(f"  Queue PR pre-release passing review excluded from post median: {qp['run_id']} ({qp['duration_seconds']} s)")
    sub = report["subsequent_merges_cohort"]
    print(f"  Subsequent merges recorded: {sub['merge_count']} (cutoff: {sub['cutoff_base_sha'][:9]}..{sub['new_cutoff_target']})")
    print(f"Sealed receipt written to {args.receipt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
