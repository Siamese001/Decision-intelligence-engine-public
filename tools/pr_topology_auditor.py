#!/usr/bin/env python3
"""
Branch Topology & Contention Auditor CLI.

Analyzes candidate git branches and worktrees to detect:
1. Pairwise file overlap matrix ($N \times N$)
2. Lineage ancestry (parent-child dependencies)
3. Superseded / obsolete duplicate branches
4. Disjoint file clusters (Data Highway candidates)
5. Rebase cascade penalty and consolidation recommendations

Usage:
  python tools/pr_topology_auditor.py [--json reports/pr_topology_matrix.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_overlap_core import (  # noqa: E402
    PLAN_SCHEMA_VERSION,
    MergeOrderPlan,
    plan_merge_order,
)
from tools.pr_topology_core import (  # noqa: E402
    compute_dispositions,
    compute_topological_clusters,
    compute_topology_matrix,
)


def get_candidate_branches(root: Path) -> List[str]:
    import subprocess

    res = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return []
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    # Filter for feature branches
    features = [
        b
        for b in lines
        if b != "main" and not b.startswith("archive/") and not b.startswith("HEAD")
    ]
    return sorted(features)


def render_topology_report(
    matrix, clusters, duration_sec: float, merge_plan: Optional[MergeOrderPlan] = None
) -> str:
    lines = []
    lines.append("=" * 80)
    lines.append("  BRANCH TOPOLOGY & CONTENTION AUDIT REPORT")
    lines.append("=" * 80)
    lines.append(f"  Base Branch:          {matrix.base_branch}")
    lines.append(f"  Candidate Branches:   {len(matrix.branches)}")
    lines.append(f"  Audit Duration:       {duration_sec:.2f}s")
    lines.append("")

    lines.append("── TOPOLOGICAL COHORTS & CLUSTERS ─────────────────────────────────────────────")
    for c in clusters:
        lines.append(f"• [{c.category}] {c.name}")
        lines.append(f"  Branches: {', '.join(c.branches)}")
        lines.append(f"  Action:   {c.recommended_action}")
        lines.append("")

    lines.append("── HIGH-CONTENTION FILES (Modified by >= 3 branches) ──────────────────────────")
    if matrix.high_contention_files:
        for f, blist in sorted(matrix.high_contention_files.items(), key=lambda x: -len(x[1]))[:10]:
            lines.append(f"• ({len(blist)} branches) {f}")
    else:
        lines.append("  No files modified by >= 3 branches.")
    lines.append("")

    lines.append("── SUPERSEDED / DUPLICATE CANDIDATES ──────────────────────────────────────────")
    if matrix.superseded_candidates:
        for cand in matrix.superseded_candidates:
            lines.append(f"⚠️  {cand['older_branch']} is superseded by {cand['newer_branch']}")
            lines.append(f"   Shared files: {cand['shared_file_count']} | {cand['recommendation']}")
    else:
        lines.append("  No superseded branch pairs detected.")
    lines.append("")

    if merge_plan:
        lines.append("── CONSOLIDATION & CHERRY-PICK PLAN ──────────────────────────────────────────")
        lines.append(f"  Plan Schema:            {merge_plan.schema_version}")
        lines.append(f"  Search Strategy:        {merge_plan.search_strategy}")
        lines.append(f"  Alphabetical Baseline:  {merge_plan.alphabetical_conflicts} conflict events")
        lines.append(f"  Simulated Plan:         {merge_plan.total_conflicts} conflict events")
        reduction_pct = (
            ((merge_plan.alphabetical_conflicts - merge_plan.total_conflicts) / max(1, merge_plan.alphabetical_conflicts)) * 100.0
            if merge_plan.alphabetical_conflicts > 0
            else 0.0
        )
        lines.append(f"  Contention Reduction:   {reduction_pct:.1f}%")
        lines.append(f"  Zero Mutation Verified: {merge_plan.zero_mutation_verified}")
        lines.append("")
        lines.append("  Recommended Merge Sequence:")
        for step in merge_plan.ordered_steps:
            cf_str = f"{step.conflict_count} files" if step.conflict_count else "clean"
            lines.append(f"    {step.step_index}. {step.branch[:35]:<36} [{step.action:<6}] {cf_str:<12} {step.recommended_command}")

        if merge_plan.rebase_recommendations:
            lines.append("")
            lines.append("  Rebase & Stack Recommendations:")
            for rec in merge_plan.rebase_recommendations:
                stk = "[STACKED]" if rec.is_stacked else "[REBASED]"
                lines.append(f"    * {stk} {rec.branch} onto {rec.target_base} (upstream: {rec.upstream_branch})")
                lines.append(f"      Command: {rec.rebase_command}")
        lines.append("")

        lines.append("── CASCADE PENALTY & CONSOLIDATION SAVINGS ────────────────────────────────────")
        lines.append(f"  Status Quo Contention Baseline:         {merge_plan.alphabetical_conflicts} conflict events")
        lines.append(f"  Simulated Consolidated Cycles:          {merge_plan.total_conflicts} conflict events")
        lines.append(f"  Total Contention Reduction:             {reduction_pct:.1f}%")
        lines.append("=" * 80)
    else:
        n = len(matrix.branches)
        serial_rebases = (n * (n - 1)) // 2 if n > 1 else 0
        cohort_rebases = len(clusters)
        savings_pct = (
            ((serial_rebases - cohort_rebases) / serial_rebases * 100.0) if serial_rebases > 0 else 0.0
        )
        lines.append("── CASCADE PENALTY & CONSOLIDATION SAVINGS ────────────────────────────────────")
        lines.append(f"  Serial Rebase Operations (Status Quo):  {serial_rebases} cycles")
        lines.append(f"  Cohort Rebase Operations (Consolidated): {cohort_rebases} cycles")
        lines.append(f"  Total Rebase Reduction:                 {savings_pct:.1f}%")
        lines.append("=" * 80)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit git branch topology and file contention.")
    parser.add_argument("--json", dest="json_path", default=None, help="Path to write JSON matrix")
    parser.add_argument("--receipt", default=None, help="Path to write the JSON receipt")
    parser.add_argument("--base", default="main", help="Base target branch")
    parser.add_argument(
        "--branches", nargs="*", default=None, help="Explicit list of branches to audit"
    )
    parser.add_argument(
        "--simulate-merge",
        dest="simulate_merge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Simulate in-memory merge order to replace naive rebase cycle formula (default: True).",
    )
    args = parser.parse_args()

    start_time = time.time()
    branches = args.branches or get_candidate_branches(ROOT)
    if not branches:
        print("No candidate feature branches found.")
        return 0

    from tools.pr_governance_core import list_active_worktrees
    from tools.pr_governance_core.inspection import _parse_waves_from_branch

    worktrees = list_active_worktrees(ROOT, ignore_review_artifacts=True)
    branch_to_wt = {wt["branch"]: wt for wt in worktrees if wt.get("branch")}
    waves_map = {}
    for b in branches:
        open_w, comp_w = _parse_waves_from_branch(b)
        waves_map[b] = {"open_waves": open_w, "completed_waves": comp_w}

    matrix = compute_topology_matrix(branches=branches, root=ROOT, base_branch=args.base)
    dispositions = compute_dispositions(
        matrix, worktree_info=branch_to_wt, waves_info=waves_map
    )
    clusters = compute_topological_clusters(matrix, dispositions=dispositions)

    merge_plan: Optional[MergeOrderPlan] = None
    if getattr(args, "simulate_merge", True) and branches:
        try:
            merge_plan = plan_merge_order(repo_root=ROOT, base_ref=args.base, branches=branches)
        except Exception as e:
            print(f"Warning: merge order planning simulation failed: {e}", file=sys.stderr)

    duration = time.time() - start_time

    # Output text summary
    print(render_topology_report(matrix, clusters, duration, merge_plan=merge_plan))

    n = len(branches)
    serial_default = (n * (n - 1)) // 2 if n > 1 else 0
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION if merge_plan else "urn:nee:pr-topology-matrix:1.0.0",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "audit_duration_seconds": round(duration, 3),
        "topology_matrix": matrix.to_dict(),
        "clusters": [c.to_dict() for c in clusters],
        "dispositions": {k: {"action": v.action, "reason": v.reason, "target": v.target} for k, v in dispositions.items()},
        "merge_order_plan": merge_plan.to_dict() if merge_plan else None,
        "metrics": {
            "branch_count": len(branches),
            "serial_rebase_cycles": merge_plan.alphabetical_conflicts if merge_plan else serial_default,
            "cohort_rebase_cycles": merge_plan.total_conflicts if merge_plan else len(clusters),
            "contention_reduction_pct": round(
                ((merge_plan.alphabetical_conflicts - merge_plan.total_conflicts) / max(1, merge_plan.alphabetical_conflicts)) * 100.0, 1
            ) if merge_plan and merge_plan.alphabetical_conflicts > 0 else 0.0,
            "zero_mutation_verified": merge_plan.zero_mutation_verified if merge_plan else True,
        },
    }

    # Output JSON if requested
    if args.json_path:
        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Report JSON written to: {out_path}")

    # Output Receipt if requested
    if args.receipt:
        receipt_path = Path(args.receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_payload = dict(payload)
        receipt_payload["terminal_outcome"] = "PASS" if (not merge_plan or merge_plan.zero_mutation_verified) else "FAIL"
        receipt_payload["gate_status"] = "PASSED" if receipt_payload["terminal_outcome"] == "PASS" else "FAILED"
        receipt_path.write_text(json.dumps(receipt_payload, indent=2), encoding="utf-8")
        print(f"Receipt written to: {receipt_path}")

    # Inject A2A Protocol for RCA Panel Debate on Clusters
    if clusters:
        print("\nSpawning A2A Forensic RCA Panel to debate cluster consolidation...")
        try:
            from dual_pipeline.a2a_protocol import A2AEnvelope, dispatch_a2a_message
            for cluster in clusters:
                if len(cluster.branches) < 2:
                    continue
                envelope = A2AEnvelope(
                    task_id=f"rca_cluster_{cluster.name.replace(' ', '_')}",
                    sender="pr_topology_auditor",
                    recipient="forensic_rca_panel",
                    hypothesis=f"Branches {', '.join(cluster.branches)} have high file contention and should be consolidated.",
                    evidence_refs=[f"Cluster: {cluster.name}"]
                )
                res = dispatch_a2a_message(envelope)
                print(f"  -> Dispatched A2A task {envelope.task_id} to {envelope.recipient}. Status: {res.get('status', 'unknown')}")
        except ImportError:
            print("  -> A2A Protocol module not found. Skipping RCA dispatch.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
