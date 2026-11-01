#!/usr/bin/env python3
"""
Local PR & Git Governance CLI Tool (Solo-Developer Workflow).

Target Pipeline:
  Feature Branch → Pre-Commit → Streamlined Pre-Merge → Local Main → Origin Main

Enforces the repository's governed PR process for local main:
- `start` / `create`: Provision an isolated feature worktree (.worktrees/<slug>) or branch.
- `doctor`: Audit git hooks configuration, file permissions, python runtime, and gate tools.
- `list`: Show candidate feature branches with ahead/behind stats and mergeability.
- `review`: Run streamlined pre-merge validation (risk-based 6/22/30 readiness, diff safety, clean merge).
- `merge`: Complete streamlined pre-merge validation + governed --no-ff merge into local main.
- `promote`: Streamlined candidate branch promotion to local main.

Usage:
  python tools/pr_governance.py start feat/<name>
  python tools/pr_governance.py review <branch-name>
  python tools/pr_governance.py merge <branch-name> --title "feat(scope): <Description>"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os

if sys.prefix == sys.base_prefix:
    venv_py = (
        (ROOT / ".venv" / "bin" / "python")
        if os.name != "nt"
        else (ROOT / ".venv" / "Scripts" / "python.exe")
    )
    if venv_py.exists():
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)

from tools.pr_governance_core import (  # noqa: E402
    CONVENTIONAL_BRANCH_RE, CONVENTIONAL_COMMIT_RE, REQUIRED_HOOKS, ROOT,
    check_merge_conflict, cmd_authorize_merge, cmd_create, cmd_dehydrate, cmd_doctor, cmd_finish, cmd_inventory,
    cmd_list, cmd_merge, cmd_rebase, cmd_review, cmd_start, cmd_sync,
    cmd_worktree_list, get_feature_branches, inspect_branch_status,
    resolve_python, run_git, slugify_branch,
)

__all__ = [
    "CONVENTIONAL_BRANCH_RE", "CONVENTIONAL_COMMIT_RE", "REQUIRED_HOOKS", "ROOT",
    "check_merge_conflict", "cmd_authorize_merge", "cmd_create", "cmd_dehydrate", "cmd_doctor", "cmd_finish", "cmd_inventory",
    "cmd_list", "cmd_merge", "cmd_rebase", "cmd_review", "cmd_start", "cmd_sync",
    "cmd_worktree_list", "get_feature_branches", "inspect_branch_status",
    "resolve_python", "run_git", "slugify_branch", "build_parser", "main",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local PR & Git Governance Tool.")
    sub = parser.add_subparsers(dest="command", required=True)

    # start (worktree provisioning)
    p_start = sub.add_parser("start", help="Provision an isolated worktree for a feature branch.")
    p_start.add_argument("branch", help="Name of feature branch (e.g. feat/my-feature).")

    # dehydrate (worktree folder removal while keeping branch)
    p_dehyd = sub.add_parser("dehydrate", help="Dehydrate an isolated worktree while preserving the branch.")
    p_dehyd.add_argument("branch", help="Name of feature branch to dehydrate.")

    # finish (worktree teardown)
    p_finish = sub.add_parser("finish", help="Tear down an isolated worktree after merge.")
    p_finish.add_argument("branch", help="Name of feature branch.")
    p_finish.add_argument("--force", action="store_true", help="Force removal even if not merged.")
    p_finish.add_argument("--ingest", action="store_true", help="Permit teardown of post-mutation ingestion worktrees.")
    p_finish.add_argument(
        "--delete-branch",
        dest="delete_branch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete branch after worktree removal (default: True).",
    )
    p_finish.add_argument(
        "--confirm-token",
        dest="confirm_token",
        help="Confirmation token for automated or HITL-approved worktree finishing.",
    )

    # sync (Stage A: Rebase + Post-Rebase Readiness)
    p_sync = sub.add_parser(
        "sync", help="Synchronize branch with main (rebase + post-rebase readiness qualification)."
    )
    p_sync.add_argument("branch", help="Name of feature branch to synchronize.")
    p_sync.add_argument(
        "--skip-readiness",
        action="store_true",
        help="Skip post-rebase readiness qualification (rebase only).",
    )
    p_sync.add_argument(
        "--fail-fast",
        action="store_true",
        help="Halt readiness qualification immediately upon first stage failure.",
    )
    p_sync.add_argument(
        "--full",
        action="store_true",
        help="Run full un-scoped 32-gate test suite without risk-proportional exemptions.",
    )
    p_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate readiness verification without running physical tests.",
    )
    p_sync.add_argument(
        "--prune-caches",
        action="store_true",
        help="Prune stale readiness caches, coverage packages, and expired receipts.",
    )

    # create
    p_create = sub.add_parser("create", help="Create a conventional feature branch from main.")
    p_create.add_argument("branch", help="Name of feature branch (e.g. feat/my-feature).")

    # rebase
    p_rebase = sub.add_parser("rebase", help="Rebase a feature branch onto latest main in its isolated worktree.")
    p_rebase.add_argument("branch", help="Name of feature branch to rebase.")

    # doctor
    p_doc = sub.add_parser("doctor", help="Audit local git hooks, python runtime, and gate tools.")
    p_doc.add_argument(
        "--prune-caches",
        action="store_true",
        help="Prune stale readiness caches, coverage packages, and expired receipts.",
    )
    p_doc.add_argument(
        "--retention-days",
        type=int,
        default=7,
        help="Retention period in days for cache pruning (default: 7).",
    )

    # list
    p_list = sub.add_parser("list", help="List candidate unmerged feature branches.")
    p_list.add_argument(
        "--json", action="store_true", help="Output raw JSON instead of text table."
    )
    p_list.add_argument("--receipt", help="Path to write the JSON receipt.")
    p_list.add_argument(
        "--view",
        choices=["executive", "compact", "wide", "decision"],
        default="executive",
        help="View mode for queue rendering (default: executive).",
    )
    p_list.add_argument(
        "--llm-summary",
        action="store_true",
        help="Generate advisory LLM intent summaries (off by default).",
    )
    p_list.add_argument(
        "--remote",
        action="store_true",
        help="Include remote-tracking branches with no local counterpart.",
    )
    p_list.add_argument(
        "--fetch",
        action="store_true",
        help="Execute 'git fetch --prune origin' before branch discovery.",
    )
    p_list.add_argument(
        "--github",
        action="store_true",
        help="Query GitHub PRs and correlate with branches (cached).",
    )
    p_list.add_argument(
        "--overlap",
        action="store_true",
        help="Include code-level overlap and reorder context in next actions.",
    )
    p_list.add_argument(
        "--simulate-merge",
        action="store_true",
        help="Simulate merge order for stacked rebase advice.",
    )
    p_list.add_argument(
        "--council",
        action="store_true",
        help="Include 5-Agent Council deliberation and merge rationalization.",
    )
    p_list.add_argument(
        "--from-snapshot",
        help="Load queue from pre-computed JSON snapshot instead of live git topology scan.",
    )

    # review
    p_rev = sub.add_parser("review", help="Review a candidate feature branch.")
    p_rev.add_argument("branch", help="Name of feature branch to review.")
    p_rev.add_argument(
        "--skip-readiness-run",
        action="store_true",
        help="Require an existing receipt; the runner still validates input equivalence.",
    )
    p_rev.add_argument(
        "--force-readiness",
        action="store_true",
        help="Force re-running readiness gates even if valid attestation exists.",
    )
    p_rev.add_argument(
        "--fail-fast",
        action="store_true",
        help="Halt readiness qualification immediately upon first stage failure.",
    )
    p_rev.add_argument(
        "--recheck-failed-only",
        action="store_true",
        help="Only re-execute stages that failed in prior run.",
    )
    p_rev.add_argument("--full", action="store_true", help="Run full un-scoped test suites.")
    p_rev.add_argument(
        "--llm-summary",
        action="store_true",
        help="Include advisory LLM intent summary in code review.",
    )
    p_rev.add_argument(
        "--remote",
        action="store_true",
        help="Allow reviewing remote-tracking branch without local checkout.",
    )
    p_rev.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch latest branches from origin before review.",
    )
    p_rev.add_argument(
        "--github",
        action="store_true",
        help="Query GitHub PR details for branch under review.",
    )
    p_rev.add_argument(
        "--retry",
        type=int,
        default=0,
        help="Retry ordinal recorded in PR latency telemetry (default: 0).",
    )
    p_rev.add_argument(
        "--allow-open-waves",
        action="store_true",
        help="Permit review even if branch plan contains uncompleted waves.",
    )

    # merge
    p_mrg = sub.add_parser("merge", help="Execute streamlined pre-merge validation and governed merge into main.")
    p_mrg.add_argument("branch", help="Name of feature branch to merge.")
    p_mrg.add_argument("--title", required=True, help="Merge title or topic summary.")
    p_mrg.add_argument(
        "--delete-branch",
        dest="delete_branch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete branch after merge (default: True).",
    )
    p_mrg.add_argument(
        "--skip-readiness-run",
        action="store_true",
        help="Require an existing receipt; runner validates input equivalence.",
    )
    p_mrg.add_argument(
        "--force-readiness",
        action="store_true",
        help="Force re-running readiness gates even if valid attestation exists.",
    )
    p_mrg.add_argument(
        "--fail-fast",
        action="store_true",
        help="Halt qualification immediately upon first stage failure.",
    )
    p_mrg.add_argument(
        "--recheck-failed-only",
        action="store_true",
        help="Only re-execute stages that failed in prior run.",
    )
    p_mrg.add_argument("--full", action="store_true", help="Run full un-scoped test suites.")

    # worktrees
    sub.add_parser("worktrees", help="List all active worktrees and their dirty state.")
    # inventory
    p_inv = sub.add_parser("inventory", help="Full-coverage inventory of all worktrees, orphan dirs, and git stashes.")
    p_inv.add_argument("--json", action="store_true", help="Output raw JSON.")
    p_inv.add_argument("--remote", action="store_true", help="Include remote-tracking branches in inventory.")
    p_inv.add_argument("--fetch", action="store_true", help="Fetch latest branches from origin before inventory.")
    p_inv.add_argument("--github", action="store_true", help="Include GitHub PR state in inventory.")

    # gc
    p_gc = sub.add_parser(
        "gc", help="Worktree garbage collector (dry-run by default; use --execute to prune)."
    )
    p_gc.add_argument(
        "--execute", action="store_true", help="Execute worktree pruning (default is dry-run)."
    )
    p_gc.add_argument(
        "--dry-run", action="store_true", help="Explicitly request dry run (default)."
    )

    # queue
    p_que = sub.add_parser("queue", help="Inspect or break merge queue lock.")
    p_que.add_argument(
        "action",
        choices=["status", "break-lock"],
        default="status",
        nargs="?",
        help="Queue action.",
    )
    p_que.add_argument("--json", action="store_true", help="Emit JSON output.")

    # council
    p_council = sub.add_parser(
        "council",
        help="5-Agent Council deliberation on candidate queue prioritization and action hardening.",
    )
    p_council.add_argument(
        "--json", action="store_true", help="Output raw JSON instead of text table."
    )
    p_council.add_argument("--receipt", help="Path to write the JSON receipt.")
    p_council.add_argument(
        "--remote",
        action="store_true",
        help="Include remote-tracking branches with no local counterpart.",
    )
    p_council.add_argument(
        "--fetch",
        action="store_true",
        help="Execute 'git fetch --prune origin' before branch discovery.",
    )
    p_council.add_argument(
        "--github",
        action="store_true",
        help="Query GitHub PRs and correlate with branches (cached).",
    )
    p_council.add_argument(
        "--from-snapshot",
        help="Load queue from pre-computed JSON snapshot instead of live git topology scan.",
    )

    # promote
    p_prm = sub.add_parser("promote", help="Execute streamlined pre-merge promotion to main.")
    p_prm.add_argument("branch", help="Name of feature branch to promote.")
    p_prm.add_argument("--title", required=True, help="Promotion merge title.")
    p_prm.add_argument(
        "--skip-readiness-run",
        action="store_true",
        help="Require an existing receipt; runner validates input equivalence.",
    )
    p_prm.add_argument(
        "--fail-fast",
        action="store_true",
        help="Halt qualification immediately upon first stage failure.",
    )
    p_prm.add_argument(
        "--recheck-failed-only",
        action="store_true",
        help="Only re-execute stages that failed in prior run.",
    )
    p_prm.add_argument("--full", action="store_true", help="Run full un-scoped test suites.")
    p_prm.add_argument(
        "--delete-branch",
        dest="delete_branch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete branch after merge (default: True).",
    )
    p_prm.add_argument(
        "--retry",
        type=int,
        default=0,
        help="Retry ordinal recorded in telemetry (default: 0).",
    )

    # submit (atomic single-lease sync + review + merge)
    p_sub = sub.add_parser(
        "submit",
        help="Atomic single-lease submission (sync + review + governed merge into main under lock).",
    )
    p_sub.add_argument("branch", help="Name of feature branch to submit.")
    p_sub.add_argument("--title", required=True, help="Submission merge title.")
    p_sub.add_argument(
        "--skip-readiness-run",
        action="store_true",
        help="Require an existing receipt; runner validates input equivalence.",
    )
    p_sub.add_argument(
        "--fail-fast",
        action="store_true",
        help="Halt qualification immediately upon first stage failure.",
    )
    p_sub.add_argument(
        "--recheck-failed-only",
        action="store_true",
        help="Only re-execute stages that failed in prior run.",
    )
    p_sub.add_argument("--full", action="store_true", help="Run full un-scoped test suites.")
    p_sub.add_argument(
        "--delete-branch",
        dest="delete_branch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete branch after merge (default: True).",
    )
    p_sub.add_argument(
        "--retry",
        type=int,
        default=0,
        help="Retry ordinal recorded in telemetry (default: 0).",
    )

    # repair-index
    p_rep = sub.add_parser("repair-index", help="Recover stale index lock, reap daemons, and restore missing files.")
    p_rep.add_argument("worktree", help="Worktree slug, branch, or path to repair.")
    p_rep.add_argument("--no-force-lock", dest="force_lock", action="store_false", help="Do not force break live locks.")

    # authorize-merge
    p_auth = sub.add_parser("authorize-merge", help="Record interactive human PR merge authorization into HITL ledger.")
    p_auth.add_argument("branch", help="Name of feature branch authorized for merge.")
    p_auth.add_argument("--selected-option", dest="selected_option", default=None, help="Selected option text.")
    p_auth.add_argument("--question", default=None, help="Question text presented to the operator.")
    p_auth.add_argument("--options", nargs="+", default=None, help="Full list of display options presented.")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    dispatch = {
        "start": cmd_start, "dehydrate": cmd_dehydrate, "finish": cmd_finish, "sync": cmd_sync, "create": cmd_create,
        "doctor": cmd_doctor, "list": cmd_list, "worktrees": cmd_worktree_list,
        "inventory": cmd_inventory, "rebase": cmd_rebase, "review": cmd_review, "merge": cmd_merge,
        "authorize-merge": cmd_authorize_merge,
    }
    if args.command in dispatch:
        return dispatch[args.command](args)
    if args.command == "repair-index":
        from tools.pr_governance_core.index_repair import cmd_repair_index
        return cmd_repair_index(args)
    if args.command == "gc":
        from tools.pr_governance_core.gc import cmd_gc
        return cmd_gc(args)
    if args.command in ("promote", "submit"):
        from tools.pr_governance_core.promotion import cmd_promote
        return cmd_promote(args)
    if args.command == "council":
        from tools.pr_governance_core.inspection import cmd_council
        return cmd_council(args)
    if args.command == "queue":
        from tools.rebase_orchestrator import cmd_break, cmd_status
        return cmd_status(args) if args.action == "status" else cmd_break(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
