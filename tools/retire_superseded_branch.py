#!/usr/bin/env python3
"""
Retire & Tombstone Superseded Feature Branch.

Safely archives a dead/superseded branch tip to an immutable git tag (refs/tags/archive/*),
safely unlinks worktree dependencies, deletes the worktree, and removes the local branch.

Usage:
  python tools/retire_superseded_branch.py <branch-name> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.worktree import (
    count_branch_stashes,
    find_valuable_ignored_files,
    is_mid_rebase,
    parse_porcelain_v2_z,
    safely_unlink_worktree_dependencies,
)

PROTECTED_BRANCHES = frozenset({"main", "master", "HEAD"})


def run_git(cmd: list[str], cwd: Path = ROOT, check: bool = True) -> str:
    full_cmd = cmd if cmd and cmd[0] == "git" else ["git"] + cmd
    res = subprocess.run(full_cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if check and res.returncode != 0:
        raise RuntimeError(f"Git command failed: {' '.join(full_cmd)}\nStderr: {res.stderr}")
    return res.stdout.strip()


def retire_branch(branch: str, root: Path = ROOT) -> Dict[str, Any]:
    if not branch or not branch.strip():
        raise ValueError("Branch name cannot be empty.")

    # 1. Invariant I5, T28: Refuse protected refs
    if branch in PROTECTED_BRANCHES or branch.startswith("backup/") or branch.startswith("archive/"):
        raise ValueError(f"Branch '{branch}' is protected and cannot be retired.")

    # 2. Verify branch exists and record exact tip SHA
    try:
        branch_sha = run_git(["rev-parse", f"refs/heads/{branch}"], cwd=root, check=True)
    except Exception:
        raise ValueError(f"Branch '{branch}' does not exist.") from None

    # 3. Locate associated worktree if any
    worktrees_raw = run_git(["worktree", "list", "--porcelain"], cwd=root, check=True)
    worktree_path: Path | None = None
    lines = worktrees_raw.splitlines()
    cur_wt = None
    for line in lines:
        if line.startswith("worktree "):
            cur_wt = line.split(" ", 1)[1].strip()
        elif (
            line.startswith("branch refs/heads/")
            and line.split(" ", 1)[1].strip() == f"refs/heads/{branch}"
            and cur_wt
        ):
            worktree_path = Path(cur_wt)

    # 4. Invariant I6, T25: Pre-execution inventory guards (dirty, stashes, rebase)
    if worktree_path and worktree_path.exists():
        if is_mid_rebase(worktree_path):
            raise RuntimeError(f"Worktree '{worktree_path}' has an active rebase/merge operation in progress.")

        # Check for uncommitted dirty files
        status_proc = subprocess.run(
            ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=worktree_path,
            capture_output=True,
            check=False,
        )
        if status_proc.returncode == 0 and status_proc.stdout:
            entries = parse_porcelain_v2_z(status_proc.stdout)
            if entries:
                raise RuntimeError(
                    f"Worktree '{worktree_path}' has {len(entries)} uncommitted file(s); commit or stash before retiring."
                )

        # Check for valuable ignored files
        valuable_ignored = find_valuable_ignored_files(worktree_path)
        if valuable_ignored:
            raise RuntimeError(
                f"Worktree '{worktree_path}' contains {len(valuable_ignored)} valuable ignored file(s): "
                f"{', '.join(valuable_ignored[:3])}; resolve before retiring."
            )

    # Check for stashes
    stash_count = count_branch_stashes(branch, root)
    if stash_count > 0:
        raise RuntimeError(f"Branch '{branch}' has {stash_count} active stash entry/entries; drop or apply before retiring.")

    # 5. Invariant I4, T26: Unique archive ref
    archive_ref = f"refs/archive/{branch}/{branch_sha}"

    receipt = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "branch": branch,
        "branch_sha": branch_sha,
        "archive_ref": archive_ref,
        "archive_tag": archive_ref,
        "worktree_path": str(worktree_path) if worktree_path else None,
        "status": "RETIRED",
    }

    # Step A: Create immutable archive ref with CAS check (fails if already exists)
    print(f"Creating immutable archive ref: {archive_ref} -> {branch_sha[:8]}...")
    run_git(["update-ref", archive_ref, branch_sha, ""], cwd=root, check=True)

    # Step B: Teardown worktree without rmtree fallback (Invariant I10)
    if worktree_path and worktree_path.exists():
        print(f"Safely unlinking worktree dependencies at: {worktree_path}...")
        safely_unlink_worktree_dependencies(worktree_path)
        print(f"Removing worktree: {worktree_path}...")
        run_git(["worktree", "remove", str(worktree_path)], cwd=root, check=True)

    # Step C: Invariant I5, T27: Compare-and-swap delete of branch ref
    print(f"Deleting branch ref (CAS verified): {branch}...")
    run_git(["update-ref", "-d", f"refs/heads/{branch}", branch_sha], cwd=root, check=True)
    print(f"✅ Branch '{branch}' safely retired to archive ref '{archive_ref}'.")

    # Write receipt
    receipt_path = root / "reports" / "branch_tombstone_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely retire a superseded feature branch.")
    parser.add_argument("branch", help="Feature branch to retire")
    args = parser.parse_args()

    try:
        retire_branch(args.branch)
        return 0
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
