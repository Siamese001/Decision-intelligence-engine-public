"""
Branch merge proof and state verification engine for PR governance.

Distinguishes genuinely merged branches from unmerged zero-commit ancestor
branches sitting at historical first-parent main commits.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import List, Optional

from .common import ROOT, run_git, slugify_branch


class BranchMergeState(str, Enum):
    GENUINELY_MERGED = "GENUINELY_MERGED"
    SUPERSEDED_BY_MAIN = "SUPERSEDED_BY_MAIN"
    ZERO_COMMIT_ANCESTOR = "ZERO_COMMIT_ANCESTOR"
    DIVERGED_UNMERGED = "DIVERGED_UNMERGED"


def is_second_parent_of_merge_on_main(
    head_sha: str,
    branch_name: Optional[str] = None,
    root: Path = ROOT,
    main_ref: str = "main",
) -> bool:
    """Checks whether head_sha is the second parent (P2) of a merge commit on main."""
    if not head_sha:
        return False
    # Check if head_sha is P2 of any merge commit on main
    out = run_git(["log", main_ref, "--merges", "--format=%H %P"], cwd=root, check=False)
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[2] == head_sha:
            return True

    # If branch_name is provided, also check if merge commit message mentions the branch
    if branch_name:
        grep_arg = f"--grep=^Merge branch '{branch_name}'"
        merge_commits = run_git(
            ["log", main_ref, "--merges", grep_arg, "--format=%H %P"],
            cwd=root,
            check=False,
        )
        for line in merge_commits.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3:
                # Check if head_sha is ancestor of or equal to P2
                p2 = parts[2]
                if p2 == head_sha:
                    return True
                res = run_git(["merge-base", "--is-ancestor", head_sha, p2], cwd=root, check=False)
                # git merge-base returns exit code 0 if is-ancestor
                # run_git returns non-empty or empty string on check=False depending on implementation
                # Let's verify with subprocess via check_ancestor
                if _is_ancestor_commit(head_sha, p2, root):
                    return True
    return False


def _is_ancestor_commit(ancestor: str, descendant: str, root: Path = ROOT) -> bool:
    import subprocess
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def has_sealed_merge_receipt(branch_name: str, head_sha: Optional[str] = None, root: Path = ROOT) -> bool:
    """
    Checks whether a valid sealed pr_merge_<slug>_receipt.json exists and,
    if head_sha is provided, verifies that head_sha matches the receipt's recorded
    branch_head_sha (or is an ancestor thereof). Prevents a newly recreated branch
    with the same name from falsely inheriting an old merge receipt!
    """
    if not branch_name:
        return False
    import json
    slug = slugify_branch(branch_name)
    receipt_file = root / "reports" / f"pr_merge_{slug}_receipt.json"
    if not receipt_file.is_file():
        return False
    if not head_sha:
        return True
    try:
        with open(receipt_file, "r") as f:
            data = json.load(f)
        recorded_head = data.get("branch_head_sha") or data.get("head_sha", "")
        if not recorded_head:
            return True
        if recorded_head == head_sha:
            return True
        # If head_sha is an ancestor of recorded_head, it was merged as part of that PR
        return _is_ancestor_commit(head_sha, recorded_head, root=root)
    except Exception:
        return False


def has_fast_forward_merge_in_reflog(
    branch_name: str, head_sha: str, root: Path = ROOT, main_ref: str = "main"
) -> bool:
    """Checks whether branch_name was fast-forward merged into main_ref per git reflog, or has real commits in its own reflog now on main."""
    if not branch_name or not head_sha:
        return False
    if not _is_ancestor_commit(head_sha, main_ref, root=root):
        return False
    main_reflog = run_git(["reflog", "show", f"refs/heads/{main_ref}"], cwd=root, check=False)
    for line in main_reflog.splitlines():
        if f"merge {branch_name}: Fast-forward" in line or f"merge refs/heads/{branch_name}: Fast-forward" in line:
            return True
    br_reflog = run_git(["reflog", "show", f"refs/heads/{branch_name}"], cwd=root, check=False)
    for line in br_reflog.splitlines():
        if ": commit:" in line or ": commit (amend):" in line:
            commit_token = line.strip().split()[0] if line.strip() else ""
            if commit_token and _is_ancestor_commit(commit_token, main_ref, root=root):
                return True
    return False


def classify_branch_merge_state(
    branch: str,
    head_sha: str,
    main_head: str,
    root: Path = ROOT,
    main_ref: str = "main",
) -> BranchMergeState:
    """
    Classifies a branch into GENUINELY_MERGED, ZERO_COMMIT_ANCESTOR, or DIVERGED_UNMERGED.

    Key Invariant:
    A branch is only GENUINELY_MERGED if:
    1. It has a verified sealed pr_merge_<slug>_receipt.json, OR
    2. Its head_sha is the second parent (or ancestor of second parent) of a merge commit on main, OR
    3. Reflog proves it authored commits that were fast-forward merged into main.

    If a branch is an ancestor of main solely because head_sha is an older first-parent commit
    on main and it has zero unique commits ahead of main, it is ZERO_COMMIT_ANCESTOR.
    """
    if not head_sha:
        try:
            head_sha = run_git(["rev-parse", branch], cwd=root, check=True).strip()
        except Exception:
            return BranchMergeState.DIVERGED_UNMERGED

    if not main_head:
        try:
            main_head = run_git(["rev-parse", main_ref], cwd=root, check=True).strip()
        except Exception:
            return BranchMergeState.DIVERGED_UNMERGED

    # 1. Sealed merge receipt check (bound to head_sha)
    if has_sealed_merge_receipt(branch, head_sha=head_sha, root=root):
        return BranchMergeState.GENUINELY_MERGED

    # 2. Check if head_sha was merged into main via merge commit (P2)
    if is_second_parent_of_merge_on_main(head_sha, branch_name=branch, root=root, main_ref=main_ref):
        return BranchMergeState.GENUINELY_MERGED

    # 2b. Check if branch was fast-forward merged into main per reflog
    if has_fast_forward_merge_in_reflog(branch, head_sha, root=root, main_ref=main_ref):
        return BranchMergeState.GENUINELY_MERGED

    # 3. Check ancestry against main
    is_ancestor = _is_ancestor_commit(head_sha, main_head, root=root)
    if not is_ancestor:
        return BranchMergeState.DIVERGED_UNMERGED

    # 4. Check ahead count vs main
    ahead_behind = run_git(["rev-list", "--left-right", "--count", f"{main_ref}...{head_sha}"], cwd=root, check=False).split()
    ahead = int(ahead_behind[1]) if len(ahead_behind) >= 2 else 0

    if ahead == 0:
        # The branch has 0 commits ahead of main and is an ancestor of main,
        # but was NOT merged via a second-parent merge commit or receipt!
        return BranchMergeState.ZERO_COMMIT_ANCESTOR

    return BranchMergeState.DIVERGED_UNMERGED
