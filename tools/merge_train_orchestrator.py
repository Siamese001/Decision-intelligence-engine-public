#!/usr/bin/env python3
"""
Merge Train Orchestrator CLI.

Orchestrates speculative batch qualification for engine cohorts and fast-tracks
client deliverables through the Data Highway.

Usage:
  python tools/merge_train_orchestrator.py --cohort platform_velocity --dry-run
  python tools/merge_train_orchestrator.py --data-highway feat/live-dociq_q4_scope_bailey_amit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.merge_train_core import (  # noqa: E402
    assemble_speculative_cohort,
    verify_and_route_data_highway,
)

PRESET_COHORTS = {
    "platform_velocity": [
        "feat/git-hook-performance-optimization",
        "feat/rca-rebase-latency-optimization",
    ],
    "client_deliverables": [
        "feat/live-dociq_q4_scope_bailey_amit",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchestrate speculative PR merge trains.")
    parser.add_argument(
        "--cohort", choices=["platform_velocity", "client_deliverables", "custom"], default=None
    )
    parser.add_argument(
        "--branches", nargs="*", default=None, help="Explicit branches for custom cohort"
    )
    parser.add_argument(
        "--data-highway",
        dest="data_branch",
        default=None,
        help="Fast-track branch via Data Highway",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Simulate assembly without creating git refs",
    )
    parser.add_argument("--json", dest="json_path", default=None, help="Output JSON receipt path")
    args = parser.parse_args()

    # Route 1: Data Highway Fast-Track
    if args.data_branch:
        res = verify_and_route_data_highway(args.data_branch, root=ROOT)
        print("=" * 80)
        print(f"  DATA HIGHWAY FAST-TRACK AUDIT: {args.data_branch}")
        print("=" * 80)
        print(f"  Eligibility:    {'✅ ELIGIBLE' if res.is_eligible else '❌ INELIGIBLE'}")
        print(f"  Triad Contract: {'✅ COMPLETE (01/02/03)' if res.has_triad else '❌ INCOMPLETE'}")
        print(f"  Total Files:    {len(res.files)}")
        print(f"  Evaluation:     {res.duration_seconds:.3f}s")
        print(
            f"  Final Decision: {'🚀 AUTHORIZED FOR DATA HIGHWAY MERGE' if res.fast_track_passed else '⛔ REJECTED FROM DATA HIGHWAY'}"
        )
        if res.violations:
            print("  Violations:")
            for v in res.violations:
                print(f"    • {v}")
        print("=" * 80)

        if args.json_path:
            out_path = Path(args.json_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")
            print(f"Receipt written to: {out_path}")
        return 0 if res.fast_track_passed else 1

    # Route 2: Cohort Train Assembly
    branches = args.branches
    cohort_name = args.cohort or "custom_cohort"
    if not branches and args.cohort in PRESET_COHORTS:
        branches = PRESET_COHORTS[args.cohort]

    if not branches:
        print("Error: No branches specified. Use --cohort or --branches.", file=sys.stderr)
        return 1

    receipt = assemble_speculative_cohort(
        cohort_name=cohort_name,
        branches=branches,
        root=ROOT,
        dry_run=args.dry_run,
    )

    print("=" * 80)
    print(f"  SPECULATIVE COHORT ASSEMBLY RECEIPT: {cohort_name.upper()}")
    print("=" * 80)
    print(f"  Constituent Branches: {len(receipt.branches)}")
    for b in receipt.branches:
        print(f"    • {b}")
    print(
        f"  Simulation Status:    {'✅ CLEAN (0 Conflicts)' if receipt.is_conflict_free else '❌ CONFLICTS DETECTED'}"
    )
    print(f"  Consolidated Files:   {len(receipt.consolidated_files)} files modified across cohort")
    print("")
    print("── COMMIT ATTRIBUTION TRAILERS ────────────────────────────────────────────────")
    for t in receipt.commit_trailers:
        print(f"  {t}")
    print("=" * 80)

    if args.json_path:
        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")
        print(f"Receipt written to: {out_path}")

    return 0 if receipt.simulation_passed else 1


if __name__ == "__main__":
    sys.exit(main())
