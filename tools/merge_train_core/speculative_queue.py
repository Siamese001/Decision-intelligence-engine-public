"""
Speculative Cohort Assembly Engine for PR merge queues.

Tests concurrent speculative merge viability and generates traceable commit trailers.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class SpeculativeCohortReceipt:
    cohort_name: str
    branches: List[str]
    base_branch: str
    is_conflict_free: bool = False
    conflicts: List[str] = field(default_factory=list)
    consolidated_files: List[str] = field(default_factory=list)
    commit_trailers: List[str] = field(default_factory=list)
    simulation_passed: bool = False
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_git(cmd: List[str], cwd: Path) -> str:
    full_cmd = cmd if cmd and cmd[0] == "git" else ["git"] + cmd
    res = subprocess.run(full_cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def assemble_speculative_cohort(
    cohort_name: str,
    branches: List[str],
    root: Path,
    base_branch: str = "main",
    dry_run: bool = True,
) -> SpeculativeCohortReceipt:
    receipt = SpeculativeCohortReceipt(
        cohort_name=cohort_name,
        branches=branches,
        base_branch=base_branch,
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    )

    if not branches:
        receipt.conflicts.append("No branches provided for cohort assembly.")
        return receipt

    all_files: set[str] = set()
    trailers: List[str] = []

    # 1. Inspect each branch and test merge-tree against base
    for b in branches:
        sha = run_git(["rev-parse", b], root)
        if not sha:
            receipt.conflicts.append(f"Branch '{b}' could not be resolved.")
            return receipt

        # Test merge-tree
        res = subprocess.run(
            ["git", "merge-tree", "--write-tree", base_branch, b],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            receipt.conflicts.append(f"Merge conflict detected between {base_branch} and {b}")

        # Collect files
        diff_out = run_git(["diff", "--name-only", f"{base_branch}...{b}"], root)
        branch_files = [f.strip() for f in diff_out.splitlines() if f.strip()]
        all_files.update(branch_files)

        trailers.append(f"Consolidated-From: {b} ({sha[:8]})")

    receipt.consolidated_files = sorted(list(all_files))
    receipt.commit_trailers = trailers
    receipt.is_conflict_free = len(receipt.conflicts) == 0
    receipt.simulation_passed = receipt.is_conflict_free

    return receipt
