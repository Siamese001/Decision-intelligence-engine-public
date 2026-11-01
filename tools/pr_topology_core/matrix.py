"""
Topology Matrix calculation for git feature branches.

Computes pairwise file overlaps, lineage ancestry, and contention hotspots.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class BranchDiff:
    branch: str
    ahead: int = 0
    behind: int = 0
    files: List[str] = field(default_factory=list)
    file_count: int = 0
    is_output_only: bool = False
    is_unknown: bool = False
    ancestor_of: List[str] = field(default_factory=list)
    descendant_of: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TopologyMatrix:
    base_branch: str
    branches: List[str]
    branch_diffs: Dict[str, BranchDiff] = field(default_factory=dict)
    pairwise_overlap: Dict[str, Dict[str, int]] = field(default_factory=dict)
    shared_files: Dict[str, List[str]] = field(default_factory=dict)
    high_contention_files: Dict[str, List[str]] = field(default_factory=dict)
    superseded_candidates: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_branch": self.base_branch,
            "branches": self.branches,
            "branch_diffs": {b: diff.to_dict() for b, diff in self.branch_diffs.items()},
            "pairwise_overlap": self.pairwise_overlap,
            "shared_files": self.shared_files,
            "high_contention_files": self.high_contention_files,
            "superseded_candidates": self.superseded_candidates,
        }


def run_git_cmd(cmd: List[str], cwd: Path) -> str:
    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def get_rev_counts(base: str, target: str, cwd: Path) -> tuple[int, int]:
    base_res = run_git_cmd(["git", "merge-base", base, target], cwd)
    if not base_res:
        return -1, -1
    behind_s = run_git_cmd(["git", "rev-list", "--first-parent", "--count", f"{base_res}..{base}"], cwd)
    ahead_s = run_git_cmd(["git", "rev-list", "--count", f"{base_res}..{target}"], cwd)
    try:
        return int(behind_s), int(ahead_s)
    except ValueError:
        return -1, -1


def get_diff_files(base: str, target: str, cwd: Path) -> List[str]:
    out = run_git_cmd(["git", "diff", "-z", "--name-only", f"{base}...{target}"], cwd)
    if not out:
        return []
    return [f for f in out.split("\0") if f.strip()]


_ANCESTOR_CACHE: Dict[Tuple[str, str], bool] = {}


def is_ancestor(ancestor: str, descendant: str, cwd: Path) -> bool:
    key = (ancestor, descendant)
    if key not in _ANCESTOR_CACHE:
        res = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
        _ANCESTOR_CACHE[key] = (res.returncode == 0)
    return _ANCESTOR_CACHE[key]


def _inspect_branch_diffs(
    branches: List[str],
    root: Path,
    base_branch: str,
    branch_status_map: Optional[Dict[str, Any]] = None,
) -> Dict[str, BranchDiff]:
    diffs = {}
    for b in branches:
        if branch_status_map and b in branch_status_map:
            info = branch_status_map[b]
            ahead = info.get("ahead", -1)
            behind = info.get("behind", -1)
            files = info.get("changed_files", [])
        else:
            behind, ahead = get_rev_counts(base_branch, b, root)
            files = get_diff_files(base_branch, b, root)
        is_unknown = (ahead == -1 or behind == -1)
        is_output_only = bool(files and all(f.startswith("output/") for f in files))
        diffs[b] = BranchDiff(
            branch=b,
            ahead=ahead,
            behind=behind,
            files=files,
            file_count=len(files),
            is_output_only=is_output_only,
            is_unknown=is_unknown,
        )
    return diffs


def _compute_lineage(branches: List[str], branch_diffs: Dict[str, BranchDiff], root: Path) -> None:
    for i, b1 in enumerate(branches):
        d1 = branch_diffs[b1]
        if d1.ahead <= 0:
            continue
        s1 = set(d1.files)
        for j, b2 in enumerate(branches):
            if i == j:
                continue
            d2 = branch_diffs[b2]
            if d2.ahead <= 0:
                continue
            # For b1 to be an ancestor of b2, b1 cannot have more commits ahead than b2
            if d1.ahead > d2.ahead:
                continue
            # b2 must touch all files touched by b1 to contain b1's full diff
            s2 = set(d2.files)
            if s1 and not s1.issubset(s2):
                continue
            if is_ancestor(b1, b2, root):
                d1.ancestor_of.append(b2)
                d2.descendant_of.append(b1)


def _compute_overlaps_and_contention(
    branches: List[str],
    branch_diffs: Dict[str, BranchDiff],
) -> tuple[Dict[str, Dict[str, int]], Dict[str, List[str]], Dict[str, List[str]]]:
    file_to_branches: Dict[str, Set[str]] = {}
    for b in branches:
        for f in branch_diffs[b].files:
            file_to_branches.setdefault(f, set()).add(b)

    pairwise: Dict[str, Dict[str, int]] = {}
    shared: Dict[str, List[str]] = {}
    for i, b1 in enumerate(branches):
        pairwise[b1] = {}
        files_b1 = set(branch_diffs[b1].files)
        for j, b2 in enumerate(branches):
            if i == j:
                pairwise[b1][b2] = len(files_b1)
                continue
            files_b2 = set(branch_diffs[b2].files)
            common = sorted(list(files_b1.intersection(files_b2)))
            pairwise[b1][b2] = len(common)
            if common and i < j:
                shared[f"{b1}::{b2}"] = common

    contention: Dict[str, List[str]] = {}
    for f, blist in file_to_branches.items():
        if len(blist) >= 3:
            contention[f] = sorted(list(blist))

    return pairwise, shared, contention


def is_completely_superseded(branch1: str, branch2: str, root: Path, base_branch: str = "main") -> bool:
    """Returns True if all changes in branch1 are completely absorbed into branch2."""
    from .supersession import evaluate_branch_to_branch_supersession
    b1_sha = run_git_cmd(["git", "rev-parse", branch1], root).strip()
    b2_sha = run_git_cmd(["git", "rev-parse", branch2], root).strip()
    b1_files = get_diff_files(base_branch, branch1, root)
    b2_files = get_diff_files(base_branch, branch2, root)
    is_sup, _, _ = evaluate_branch_to_branch_supersession(
        branch1, b1_sha, branch2, b2_sha, b1_files, b2_files, root
    )
    return is_sup


def get_branch_shas(branches: List[str], root: Path) -> Dict[str, str]:
    proc = subprocess.run(["git", "show-ref", "--heads"], cwd=root, capture_output=True, text=True, check=False)
    shas = {}
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                ref_name = parts[1].removeprefix("refs/heads/")
                shas[ref_name] = parts[0]
    for b in branches:
        if b not in shas:
            shas[b] = run_git_cmd(["git", "rev-parse", b], root).strip()
    return shas


def _detect_superseded(
    branches: List[str],
    branch_diffs: Dict[str, BranchDiff],
    root: Path,
    base_branch: str = "main",
) -> List[Dict[str, Any]]:
    from .supersession import evaluate_branch_to_branch_supersession

    superseded = []
    branch_shas = get_branch_shas(branches, root)

    for i, b1 in enumerate(branches):
        if branch_diffs[b1].ahead == 0:
            continue
        for j, b2 in enumerate(branches):
            if i == j or branch_diffs[b2].ahead == 0:
                continue

            from tools.pr_governance_core.common import GENERATED_ARTIFACT_PATTERNS

            s1 = set(branch_diffs[b1].files)
            s2 = set(branch_diffs[b2].files)
            if not s1 or not s2:
                continue

            # Must share at least one substantive file to be considered for supersession (exclude generated artifacts)
            s1_sub = {f for f in s1 if not any(f.startswith(p) for p in GENERATED_ARTIFACT_PATTERNS)}
            s2_sub = {f for f in s2 if not any(f.startswith(p) for p in GENERATED_ARTIFACT_PATTERNS)}
            common = s1_sub.intersection(s2_sub)
            if not common:
                continue

            # b2 can only completely supersede b1 if all substantive files changed by b1 are touched by b2
            if s1_sub and not s1_sub.issubset(s2_sub):
                continue

            b1_sha = branch_shas.get(b1, "")
            b2_sha = branch_shas.get(b2, "")

            is_sup, evidence, _ = evaluate_branch_to_branch_supersession(
                b1, b1_sha, b2, b2_sha, branch_diffs[b1].files, branch_diffs[b2].files, root
            )
            if is_sup and evidence:
                superseded.append(
                    {
                        "older_branch": b1,
                        "newer_branch": b2,
                        "newer_sha": b2_sha[:8],
                        "shared_file_count": len(common),
                        "older_behind": branch_diffs[b1].behind,
                        "newer_behind": branch_diffs[b2].behind,
                        "evidence_type": evidence.evidence_type,
                        "confidence": evidence.confidence,
                        "recommendation": f"Tombstone {b1} in favor of {b2}",
                    }
                )
    return superseded


def compute_topology_matrix(
    branches: List[str],
    root: Path,
    base_branch: str = "main",
    branch_status_map: Optional[Dict[str, Any]] = None,
) -> TopologyMatrix:
    matrix = TopologyMatrix(base_branch=base_branch, branches=branches)
    matrix.branch_diffs = _inspect_branch_diffs(
        branches, root, base_branch, branch_status_map=branch_status_map
    )
    _compute_lineage(branches, matrix.branch_diffs, root)
    matrix.pairwise_overlap, matrix.shared_files, matrix.high_contention_files = (
        _compute_overlaps_and_contention(branches, matrix.branch_diffs)
    )
    matrix.superseded_candidates = _detect_superseded(branches, matrix.branch_diffs, root, base_branch)
    return matrix
