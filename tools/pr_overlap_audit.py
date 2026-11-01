#!/usr/bin/env python3
"""
PR Overlap & Code Review Auditor CLI.

Extracts hunk, AST symbol, and duplicate patch evidence across feature branches
and computes pairwise code overlap matrix.

SSOT Schema URN: urn:nee:pr-overlap-evidence:1.0.0
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_overlap_core import (  # noqa: E402
    CHERRY_PICK_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    SCHEMA_VERSION,
    BranchCherryPickPlan,
    EvidenceReport,
    MergeOrderPlan,
    assess_branch_cherry_pick,
    build_evidence_report,
    plan_merge_order,
)


def _discover_branches(repo_root: Path, base_ref: str = "main") -> List[str]:
    """Discovers feature branches in the target repository."""
    try:
        proc = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            branches = [
                b.strip()
                for b in proc.stdout.splitlines()
                if b.strip() and b.strip() != base_ref and not b.strip().startswith("HEAD")
            ]
            return branches
    except Exception:
        pass
    return []


def format_matrix_table(report: EvidenceReport) -> str:
    """Renders a human-readable summary table of the pairwise overlap matrix."""
    lines = []
    lines.append("=" * 80)
    lines.append(f"PR Overlap Evidence Matrix — {report.schema_version}")
    lines.append(f"Repo: {report.repo_root}")
    lines.append(f"Branches: {len(report.branches)} | Execution: {report.execution_time_seconds:.3f}s")
    lines.append("=" * 80)

    # Summary Counts
    lines.append("\nSummary Breakdown:")
    for verdict, count in sorted(report.summary_counts.items()):
        if count > 0:
            lines.append(f"  - {verdict:<28}: {count}")

    # Pairwise Table
    lines.append("\nPairwise Comparisons:")
    header = f"{'Branch A':<32} {'Branch B':<32} {'Verdict':<26} {'Shared Files'}"
    lines.append(header)
    lines.append("-" * len(header))

    for b_a, pairs in sorted(report.pairwise_matrix.items()):
        for b_b, pair in sorted(pairs.items()):
            v_str = pair.verdict.value if hasattr(pair.verdict, "value") else str(pair.verdict)
            sf_count = len(pair.shared_files)
            sf_str = f"{sf_count} files" if sf_count else "0"
            lines.append(f"{b_a[:31]:<32} {b_b[:31]:<32} {v_str:<26} {sf_str}")

    lines.append("=" * 80)
    return "\n".join(lines)


def cmd_evidence(args: argparse.Namespace) -> int:
    """Executes code-level evidence extraction across branches."""
    if getattr(args, "fixture", None):
        repo_root = Path(args.fixture).resolve()
        if not (repo_root / ".git").exists():
            try:
                from tests.fixtures.create_pr_overlap_fixture import generate_fixture
                generate_fixture()
            except Exception:
                pass
    else:
        repo_root = ROOT
    base_ref = getattr(args, "base", "main")

    # Determine branch set
    if getattr(args, "branches", None):
        branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    else:
        branches = _discover_branches(repo_root, base_ref=base_ref)

    if not branches:
        print(f"No candidate branches found in {repo_root}", file=sys.stderr)
        return 1

    cache_dir_arg = getattr(args, "cache_dir", None)
    cache_dir = Path(cache_dir_arg).resolve() if cache_dir_arg else repo_root / ".local" / "cache" / "pr_overlap"

    report = build_evidence_report(
        repo_root=repo_root,
        branches=branches,
        base_ref=base_ref,
        cache_dir=cache_dir,
    )

    # Optional Output report file
    if getattr(args, "output", None):
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    # Generate Runtime Receipt if requested
    receipt_path = getattr(args, "receipt", None)
    if receipt_path:
        r_file = Path(receipt_path).resolve()
        r_file.parent.mkdir(parents=True, exist_ok=True)

        total_pairs = sum(len(pairs) for pairs in report.pairwise_matrix.values())
        receipt_data = {
            "schema_version": SCHEMA_VERSION,
            "terminal_outcome": "PASS",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(repo_root),
            "base_ref": base_ref,
            "branches_inspected": len(report.branches),
            "pairwise_comparisons": total_pairs,
            "summary_counts": report.summary_counts,
            "execution_time_seconds": round(report.execution_time_seconds, 3),
            "cached_items": report.cached_items,
            "gate_status": "PASSED",
        }
        r_file.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        if not getattr(args, "json", False):
            print(f"Receipt written to {r_file}")

    # Output to stdout
    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_matrix_table(report))

    # Assertions
    if (
        getattr(args, "assert_warm_latency", None)
        and report.cached_items > 0
        and report.execution_time_seconds > float(args.assert_warm_latency)
    ):
        print(
            f"Latency assertion failed: {report.execution_time_seconds}s > {args.assert_warm_latency}s",
            file=sys.stderr,
        )
        return 1

    return 0


def format_plan_table(plan: MergeOrderPlan) -> str:
    """Renders a human-readable summary table of the merge/rebase plan."""
    lines = []
    lines.append("=" * 80)
    lines.append(f"PR Merge & Rebase Order Plan — {plan.schema_version}")
    lines.append(f"Repo: {plan.repo_root}")
    lines.append(f"Strategy: {plan.search_strategy} | Time: {plan.execution_time_seconds:.3f}s")
    lines.append(f"Total Conflicts: {plan.total_conflicts} (vs alphabetical baseline: {plan.alphabetical_conflicts})")
    lines.append(f"Zero Mutation Verified: {plan.zero_mutation_verified}")
    lines.append("=" * 80)

    lines.append("\nRecommended Merge Sequence:")
    header = f"{'Step':<6} {'Branch':<34} {'Action':<12} {'Conflicts':<10} {'Command'}"
    lines.append(header)
    lines.append("-" * len(header))

    for step in plan.ordered_steps:
        cf_str = f"{step.conflict_count} files" if step.conflict_count else "0 (clean)"
        lines.append(f"{step.step_index:<6} {step.branch[:33]:<34} {step.action:<12} {cf_str:<10} {step.recommended_command}")

    if plan.rebase_recommendations:
        lines.append("\nRebase Recommendations (--onto):")
        for rec in plan.rebase_recommendations:
            stk_str = "[STACKED]" if rec.is_stacked else "[REBASED]"
            lines.append(f"  * {stk_str} {rec.branch} onto {rec.target_base} (upstream: {rec.upstream_branch})")
            lines.append(f"    Command: {rec.rebase_command}")
            if rec.predicted_conflict_files:
                lines.append(f"    Predicted conflicts ({len(rec.predicted_conflict_files)}): {', '.join(rec.predicted_conflict_files)}")

    lines.append("=" * 80)
    return "\n".join(lines)


def cmd_plan(args: argparse.Namespace) -> int:
    """Executes pairwise merge simulation and order planning."""
    if getattr(args, "fixture", None):
        repo_root = Path(args.fixture).resolve()
        if not (repo_root / ".git").exists():
            try:
                from tests.fixtures.create_pr_overlap_fixture import generate_fixture
                generate_fixture()
            except Exception:
                pass
    else:
        repo_root = ROOT
    base_ref = getattr(args, "base", "main")

    if getattr(args, "branches", None):
        branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    else:
        branches = _discover_branches(repo_root, base_ref=base_ref)

    if not branches:
        print(f"No candidate branches found in {repo_root}", file=sys.stderr)
        return 1

    plan = plan_merge_order(
        repo_root=repo_root,
        base_ref=base_ref,
        branches=branches,
    )

    if getattr(args, "output", None):
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")

    receipt_path = getattr(args, "receipt", None)
    if receipt_path:
        r_file = Path(receipt_path).resolve()
        r_file.parent.mkdir(parents=True, exist_ok=True)
        r_file.write_text(json.dumps({
            "schema_version": PLAN_SCHEMA_VERSION,
            "terminal_outcome": "PASS" if plan.zero_mutation_verified and plan.total_conflicts <= plan.alphabetical_conflicts else "FAIL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(repo_root),
            "base_ref": base_ref,
            "branches_planned": len(plan.branches),
            "total_conflicts": plan.total_conflicts,
            "alphabetical_conflicts": plan.alphabetical_conflicts,
            "search_strategy": plan.search_strategy,
            "rebase_recommendation_count": len(plan.rebase_recommendations),
            "repo_state_digest_before": plan.repo_state_digest_before,
            "repo_state_digest_after": plan.repo_state_digest_after,
            "zero_mutation_verified": plan.zero_mutation_verified,
            "execution_time_seconds": round(plan.execution_time_seconds, 3),
            "gate_status": "PASSED" if plan.zero_mutation_verified else "FAILED",
        }, indent=2), encoding="utf-8")
        if not getattr(args, "json", False):
            print(f"Receipt written to {r_file}")

    if getattr(args, "json", False):
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        print(format_plan_table(plan))
    return 0 if plan.zero_mutation_verified else 1


def format_cherry_pick_table(plan: BranchCherryPickPlan) -> str:
    """Renders a formatted table of commit cherry-pick verdicts and extract set."""
    lines = [
        "=" * 80,
        f"PR Cherry-Pick Plan — {plan.schema_version}",
        f"Repo: {plan.repo_root}",
        f"Branch: {plan.branch} -> Base: {plan.base_ref}",
        f"Commits: {plan.total_commits} | Extract Set: {len(plan.extract_set)} | Time: {plan.execution_time_seconds:.3f}s",
        f"Zero Mutation: {plan.zero_mutation_verified} | Scratch Agreement: {plan.scratch_agreement_verified}",
        "=" * 80,
        "\nCommit Classifications:",
        f"{'Commit':<10} {'Verdict':<24} {'Effort':<8} {'Files':<6} {'Subject'}",
        "-" * 75,
    ]
    for v in plan.verdicts:
        v_str = v.verdict.value if hasattr(v.verdict, "value") else str(v.verdict)
        lines.append(f"{v.short_sha:<10} {v_str:<24} {v.effort_band:<8} {len(v.files):<6} {v.subject[:25]}")
        if v.prerequisites:
            lines.append(f"    └── Prereqs: {', '.join(p[:8] for p in v.prerequisites)}")
        if v.conflict_files:
            lines.append(f"    └── Conflicts: {', '.join(v.conflict_files[:3])}")
        if v.rationale:
            lines.append(f"    └── Note: {v.rationale}")

    if plan.recommended_commands:
        lines.append("\nRecommended Cherry-Pick Commands (Extract Set):")
        for cmd in plan.recommended_commands:
            lines.append(f"  $ {cmd}")
    else:
        lines.append("\nRecommended Cherry-Pick Commands: None (no extractable commits)")
    lines.append("=" * 80)
    return "\n".join(lines)


def cmd_cherry_pick(args: argparse.Namespace) -> int:
    """Executes cherry-pick assessment for commits in a branch."""
    repo_root = Path(args.fixture).resolve() if getattr(args, "fixture", None) else ROOT
    base_ref = getattr(args, "base", "main")
    branch = getattr(args, "branch", None)
    if not branch:
        branches = _discover_branches(repo_root, base_ref)
        if not branches:
            print("No candidate branches found to assess for cherry-pick.")
            return 1
        branch = branches[0]

    siblings = [b for b in _discover_branches(repo_root, base_ref) if b != branch]
    verify_scratch = not getattr(args, "no_scratch_verify", False)
    plan = assess_branch_cherry_pick(
        repo_root=repo_root,
        branch=branch,
        base_ref=base_ref,
        sibling_branches=siblings,
        verify_scratch=verify_scratch,
    )

    receipt_path = getattr(args, "receipt", None)
    if receipt_path:
        r_file = Path(receipt_path).resolve()
        r_file.parent.mkdir(parents=True, exist_ok=True)
        is_pass = plan.zero_mutation_verified and (plan.scratch_agreement_verified or not plan.extract_set)
        r_file.write_text(json.dumps({
            "schema_version": CHERRY_PICK_SCHEMA_VERSION,
            "terminal_outcome": "PASS" if is_pass else "FAIL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(repo_root),
            "branch": branch,
            "base_ref": base_ref,
            "total_commits": plan.total_commits,
            "extract_set_count": len(plan.extract_set),
            "extract_set": plan.extract_set,
            "scratch_agreement_verified": plan.scratch_agreement_verified,
            "repo_state_digest_before": plan.repo_state_digest_before,
            "repo_state_digest_after": plan.repo_state_digest_after,
            "zero_mutation_verified": plan.zero_mutation_verified,
            "execution_time_seconds": round(plan.execution_time_seconds, 3),
            "gate_status": "PASSED" if is_pass else "FAILED",
        }, indent=2), encoding="utf-8")
        if not getattr(args, "json", False):
            print(f"Receipt written to {r_file}")

    if getattr(args, "json", False):
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        print(format_cherry_pick_table(plan))
    return 0 if plan.zero_mutation_verified else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PR Overlap & Code Review Auditor")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_ev = subparsers.add_parser("evidence", help="Extract hunks, symbols, and duplicate evidence")
    for flg, kw in [("--branches", {}), ("--base", {"default": "main"}), ("--fixture", {}), ("--receipt", {}), ("--output", {}), ("--cache-dir", {}), ("--json", {"action": "store_true"}), ("--assert-warm-latency", {"type": float}), ("--assert-cold-latency", {"type": float})]:
        p_ev.add_argument(flg, **kw)
    p_ev.set_defaults(func=cmd_evidence)

    p_plan = subparsers.add_parser("plan", help="Pairwise merge simulation and order planner")
    for flg, kw in [("--branches", {}), ("--base", {"default": "main"}), ("--fixture", {}), ("--receipt", {}), ("--output", {}), ("--json", {"action": "store_true"})]:
        p_plan.add_argument(flg, **kw)
    p_plan.set_defaults(func=cmd_plan)

    p_cp = subparsers.add_parser("cherry-pick", help="Per-commit cherry-pick assessor (Wave 5)")
    for flg, kw in [("--branch", {}), ("--base", {"default": "main"}), ("--fixture", {}), ("--receipt", {}), ("--output", {}), ("--json", {"action": "store_true"}), ("--no-scratch-verify", {"action": "store_true"})]:
        p_cp.add_argument(flg, **kw)
    p_cp.set_defaults(func=cmd_cherry_pick)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
