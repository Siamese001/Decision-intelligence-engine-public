#!/usr/bin/env python3
"""Governed Worktree Index Repair Engine.

Provides safe, non-destructive restoration of interrupted worktrees, recovering
abandoned locks, reaping ghost daemons, and repopulating missing files via
checkout-index without producing cascading false unstaged deletions.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, List, Optional

from .common import ROOT, run_git, slugify_branch
from .worktree_daemon_reaper import reap_worktree_fsmonitor
from .worktree_lock import reclaim_stale_worktree_lock

logger = logging.getLogger("tools.pr_governance_core.index_repair")


@dataclass
class RepairResult:
    """Outcome of worktree index recovery and tree parity restoration."""

    success: bool
    worktree_slug: str
    worktree_path: str
    lock_reclaimed: bool
    daemons_reaped: List[int]
    unstaged_deletions: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_target_worktree(target: str, root: Path = ROOT) -> Path:
    """Resolves arbitrary target string (slug, branch, or path) to worktree directory."""
    raw_path = Path(target)
    if raw_path.is_dir() and (raw_path / ".git").exists():
        return raw_path.resolve()

    slug = slugify_branch(target)
    candidate = root / ".worktrees" / slug
    if candidate.is_dir():
        return candidate.resolve()

    for item in (root / ".worktrees").glob("*"):
        if item.is_dir() and (item.name == target or item.name == slug):
            return item.resolve()

    # Search porcelain list
    try:
        raw_list = run_git(["worktree", "list", "--porcelain"], cwd=root)
        cur_path = None
        for line in raw_list.splitlines():
            if line.startswith("worktree "):
                cur_path = Path(line.split(" ", 1)[1].strip())
            elif line.startswith("branch refs/heads/") and cur_path:
                branch = line.split(" ", 1)[1].strip().replace("refs/heads/", "")
                if branch == target or slugify_branch(branch) == slug:
                    return cur_path.resolve()
    except Exception:
        pass

    return candidate.resolve()


def repair_worktree_index(
    worktree_dir: Path,
    root: Path = ROOT,
    force_lock: bool = True,
) -> RepairResult:
    """
    Executes governed non-destructive index and working tree recovery.
    1. Safely recovers abandoned index.lock.
    2. Reaps orphaned fsmonitor daemons.
    3. Re-reads commit tree into index.
    4. Extracts any missing files to disk (checkout-index -a -f).
    5. Refreshes index stat cache.
    6. Verifies zero false unstaged deletions.
    """
    slug = worktree_dir.name
    if not worktree_dir.exists():
        return RepairResult(
            success=False,
            worktree_slug=slug,
            worktree_path=str(worktree_dir),
            lock_reclaimed=False,
            daemons_reaped=[],
            unstaged_deletions=0,
            message=f"Worktree directory does not exist: {worktree_dir}",
        )

    # 1. Stale lock reclamation
    lock_reclaimed = False
    try:
        lock_reclaimed = reclaim_stale_worktree_lock(slug, repo_root=root, force=force_lock)
    except Exception as e:
        logger.warning("Lock recovery warning for %s: %s", slug, e)

    # 2. Reap background daemons
    reaped = reap_worktree_fsmonitor(worktree_dir, root=root)

    # 3. Synchronize index to HEAD commit tree
    try:
        run_git(["read-tree", "HEAD"], cwd=worktree_dir, check=True)
    except Exception as e:
        return RepairResult(
            success=False,
            worktree_slug=slug,
            worktree_path=str(worktree_dir),
            lock_reclaimed=lock_reclaimed,
            daemons_reaped=reaped,
            unstaged_deletions=0,
            message=f"git read-tree HEAD failed: {e}",
        )

    # 4. Extract missing committed files without touching untracked or locally modified files
    try:
        missing_raw = run_git(["ls-files", "-d"], cwd=worktree_dir, check=True)
        if missing_raw.strip():
            subprocess.run(
                ["git", "checkout-index", "-u", "-q", "--stdin"],
                input=missing_raw,
                cwd=worktree_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=60.0,
            )
    except Exception as e:
        return RepairResult(
            success=False,
            worktree_slug=slug,
            worktree_path=str(worktree_dir),
            lock_reclaimed=lock_reclaimed,
            daemons_reaped=reaped,
            unstaged_deletions=0,
            message=f"git checkout-index failed: {e}",
        )

    # 5. Refresh index stats
    with contextlib.suppress(Exception):
        run_git(["update-index", "--refresh"], cwd=worktree_dir, check=False)

    # 6. Verify status
    status_raw = run_git(["status", "--porcelain"], cwd=worktree_dir, check=False)
    unstaged_deletions = sum(
        1 for line in status_raw.splitlines() if line.startswith(" D") or line.startswith("D ")
    )

    success = unstaged_deletions == 0
    msg = (
        "Worktree index and files successfully restored with zero unstaged deletions."
        if success
        else f"Warning: {unstaged_deletions} unstaged deletion(s) remain after repair."
    )

    return RepairResult(
        success=success,
        worktree_slug=slug,
        worktree_path=str(worktree_dir),
        lock_reclaimed=lock_reclaimed,
        daemons_reaped=reaped,
        unstaged_deletions=unstaged_deletions,
        message=msg,
    )


def cmd_repair_index(args: argparse.Namespace) -> int:
    """CLI handler for tools/pr_governance.py repair-index."""
    worktree_target = args.worktree
    force_lock = getattr(args, "force_lock", True)

    print(f"  PR GOVERNANCE: REPAIR WORKTREE INDEX ({worktree_target})")
    wt_dir = resolve_target_worktree(worktree_target, root=ROOT)
    print(f"  Target Worktree Path: {wt_dir}")

    result = repair_worktree_index(wt_dir, root=ROOT, force_lock=force_lock)
    if result.lock_reclaimed:
        print("  • Stale index lock: Reclaimed.")
    if result.daemons_reaped:
        print(f"  • Orphan daemons reaped: {len(result.daemons_reaped)} PID(s) ({result.daemons_reaped}).")

    if result.success:
        print(f"  ✅ {result.message}")
        return 0

    print(f"  ❌ {result.message}")
    return 1
