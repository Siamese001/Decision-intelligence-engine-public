"""
Rebase recommendation and stacked lineage detection.
Synthesizes git rebase --onto commands and detects rewritten series using git range-diff.
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .pairwise import simulate_pairwise_merge


@dataclass
class RebaseRecommendation:
    """Detailed recommendation for rebasing a branch after an upstream merge."""
    branch: str
    upstream_branch: str
    target_base: str
    rebase_command: str
    is_stacked: bool
    predicted_conflict_files: List[str] = field(default_factory=list)
    range_diff_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_ANCESTOR_CACHE: Dict[Tuple[str, str, str], bool] = {}


def is_ancestor(repo_root: Path, potential_ancestor: str, descendant: str) -> bool:
    """Checks if potential_ancestor commit is an ancestor of descendant."""
    key = (str(repo_root), potential_ancestor, descendant)
    if key in _ANCESTOR_CACHE:
        return _ANCESTOR_CACHE[key]
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", potential_ancestor, descendant],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
        )
        res = (proc.returncode == 0)
        _ANCESTOR_CACHE[key] = res
        return res
    except Exception:
        return False


def compute_range_diff(
    repo_root: Path,
    base_ref: str,
    branch_a: str,
    branch_b: str,
) -> Optional[str]:
    """
    Computes git range-diff between branch_a and branch_b to detect rewritten commit series.
    Returns summary string or None if failed.
    """
    try:
        cmd = [
            "git",
            "range-diff",
            f"{base_ref}..{branch_a}",
            f"{base_ref}..{branch_b}",
        ]
        proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            # Return brief summary (first 10 lines)
            lines = proc.stdout.splitlines()[:10]
            return "\n".join(lines)
    except Exception:
        pass
    return None


def detect_stacked_lineage(
    repo_root: Path,
    branch_a: str,
    branch_b: str,
    base_ref: str = "main",
) -> bool:
    """
    Determines if branch_b is directly stacked on top of branch_a (contains all of A's commits).
    Crucially:
    - branch_a must not be identical to branch_b
    - branch_a must NOT be already an ancestor of base_ref (if already merged to main, not stacked)
    - branch_a must be an ancestor of branch_b
    """
    if branch_a == branch_b:
        return False
    if is_ancestor(repo_root, branch_a, base_ref):
        return False
    return is_ancestor(repo_root, branch_a, branch_b)


def build_rebase_recommendation(
    repo_root: Path,
    branch: str,
    upstream_branch: str,
    target_base: str = "main",
) -> RebaseRecommendation:
    """
    Synthesizes a git rebase --onto recommendation and predicts conflict files.
    """
    stacked = detect_stacked_lineage(repo_root, upstream_branch, branch, base_ref=target_base)
    rebase_cmd = f"git rebase --onto {target_base} {upstream_branch} {branch}"

    # Predict conflicts if rebasing onto target_base
    merge_sim = simulate_pairwise_merge(
        repo_root=repo_root,
        base_ref=upstream_branch,
        branch_a=target_base,
        branch_b=branch,
    )

    range_diff = compute_range_diff(repo_root, target_base, upstream_branch, branch)

    return RebaseRecommendation(
        branch=branch,
        upstream_branch=upstream_branch,
        target_base=target_base,
        rebase_command=rebase_cmd,
        is_stacked=stacked,
        predicted_conflict_files=merge_sim.conflict_files,
        range_diff_summary=range_diff,
    )
