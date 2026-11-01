#!/usr/bin/env python3
"""
Continuous Rebase Daemon CLI.

Monitors active feature branches and pre-checks their rebaseability against main HEAD,
keeping active branches perpetually at behind: 0 and detecting conflicts early.

Usage:
  python tools/continuous_rebase_daemon.py [--execute] [--json reports/rebase_daemon_report.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent


def run_git(cmd: List[str], cwd: Path = ROOT) -> str:
    full_cmd = cmd if cmd and cmd[0] == "git" else ["git"] + cmd
    res = subprocess.run(full_cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def check_branch_rebase_health(branch: str, worktree_path: Path | None) -> Dict[str, Any]:
    # Check rev counts using first-parent mainline distance
    base_res = run_git(["merge-base", "main", branch])
    behind, ahead = 0, 0
    if base_res:
        behind_s = run_git(["rev-list", "--first-parent", "--count", f"{base_res}..main"])
        ahead_s = run_git(["rev-list", "--count", f"{base_res}..{branch}"])
        if behind_s and ahead_s:
            try:
                behind, ahead = int(behind_s), int(ahead_s)
            except ValueError:
                pass

    # Check mergeability via merge-tree
    res = subprocess.run(
        ["git", "merge-tree", "--write-tree", "main", branch],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    is_clean = res.returncode == 0

    # Check worktree dirty state if available
    is_dirty = False
    if worktree_path and worktree_path.exists():
        status_out = run_git(["status", "--porcelain"], cwd=worktree_path)
        is_dirty = bool(status_out)

    return {
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "is_clean_merge": is_clean,
        "is_dirty": is_dirty,
        "can_auto_rebase": is_clean and not is_dirty and behind > 0,
        "worktree_path": str(worktree_path) if worktree_path else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuous background rebase sentinel.")
    parser.add_argument(
        "--execute", action="store_true", help="Perform git rebase main in clean worktrees"
    )
    parser.add_argument("--json", dest="json_path", default=None, help="Output JSON report path")
    args = parser.parse_args()

    # Get feature branches
    branches_raw = run_git(["branch", "--format=%(refname:short)"])
    branches = [
        b
        for b in branches_raw.splitlines()
        if b != "main" and not b.startswith("archive/") and not b.startswith("HEAD")
    ]

    # Map worktrees
    worktrees_raw = run_git(["worktree", "list", "--porcelain"])
    wt_map: Dict[str, Path] = {}
    cur_wt = None
    for line in worktrees_raw.splitlines():
        if line.startswith("worktree "):
            cur_wt = Path(line.split(" ", 1)[1].strip())
        elif line.startswith("branch refs/heads/") and cur_wt:
            bname = line.split(" ", 1)[1].strip().replace("refs/heads/", "")
            wt_map[bname] = cur_wt

    results = []
    print("=" * 80)
    print("  CONTINUOUS REBASE SENTINEL & HEALTH REPORT")
    print("=" * 80)

    for b in sorted(branches):
        health = check_branch_rebase_health(b, wt_map.get(b))
        results.append(health)

        status_icon = (
            "✅ UP-TO-DATE"
            if health["behind"] == 0
            else (
                "🔄 CAN REBASE"
                if health["can_auto_rebase"]
                else ("⚠️ DIRTY (SKIP)" if health["is_dirty"] else "❌ CONFLICT")
            )
        )
        print(
            f"• [{status_icon:<14}] {b:<40} (Ahead: {health['ahead']}, Behind: {health['behind']})"
        )

        if args.execute and health["can_auto_rebase"] and health["worktree_path"]:
            wt_path = Path(health["worktree_path"])
            print(f"  Executing 'git rebase main' in {wt_path}...")
            reb_res = subprocess.run(
                ["git", "rebase", "main"], cwd=wt_path, capture_output=True, text=True, check=False
            )
            if reb_res.returncode == 0:
                print("  ✅ Rebased cleanly to main HEAD.")
            else:
                print(f"  ❌ Rebase failed: {reb_res.stderr}")
                subprocess.run(["git", "rebase", "--abort"], cwd=wt_path, check=False)

    print("=" * 80)

    if args.json_path:
        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "branches_evaluated": len(results),
            "results": results,
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Report written to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
