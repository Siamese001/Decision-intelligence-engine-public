#!/usr/bin/env python3
"""tools/benchmark_pr_promotion.py

Benchmark PR promotion latency across representative candidates.
Measures all 10 phases from instruction intake through cleanup,
accounting for cold and warm runs across documentation, application, and governance workloads.
Emits immutable phase receipts for Wave 1 baseline and future comparison waves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_pr_promotion_core import (
    DEFAULT_PERCENTILE_METHOD,
    DEFAULT_WORKLOADS,
    PROMOTION_PHASES,
    compute_stats,
    init_sandbox_git_repo,
    percentile,
    replay_candidate_promotion,
    run_benchmark,
    validate_wave2_scope_qualifications,
    validate_wave3_reuse_qualifications,
)

# Backward-compatibility aliases
_compute_stats = compute_stats
_percentile = percentile
_init_sandbox_git_repo = init_sandbox_git_repo
_replay_candidate_promotion = replay_candidate_promotion
_validate_wave2_scope_qualifications = validate_wave2_scope_qualifications
_validate_wave3_reuse_qualifications = validate_wave3_reuse_qualifications


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PR promotion latency benchmark.")
    parser.add_argument(
        "--phase",
        choices=["baseline", "scope", "reuse", "compare"],
        default="baseline",
        help="Benchmark phase (default: baseline).",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="Path to write the immutable phase receipt.",
    )
    parser.add_argument(
        "--workloads",
        default="documentation,application",
        help="Comma-separated workload names (default: documentation,application).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of paired iterations per workload (default: 1).",
    )
    parser.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fast mode for representative simulated gates (default: True).",
    )

    parser.add_argument(
        "--baseline-receipt",
        type=Path,
        default=None,
        help="Path to baseline receipt for comparative verification (default: reports/local_gate_consolidation/wave-1.json).",
    )

    args = parser.parse_args(argv)
    workload_list = [w.strip() for w in args.workloads.split(",") if w.strip()]

    receipt = run_benchmark(
        phase=args.phase,
        receipt_path=args.receipt,
        baseline_receipt_path=args.baseline_receipt,
        workloads=workload_list,
        iterations=args.iterations,
        fast=args.fast,
        repo_root=ROOT,
    )
    return 0 if receipt.get("terminal_outcome") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
