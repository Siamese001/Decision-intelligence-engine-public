"""
Worktree Garbage Collector for PR governance.

Detects and safely removes worktrees whose branches have already merged into main.
Flags stale or idle unmerged worktrees exceeding TTL.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .common import ROOT
from .merge_proof import BranchMergeState, classify_branch_merge_state
from .worktree import (
    count_branch_stashes,
    find_valuable_ignored_files,
    is_mid_rebase,
    parse_porcelain_v2_z,
    safely_unlink_worktree_dependencies,
)


def run_git(cmd: List[str], cwd: Path = ROOT) -> str:
    full_cmd = cmd if cmd and cmd[0] == "git" else ["git"] + cmd
    res = subprocess.run(full_cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def inspect_worktrees_for_gc(root: Path = ROOT) -> List[Dict[str, Any]]:
    raw = run_git(["worktree", "list", "--porcelain"], cwd=root)
    worktrees: List[Dict[str, Any]] = []

    cur_entry: Dict[str, Any] = {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if cur_entry:
                worktrees.append(cur_entry)
            cur_entry = {"path": line.split(" ", 1)[1].strip()}
        elif line.startswith("HEAD "):
            cur_entry["head"] = line.split(" ", 1)[1].strip()
        elif line.startswith("branch refs/heads/"):
            cur_entry["branch"] = line.split(" ", 1)[1].strip().replace("refs/heads/", "")

    if cur_entry:
        worktrees.append(cur_entry)

    main_head = run_git(["rev-parse", "main"], cwd=root)

    candidates = []
    for wt in worktrees:
        path = Path(wt["path"])
        # Skip root repo itself
        if path.resolve() == root.resolve():
            continue

        head = wt.get("head", "")
        branch = wt.get("branch", "")
        if not head:
            continue

        # Invariant I12: Registered worktree with missing directory is UNKNOWN, never prunable
        if not path.exists():
            candidates.append(
                {
                    "path": str(path),
                    "branch": branch,
                    "head": head,
                    "is_merged": False,
                    "is_dirty": False,
                    "status": "UNKNOWN (missing directory)",
                    "can_auto_clean": False,
                }
            )
            continue

        # Protected branches: never prune
        if branch in ("main", "master") or branch.startswith("backup/") or branch.startswith("archive/"):
            candidates.append(
                {
                    "path": str(path),
                    "branch": branch,
                    "head": head,
                    "is_merged": False,
                    "is_dirty": False,
                    "status": "PROTECTED",
                    "can_auto_clean": False,
                }
            )
            continue

        # Invariant I7: Mid-rebase or stopped operations are UNKNOWN and never prunable
        if is_mid_rebase(path):
            candidates.append(
                {
                    "path": str(path),
                    "branch": branch,
                    "head": head,
                    "is_merged": False,
                    "is_dirty": True,
                    "status": "UNKNOWN (mid-rebase)",
                    "can_auto_clean": False,
                }
            )
            continue

        # Check merge status against main
        merge_state = classify_branch_merge_state(branch, head, main_head, root=root)
        is_merged = (merge_state == BranchMergeState.GENUINELY_MERGED)
        is_zero_commit = (merge_state == BranchMergeState.ZERO_COMMIT_ANCESTOR)

        # Invariant I2, I3, I8: Check dirty state, valuable ignored files, and stashes
        valuable_ignored = find_valuable_ignored_files(path)
        stash_count = count_branch_stashes(branch, root)

        status_raw = subprocess.run(
            ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=path,
            capture_output=True,
            check=False,
        )
        uncommitted_entries = parse_porcelain_v2_z(status_raw.stdout) if status_raw.returncode == 0 else []

        is_dirty = bool(uncommitted_entries) or bool(valuable_ignored) or (stash_count > 0)

        is_abandoned_live = False
        if (
            branch.startswith(("audio/", "feat/live-", "transcript/", "ingest/meeting-"))
            and is_zero_commit
            and not is_dirty
        ):
            from tools.audit_worktree_hygiene_core.pruner import _is_abandoned_live_worktree
            is_abandoned_live = _is_abandoned_live_worktree(path, branch, root, main_head, max_age_hours=24.0)

        is_abandoned_zero_commit = False
        if is_zero_commit and not is_dirty:
            from tools.pr_topology_core.birth import inspect_branch_birth
            birth = inspect_branch_birth(branch, root=root, worktree_path=path)
            if not birth.is_inception_active or path.parent.resolve() != (root / ".worktrees").resolve():
                from tools.audit_worktree_hygiene_core.pruner import _is_abandoned_live_worktree
                is_abandoned_zero_commit = _is_abandoned_live_worktree(path, branch, root, main_head, max_age_hours=1.0)

        can_auto_clean = (is_merged and not is_dirty and not is_zero_commit) or is_abandoned_live or is_abandoned_zero_commit

        candidates.append(
            {
                "path": str(path),
                "branch": branch,
                "head": head,
                "is_merged": is_merged,
                "is_dirty": is_dirty,
                "is_zero_commit": is_zero_commit,
                "is_abandoned_live": is_abandoned_live,
                "valuable_ignored_count": len(valuable_ignored),
                "stash_count": stash_count,
                "can_auto_clean": can_auto_clean,
            }
        )

    return candidates


def prune_orphan_receipts(root: Path = ROOT, dry_run: bool = True) -> List[str]:
    """Scans reports/ for untracked ephemeral review/promotion receipts and prunes them."""
    reports_dir = root / "reports"
    readiness_dir = reports_dir / "readiness"
    pruned: List[str] = []

    candidate_files = list(reports_dir.glob("pr_review_*.json")) + list(
        reports_dir.glob("pr_promotion_*.json")
    )
    if readiness_dir.is_dir():
        candidate_files.extend(readiness_dir.glob("local_main_readiness_*.json"))

    for f in candidate_files:
        try:
            rel = str(f.relative_to(root))
        except ValueError:
            continue
        chk = run_git(["ls-files", "--error-unmatch", rel], cwd=root)
        if chk:
            continue
        if not dry_run:
            try:
                f.unlink(missing_ok=True)
                pruned.append(f.name)
            except Exception:
                pass
        else:
            pruned.append(f.name)
    return pruned


def cmd_gc(args: argparse.Namespace) -> int:
    execute = getattr(args, "execute", False)
    dry_run = getattr(args, "dry_run", not execute)
    if execute:
        dry_run = False

    print("=" * 80)
    print(f"  PR GOVERNANCE: WORKTREE GARBAGE COLLECTOR {'(DRY RUN - use --execute to prune)' if dry_run else '(EXECUTING)'}")
    print("=" * 80)

    candidates = inspect_worktrees_for_gc(ROOT)
    merged_clean = [c for c in candidates if c["can_auto_clean"]]
    stale_dirty = [c for c in candidates if c["is_dirty"]]

    print(f"  Total Non-Root Worktrees Scanned: {len(candidates)}")
    print(f"  Merged & Clean for Pruning:      {len(merged_clean)}")
    print(f"  Active Dirty / Protected:        {len(stale_dirty)}")

    orphan_receipts = prune_orphan_receipts(ROOT, dry_run=dry_run)
    if orphan_receipts:
        print(f"  Orphan Untracked Receipts ({'Would Prune' if dry_run else 'Pruned'}): {len(orphan_receipts)}")
        for r_name in orphan_receipts[:5]:
            print(f"    • {r_name}")
        if len(orphan_receipts) > 5:
            print(f"    ... and {len(orphan_receipts) - 5} more")
    print("")

    if not merged_clean and not orphan_receipts:
        print("  No dangling merged worktrees or orphan receipts detected. Workspace is clean.")
        return 0

    for c in merged_clean:
        print(f"• [PRUNABLE] Branch: {c['branch']:<35} Head: {c['head'][:8]}")
        print(f"  Path: {c['path']}")
        if not dry_run:
            wt_path = Path(c["path"])
            if wt_path.exists():
                print("  Reaping worktree daemons...")
                from .worktree_daemon_reaper import reap_worktree_fsmonitor
                reap_worktree_fsmonitor(wt_path, root=ROOT)
                print("  Safely unlinking dependencies...")
                safely_unlink_worktree_dependencies(wt_path)
                print("  Removing worktree via git...")
                res = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)], cwd=ROOT, capture_output=True, text=True, check=False, timeout=30
                )
                if res.returncode == 0:
                    subprocess.run(
                        ["git", "branch", "-D", c["branch"]], cwd=ROOT, capture_output=True, text=True, check=False, timeout=30
                    )
                    print("  ✅ Pruned successfully.")
                else:
                    print(f"  ❌ git worktree remove failed: {res.stderr.strip()}")
        else:
            print("  [DRY-RUN] Would safely unlink and prune worktree.")

    print("=" * 80)
    return 0
