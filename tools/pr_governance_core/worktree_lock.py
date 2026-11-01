#!/usr/bin/env python3
"""Governed Worktree Lock Inspection and Recovery.

Provides non-destructive discovery, process liveness inspection, and safe
reclamation of stale git worktree index.lock files.
"""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, List, Optional

from .merge_lock import (
    LockContentionError,
    _is_pid_alive,
    resolve_git_common_dir,
)

logger = logging.getLogger("tools.pr_governance_core.worktree_lock")

DEFAULT_STALE_THRESHOLD_SEC = 60.0


@dataclass
class WorktreeLockInfo:
    """Metadata describing a worktree index.lock file and holder status."""

    worktree_slug: str
    lock_file_path: str
    holding_pid: Optional[int]
    is_holding_pid_alive: bool
    age_seconds: float
    is_stale: bool
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_file_holder_pid(file_path: Path) -> Optional[int]:
    """Queries operating system to find PID holding open descriptor on file."""
    if not file_path.exists():
        return None
    try:
        res = subprocess.run(
            ["lsof", "-t", str(file_path.resolve())],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.strip().splitlines():
                pid_str = line.strip()
                if pid_str.isdigit():
                    return int(pid_str)
    except Exception:
        pass
    return None


def inspect_worktree_index_locks(
    repo_root: Optional[Path] = None,
    stale_threshold_sec: float = DEFAULT_STALE_THRESHOLD_SEC,
) -> List[WorktreeLockInfo]:
    """Discovers and inspects all active and stale index.lock files in worktrees."""
    common_dir = resolve_git_common_dir(repo_root)
    worktrees_meta_dir = common_dir / "worktrees"
    results: List[WorktreeLockInfo] = []
    now = time.time()

    candidates: List[tuple[str, Path]] = []
    if worktrees_meta_dir.is_dir():
        for wt_meta in sorted(worktrees_meta_dir.iterdir()):
            if wt_meta.is_dir():
                lock_p = wt_meta / "index.lock"
                if lock_p.exists():
                    candidates.append((wt_meta.name, lock_p))

    main_lock = common_dir / "index.lock"
    if main_lock.exists():
        candidates.append(("main", main_lock))

    for slug, lock_p in candidates:
        try:
            stat_res = lock_p.stat()
            age = max(0.0, now - stat_res.st_mtime)
            size = stat_res.st_size
            pid = get_file_holder_pid(lock_p)
            pid_alive = _is_pid_alive(pid) if pid is not None else False

            # Lock is stale if holder process is dead, or if no process holds it and age exceeds threshold
            is_stale = False
            if pid is not None:
                is_stale = not pid_alive
            else:
                is_stale = age >= stale_threshold_sec

            results.append(
                WorktreeLockInfo(
                    worktree_slug=slug,
                    lock_file_path=str(lock_p.resolve()),
                    holding_pid=pid,
                    is_holding_pid_alive=pid_alive,
                    age_seconds=round(age, 2),
                    is_stale=is_stale,
                    size_bytes=size,
                )
            )
        except Exception as err:
            logger.warning("Error inspecting lock file %s: %s", lock_p, err)

    return results


def reclaim_stale_worktree_lock(
    worktree_slug: str,
    repo_root: Optional[Path] = None,
    force: bool = False,
    stale_threshold_sec: float = DEFAULT_STALE_THRESHOLD_SEC,
) -> bool:
    """Safely removes an abandoned worktree index.lock file if holding process is dead."""
    common_dir = resolve_git_common_dir(repo_root)
    if worktree_slug == "main":
        lock_file = common_dir / "index.lock"
    else:
        lock_file = common_dir / "worktrees" / worktree_slug / "index.lock"

    if not lock_file.exists():
        return True

    holder_pid = get_file_holder_pid(lock_file)
    pid_alive = _is_pid_alive(holder_pid) if holder_pid is not None else False

    if pid_alive and not force:
        raise LockContentionError(
            f"Cannot reclaim lock for '{worktree_slug}': held by active PID {holder_pid}"
        )

    stat_res = lock_file.stat()
    age = max(0.0, time.time() - stat_res.st_mtime)
    if holder_pid is None and age < stale_threshold_sec and not force:
        raise LockContentionError(
            f"Lock for '{worktree_slug}' has no identified holder and was recently modified ({age:.1f}s ago). "
            f"Wait {stale_threshold_sec - age:.1f}s or use force=True to break."
        )

    with contextlib.suppress(Exception):
        lock_file.unlink()
        logger.info("Successfully reclaimed stale worktree lock for '%s'.", worktree_slug)
        return True

    return not lock_file.exists()
