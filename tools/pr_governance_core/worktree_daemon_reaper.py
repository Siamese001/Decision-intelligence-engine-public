#!/usr/bin/env python3
"""Governed Worktree Daemon & IPC Socket Reaper.

Provides robust discovery, graceful shutdown, and process termination for orphaned
git fsmonitor--daemon processes and lingering Unix domain sockets across worktrees.
"""
from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Set

from .common import ROOT, get_immune_pids, run_git, slugify_branch
from .merge_lock import _is_pid_alive, resolve_git_common_dir

logger = logging.getLogger("tools.pr_governance_core.worktree_daemon_reaper")


def _resolve_worktree_gitdir(worktree_dir: Path, root: Path) -> Optional[Path]:
    """Finds the worktree metadata directory under .git/worktrees/."""
    common_dir = resolve_git_common_dir(root)
    worktrees_meta = common_dir / "worktrees"
    if not worktrees_meta.is_dir():
        return None

    wt_resolved = str(worktree_dir.resolve())
    for wt_meta in worktrees_meta.iterdir():
        if wt_meta.is_dir():
            gitdir_file = wt_meta / "gitdir"
            if gitdir_file.is_file():
                try:
                    content = gitdir_file.read_text(encoding="utf-8").strip()
                    if content and (content == wt_resolved or Path(content).resolve() == Path(wt_resolved)):
                        return wt_meta
                except Exception:
                    pass
            if wt_meta.name == worktree_dir.name or wt_meta.name == slugify_branch(worktree_dir.name):
                return wt_meta

    return None


def stop_worktree_fsmonitor(worktree_dir: Path) -> bool:
    """Attempts graceful shutdown of fsmonitor daemon via git CLI command."""
    if not worktree_dir.exists():
        return False
    try:
        res = subprocess.run(
            ["git", "-C", str(worktree_dir), "fsmonitor--daemon", "stop"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        return res.returncode == 0
    except Exception as e:
        logger.debug("Graceful fsmonitor stop on %s failed: %s", worktree_dir, e)
        return False


def find_fsmonitor_pids(worktree_dir: Path, root: Path = ROOT) -> List[int]:
    """Discovers all daemon process IDs attached to worktree directory or its gitdir."""
    immune = get_immune_pids()
    target_pids: Set[int] = set()

    # Search descriptors held in worktree_dir
    paths_to_audit = [worktree_dir]
    gitdir = _resolve_worktree_gitdir(worktree_dir, root)
    if gitdir and gitdir.exists():
        paths_to_audit.append(gitdir)

    for p in paths_to_audit:
        if not p.exists():
            continue
        try:
            proc = subprocess.run(
                ["lsof", "-t", "+D", str(p.resolve())],
                capture_output=True,
                text=True,
                check=False,
                timeout=3.0,
            )
            if proc.stdout.strip():
                for line in proc.stdout.strip().splitlines():
                    pid_str = line.strip()
                    if pid_str.isdigit():
                        pid = int(pid_str)
                        if pid > 0 and pid not in immune:
                            target_pids.add(pid)
        except Exception:
            pass

    return sorted(target_pids)


def unlink_lingering_sockets(worktree_dir: Path, root: Path = ROOT) -> List[str]:
    """Unlinks abandoned .ipc socket files and daemon state in gitdir."""
    unlinked: List[str] = []
    gitdir = _resolve_worktree_gitdir(worktree_dir, root)
    if not gitdir or not gitdir.exists():
        return unlinked

    ipc_socket = gitdir / "fsmonitor--daemon.ipc"
    if ipc_socket.exists():
        with contextlib.suppress(Exception):
            ipc_socket.unlink()
            unlinked.append(ipc_socket.name)

    cookie_dir = gitdir / "fsmonitor--daemon"
    if cookie_dir.is_dir():
        with contextlib.suppress(Exception):
            for item in cookie_dir.iterdir():
                if item.is_file():
                    item.unlink()
            cookie_dir.rmdir()
            unlinked.append(cookie_dir.name)

    return unlinked


def reap_worktree_fsmonitor(
    worktree_dir: Path,
    root: Path = ROOT,
    timeout_sec: float = 2.0,
) -> List[int]:
    """
    Terminates orphaned fsmonitor daemons and unlinks IPC sockets for a worktree.
    Returns list of reaped process IDs.
    """
    stop_worktree_fsmonitor(worktree_dir)

    pids = find_fsmonitor_pids(worktree_dir, root=root)
    reaped: List[int] = []

    for pid in pids:
        if not _is_pid_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("Sent SIGTERM to worktree daemon PID %d.", pid)
        except OSError:
            continue

        # Wait briefly for process to exit
        start_t = time.monotonic()
        while time.monotonic() - start_t < timeout_sec:
            if not _is_pid_alive(pid):
                reaped.append(pid)
                break
            time.sleep(0.05)
        else:
            # Force kill if still alive
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
                logger.warning("Forcibly killed worktree daemon PID %d with SIGKILL.", pid)
            reaped.append(pid)

    unlink_lingering_sockets(worktree_dir, root=root)
    return reaped
