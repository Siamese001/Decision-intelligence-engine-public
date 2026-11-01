#!/usr/bin/env python3
"""tools/audit_worktree_stale_locks.py

High-Speed Governance Linter & Sweeper for Worktree Locks and Sockets.
Audits .git/worktrees/ for abandoned index.lock files, detached IPC sockets,
and stale provisioning mutexes.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.common import ROOT as GOVERNANCE_ROOT
from tools.pr_governance_core.merge_lock import resolve_git_common_dir
from tools.pr_governance_core.worktree_daemon_reaper import (
    reap_worktree_fsmonitor,
)
from tools.pr_governance_core.worktree_lock import (
    DEFAULT_STALE_THRESHOLD_SEC,
    inspect_worktree_index_locks,
    reclaim_stale_worktree_lock,
)

logger = logging.getLogger("tools.audit_worktree_stale_locks")


def is_ipc_socket_orphaned(sock_path: Path, wt_entry: Path) -> bool:
    """Returns True if an IPC socket is detached, holds no running daemon, or worktree was deleted."""
    if not sock_path.exists():
        return False
    gitdir_file = wt_entry / "gitdir"
    if not gitdir_file.is_file():
        return True
    try:
        content = gitdir_file.read_text(encoding="utf-8").strip()
        if not content:
            return True
        git_target = Path(content)
        wt_path = git_target.parent if git_target.name == ".git" else git_target
        if not wt_path.is_dir():
            return True
        res = subprocess.run(
            ["git", "-C", str(wt_path), "fsmonitor--daemon", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        return res.returncode != 0
    except Exception:
        return True


def audit_worktree_stale_locks(
    repo_root: Optional[Path] = None,
    stale_age_seconds: float = 600.0,
) -> Dict[str, Any]:
    """Inspects repository worktrees for stale locks and orphaned daemon sockets."""
    target_root = repo_root or GOVERNANCE_ROOT
    common_dir = resolve_git_common_dir(target_root)
    worktrees_gitdir = common_dir / "worktrees"
    locks = inspect_worktree_index_locks(repo_root=target_root, stale_threshold_sec=stale_age_seconds)

    stale_locks: List[Dict[str, Any]] = []
    active_locks: List[Dict[str, Any]] = []

    for lk in locks:
        info = lk.to_dict()
        if lk.is_stale:
            stale_locks.append(info)
        else:
            active_locks.append(info)

    # Check for orphaned IPC sockets in worktree gitdirs
    orphaned_sockets: List[str] = []
    if worktrees_gitdir.is_dir():
        for wt_entry in worktrees_gitdir.iterdir():
            if wt_entry.is_dir():
                for sock in wt_entry.glob("*.ipc"):
                    if is_ipc_socket_orphaned(sock, wt_entry):
                        orphaned_sockets.append(str(sock.resolve()))

    # Check provisioning flock
    prov_lock = target_root / ".worktrees" / ".provisioning.lock"
    prov_stale = False
    if prov_lock.exists():
        try:
            mtime = prov_lock.stat().st_mtime
            import time

            age = time.time() - mtime
            if age > stale_age_seconds:
                prov_stale = True
        except OSError:
            pass

    is_clean = len(stale_locks) == 0 and len(orphaned_sockets) == 0 and not prov_stale

    return {
        "is_clean": is_clean,
        "stale_locks": stale_locks,
        "active_locks": active_locks,
        "orphaned_sockets": orphaned_sockets,
        "stale_provisioning_lock": prov_stale,
        "stale_threshold_seconds": stale_age_seconds,
        "worktrees_gitdir": str(worktrees_gitdir),
    }


def format_audit_report(result: Dict[str, Any]) -> str:
    """Formats human-readable governance audit report."""
    lines: List[str] = [
        "=" * 80,
        "  WORKTREE STALE LOCK & DAEMON SOCKET AUDIT",
        "=" * 80,
    ]

    stale_locks = result["stale_locks"]
    orphaned_sockets = result["orphaned_sockets"]
    prov_stale = result["stale_provisioning_lock"]

    if result["is_clean"]:
        lines.append("✅ PASS: No stale locks or orphaned IPC sockets detected.")
        lines.append(f"   Checked worktree directory: {result['worktrees_gitdir']}")
        lines.append("=" * 80)
        return "\n".join(lines)

    lines.append("⛔ DEFECTS DETECTED: Stale lock files or orphaned sockets found:")
    for lk in stale_locks:
        slug = lk["worktree_slug"]
        pid = lk["holding_pid"]
        alive = lk["is_holding_pid_alive"]
        age = round(lk["age_seconds"], 1)
        lines.append(f"  • Stale Lock: {lk['lock_file_path']}")
        lines.append(f"    Worktree: {slug} | Age: {age}s | Holding PID: {pid} (Alive: {alive})")
        lines.append(f"    Remediation: python tools/pr_governance.py repair-index {slug}")

    for sock in orphaned_sockets:
        lines.append(f"  • Orphaned Socket: {sock}")

    if prov_stale:
        lines.append("  • Stale Provisioning Mutex: .worktrees/.provisioning.lock")

    lines.append("=" * 80)
    return "\n".join(lines)


def auto_fix_defects(result: Dict[str, Any], repo_root: Optional[Path] = None) -> int:
    """Safely recovers detected stale locks and unlinks orphaned sockets."""
    target_root = repo_root or GOVERNANCE_ROOT
    fixed = 0
    for lk in result["stale_locks"]:
        slug = lk["worktree_slug"]
        print(f"  [FIX] Reclaiming stale lock for worktree: {slug}")
        if reclaim_stale_worktree_lock(slug, repo_root=target_root, force=True):
            fixed += 1
            print(f"  ✅ Reclaimed lock for {slug}")
        wt_dir = target_root / ".worktrees" / slug
        if wt_dir.exists():
            reap_worktree_fsmonitor(wt_dir, root=target_root)

    for sock_str in result["orphaned_sockets"]:
        sock_path = Path(sock_str)
        if sock_path.exists():
            sock_path.unlink(missing_ok=True)
            fixed += 1
            print(f"  ✅ Unlinked orphaned socket: {sock_path.name}")

    if result.get("stale_provisioning_lock"):
        prov_lock = target_root / ".worktrees" / ".provisioning.lock"
        if prov_lock.exists():
            prov_lock.unlink(missing_ok=True)
            fixed += 1
            print("  ✅ Removed stale provisioning lock")

    return fixed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit .git/worktrees for stale locks and orphaned sockets.")
    parser.add_argument("--check", action="store_true", help="Fail closed (exit 1) if stale locks detected.")
    parser.add_argument("--json", action="store_true", help="Output audit results as JSON.")
    parser.add_argument("--fix", action="store_true", help="Automatically reclaim stale locks and purge orphaned sockets.")
    parser.add_argument(
        "--stale-threshold",
        type=float,
        default=600.0,
        help="Age in seconds to classify an abandoned lock as stale (default: 600s / 10m).",
    )
    parser.add_argument("--repo-root", type=str, default="", help="Optional repository root directory.")

    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve() if args.repo_root else GOVERNANCE_ROOT

    audit_res = audit_worktree_stale_locks(repo_root=root, stale_age_seconds=args.stale_threshold)

    if args.fix:
        auto_fix_defects(audit_res, repo_root=root)
        # Re-audit after fix
        audit_res = audit_worktree_stale_locks(repo_root=root, stale_age_seconds=args.stale_threshold)

    if args.json:
        print(json.dumps(audit_res, indent=2))
    else:
        print(format_audit_report(audit_res))

    if args.check and not audit_res["is_clean"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
