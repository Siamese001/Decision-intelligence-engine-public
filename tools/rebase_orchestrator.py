#!/usr/bin/env python3
"""Governed Multi-Agent Rebase Orchestrator & Fast Landing CLI (Wave 5).

Serializes branch rebasing, readiness verification, and PR landing under the
governed merge queue lock (.git/governed_merge_queue.lock). Prevents multi-agent
convoy livelocks, duplicate work, and race conditions during high-concurrency
worktree development.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.common import slugify_branch  # noqa: E402
from tools.pr_governance_core.merge_queue import (  # noqa: E402
    GovernedMergeQueueLock,
    force_break_queue_lock,
    inspect_queue_lock,
)
from tools.pr_governance_core.sync import RebaseOutcome, rebase_branch_onto_main  # noqa: E402

logger = logging.getLogger("tools.rebase_orchestrator")


@dataclass
class RebaseResult:
    """Outcome of a governed rebase operation."""

    branch: str
    status: str  # SUCCESS, UP_TO_DATE, CONFLICT, FAILED
    commits_rebased: int = 0
    duration_seconds: float = 0.0
    head_sha_before: str = ""
    head_sha_after: str = ""
    conflict_files: List[str] = field(default_factory=list)
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LandResult:
    """Outcome of a governed branch landing operation."""

    branch: str
    status: str  # SUCCESS, FAILED
    merge_commit_sha: str = ""
    readiness_passed: bool = False
    duration_seconds: float = 0.0
    receipt_path: str = ""
    error_message: str = ""
    rebase_status: str = "NOT_RUN"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_head_sha(cwd: Path) -> str:
    with contextlib.suppress(Exception):
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True, text=True, check=True, timeout=10.0)
        return res.stdout.strip()
    return "unknown"


def _outcome_to_rebase_result(outcome: RebaseOutcome) -> RebaseResult:
    status = "FAILED" if outcome.status == "ERROR" else outcome.status
    err_msg = outcome.message if status in ("CONFLICT", "FAILED") else ""
    return RebaseResult(
        branch=outcome.branch,
        status=status,
        commits_rebased=outcome.commits_rebased,
        duration_seconds=outcome.duration_sec,
        head_sha_before=outcome.head_sha_before,
        head_sha_after=outcome.head_sha_after,
        conflict_files=list(outcome.conflict_files),
        error_message=err_msg,
    )


def resolve_worktree_for_branch(branch: str, repo_root: Path) -> Path:
    """Finds the isolated worktree directory for the given branch, falling back to repo_root."""
    slug = slugify_branch(branch)
    candidate = repo_root / ".worktrees" / slug
    if candidate.is_dir():
        return candidate
    if not (repo_root / ".worktrees").is_dir():
        return repo_root
    try:
        from tools.pr_governance_core.worktree import list_active_worktrees

        for wt in list_active_worktrees(repo_root):
            if wt.get("branch") == branch and Path(str(wt.get("path"))).exists():
                return Path(str(wt.get("path")))
    except Exception as exc:
        _ = exc
    return repo_root


def rebase_branch_serialized(
    branch: str,
    base_branch: str = "main",
    repo_root: Optional[Path] = None,
    timeout_sec: float = 300.0,
    non_blocking: bool = False,
) -> RebaseResult:
    """Rebases the specified branch onto base_branch under the governed queue lock."""
    root = repo_root or ROOT
    work_dir = resolve_worktree_for_branch(branch, root)

    with GovernedMergeQueueLock(
        branch=branch,
        repo_root=root,
        timeout_sec=timeout_sec,
        non_blocking=non_blocking,
    ):
        outcome = rebase_branch_onto_main(
            branch=branch,
            base_branch=base_branch,
            repo_root=root,
            work_dir=work_dir,
            use_lock=False,
            timeout_sec=timeout_sec,
        )
        return _outcome_to_rebase_result(outcome)


def _run_readiness_qualification(branch: str, work_dir: Path) -> bool:
    from tools.pr_governance_core.lifecycle import cmd_review

    _ = work_dir
    rev_args = argparse.Namespace(
        branch=branch,
        fail_fast=False,
        skip_readiness_run=False,
        recheck_failed_only=False,
        full=False,
        llm_summary=False,
    )
    return cmd_review(rev_args) == 0


def _run_pr_merge(branch: str, title: str, delete_branch: bool, work_dir: Path) -> int:
    from tools.pr_governance_core.lifecycle import cmd_merge

    _ = work_dir
    merge_args = argparse.Namespace(
        branch=branch,
        title=title,
        fail_fast=False,
        skip_readiness_run=True,
        recheck_failed_only=False,
        full=False,
        delete_branch=delete_branch,
        retry=0,
        llm_summary=False,
    )
    return cmd_merge(merge_args)


def land_branch_serialized(
    branch: str,
    title: str,
    base_branch: str = "main",
    repo_root: Optional[Path] = None,
    delete_branch: bool = True,
    timeout_sec: float = 600.0,
) -> LandResult:
    """Performs serialized rebase, cached readiness qualification, and landing to main."""
    root = repo_root or ROOT
    work_dir = resolve_worktree_for_branch(branch, root)
    start_t = time.monotonic()

    # 1. Initial rebase onto base_branch if behind (executed outside lock)
    reb_outcome = rebase_branch_onto_main(
        branch=branch,
        base_branch=base_branch,
        repo_root=root,
        work_dir=work_dir,
        use_lock=False,
        timeout_sec=timeout_sec,
    )
    reb_res = _outcome_to_rebase_result(reb_outcome)
    if reb_res.status not in ("SUCCESS", "UP_TO_DATE"):
        return LandResult(
            branch=branch,
            status="FAILED",
            duration_seconds=round(time.monotonic() - start_t, 3),
            rebase_status=reb_res.status,
            error_message=f"Rebase failed with status {reb_res.status}: {reb_res.error_message}",
        )

    # 2. Run cached readiness qualification (executed outside lock)
    readiness_ok = _run_readiness_qualification(branch, work_dir)
    if not readiness_ok:
        return LandResult(
            branch=branch,
            status="FAILED",
            readiness_passed=False,
            rebase_status=reb_res.status,
            duration_seconds=round(time.monotonic() - start_t, 3),
            error_message="Readiness qualification failed promotion criteria.",
        )

    # 3. Hold trunk lock strictly for final catch-up verification and landing
    with GovernedMergeQueueLock(branch=branch, repo_root=root, timeout_sec=timeout_sec):
        catchup_outcome = rebase_branch_onto_main(
            branch=branch,
            base_branch=base_branch,
            repo_root=root,
            work_dir=work_dir,
            use_lock=False,
            timeout_sec=timeout_sec,
        )
        catchup_reb = _outcome_to_rebase_result(catchup_outcome)
        if catchup_reb.status not in ("SUCCESS", "UP_TO_DATE"):
            return LandResult(
                branch=branch,
                status="FAILED",
                duration_seconds=round(time.monotonic() - start_t, 3),
                rebase_status=catchup_reb.status,
                error_message=f"Catch-up rebase failed: {catchup_reb.error_message}",
            )

        merge_rc = _run_pr_merge(branch, title, delete_branch, work_dir)
        dur = round(time.monotonic() - start_t, 3)
        slug = slugify_branch(branch)
        receipt_p = root / "reports" / f"pr_merge_{slug}_receipt.json"

        if merge_rc != 0:
            return LandResult(
                branch=branch,
                status="FAILED",
                readiness_passed=True,
                rebase_status=catchup_reb.status,
                duration_seconds=dur,
                error_message="Governed merge command returned non-zero status.",
            )

        merge_sha = _get_head_sha(root)
        return LandResult(
            branch=branch,
            status="SUCCESS",
            merge_commit_sha=merge_sha,
            readiness_passed=True,
            rebase_status=catchup_reb.status,
            duration_seconds=dur,
            receipt_path=str(receipt_p) if receipt_p.is_file() else "",
        )


def cmd_rebase(args: argparse.Namespace) -> int:
    res = rebase_branch_serialized(
        branch=args.branch,
        base_branch=args.base_branch,
        timeout_sec=args.timeout,
        non_blocking=args.non_blocking,
    )
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(f"Rebase outcome for '{args.branch}': {res.status} ({res.duration_seconds}s)")
        if res.error_message:
            print(f"Details: {res.error_message}")
    return 0 if res.status in ("SUCCESS", "UP_TO_DATE") else 1


def cmd_land(args: argparse.Namespace) -> int:
    res = land_branch_serialized(
        branch=args.branch,
        title=args.title,
        base_branch=args.base_branch,
        delete_branch=not args.keep_branch,
        timeout_sec=args.timeout,
    )
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(f"Land outcome for '{args.branch}': {res.status} in {res.duration_seconds}s")
        if res.merge_commit_sha:
            print(f"Merge Commit SHA: {res.merge_commit_sha}")
        if res.error_message:
            print(f"Error: {res.error_message}")
    return 0 if res.status == "SUCCESS" else 1


def cmd_status(args: argparse.Namespace) -> int:
    repo_root = getattr(args, "repo_root", None)
    info = inspect_queue_lock(repo_root=repo_root)
    if info is None:
        print("🟢 Governed Merge Queue is idle (no active reservations).")
        return 0
    if getattr(args, "json", False):
        print(json.dumps(info.to_dict(), indent=2))
    else:
        alive_str = "ACTIVE" if info.worktree_path == "True" else "DEAD/ORPHANED"
        print(f"🟡 Merge Queue RESERVED by branch '{info.branch}'")
        print(f"   Holder PID:   {info.holder_pid} ({alive_str})")
        print(f"   Acquired At:  {info.acquired_at}")
        print(f"   Heartbeat:    {info.heartbeat_at}")
        print(f"   Timeout:      {info.timeout_sec}s")
    return 0


def cmd_break(args: argparse.Namespace) -> int:
    repo_root = getattr(args, "repo_root", None)
    broken = force_break_queue_lock(repo_root=repo_root)
    if broken:
        print("✅ Active merge queue lock forcibly removed.")
    else:
        print("ℹ️ No active merge queue lock file found.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Governed Multi-Agent Rebase & Landing Orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    # rebase
    p_reb = sub.add_parser("rebase", help="Rebase branch onto base under queue lock.")
    p_reb.add_argument("branch", help="Feature branch to rebase.")
    p_reb.add_argument("--base-branch", default="main", help="Base branch (default: main).")
    p_reb.add_argument("--timeout", type=float, default=300.0, help="Lock timeout in seconds.")
    p_reb.add_argument("--non-blocking", action="store_true", help="Fail immediately if locked.")
    p_reb.add_argument("--json", action="store_true", help="Emit JSON output.")

    # land
    p_lnd = sub.add_parser("land", help="Serialize rebase + readiness + merge to main.")
    p_lnd.add_argument("branch", help="Feature branch to land.")
    p_lnd.add_argument("--title", required=True, help="Merge commit title.")
    p_lnd.add_argument("--base-branch", default="main", help="Base branch (default: main).")
    p_lnd.add_argument("--keep-branch", action="store_true", help="Keep branch ref after merge.")
    p_lnd.add_argument("--timeout", type=float, default=600.0, help="Lock timeout in seconds.")
    p_lnd.add_argument("--json", action="store_true", help="Emit JSON output.")

    # status
    p_sts = sub.add_parser("status", help="Inspect merge queue lock status.")
    p_sts.add_argument("--json", action="store_true", help="Emit JSON output.")

    # break-lock
    sub.add_parser("break-lock", help="Forcibly remove active merge queue lock.")

    args = parser.parse_args(argv)
    if args.command == "rebase":
        return cmd_rebase(args)
    elif args.command == "land":
        return cmd_land(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "break-lock":
        return cmd_break(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
