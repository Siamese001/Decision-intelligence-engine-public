"""
Pairwise merge simulation using git merge-tree --write-tree.
Provides true branch-vs-branch conflict matrix without modifying repository state.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Files known to be safely auto-resolvable (e.g. append-only ledgers/logs)
AUTO_RESOLVABLE_PATTERNS = (
    "CHANGELOG.md",
    "reports/test_timing_db.json",
    "reports/testing_gaps_audit_report.json",
    "hitl_decision_ledger.json",
)

RE_CONFLICT_LINE = re.compile(
    r"^CONFLICT \((.+?)\): (?:Merge conflict in )?(.+)$"
)


@dataclass
class PairwiseMergeResult:
    """Outcome of simulating a git merge between branch_a and branch_b."""
    branch_a: str
    branch_b: str
    base_ref: str
    is_clean: bool
    tree_oid: Optional[str] = None
    conflict_files: List[str] = field(default_factory=list)
    conflict_types: Dict[str, str] = field(default_factory=dict)
    conflict_count: int = 0
    auto_resolvable: bool = False
    auto_resolvable_files: List[str] = field(default_factory=list)
    raw_messages: List[str] = field(default_factory=list)
    execution_time_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_auto_resolvable(path: str) -> bool:
    """Checks whether a conflicting file pattern can be resolved automatically."""
    return any(path.endswith(pat) or pat in path for pat in AUTO_RESOLVABLE_PATTERNS)


def simulate_pairwise_merge(
    repo_root: Path,
    base_ref: str,
    branch_a: str,
    branch_b: str,
    shared_files: Optional[Set[str]] = None,
) -> PairwiseMergeResult:
    """
    Simulates merging branch_b into branch_a relative to base_ref.
    Uses `git merge-tree --write-tree --merge-base=<base> <branch_a> <branch_b>`.
    Does not touch index or working tree.
    """
    start_time = time.perf_counter()

    # Fast-path: if shared_files is provided and empty, branches are disjoint and merge is clean
    if shared_files is not None and len(shared_files) == 0:
        elapsed = time.perf_counter() - start_time
        return PairwiseMergeResult(
            branch_a=branch_a,
            branch_b=branch_b,
            base_ref=base_ref,
            is_clean=True,
            tree_oid=None,
            conflict_files=[],
            conflict_types={},
            conflict_count=0,
            auto_resolvable=True,
            auto_resolvable_files=[],
            raw_messages=["Branches share no files (disjoint fast-path)"],
            execution_time_seconds=elapsed,
        )

    # Resolve base commit
    cmd_base = ["git", "merge-base", base_ref, branch_a]
    proc_base = subprocess.run(cmd_base, cwd=str(repo_root), capture_output=True, text=True, check=False)
    merge_base = proc_base.stdout.strip() if proc_base.returncode == 0 and proc_base.stdout.strip() else base_ref

    cmd = [
        "git",
        "merge-tree",
        "--write-tree",
        f"--merge-base={merge_base}",
        branch_a,
        branch_b,
    ]

    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - start_time

    output_lines = proc.stdout.splitlines()
    tree_oid = output_lines[0].strip() if output_lines else None

    if proc.returncode == 0:
        return PairwiseMergeResult(
            branch_a=branch_a,
            branch_b=branch_b,
            base_ref=base_ref,
            is_clean=True,
            tree_oid=tree_oid,
            conflict_files=[],
            conflict_types={},
            conflict_count=0,
            auto_resolvable=True,
            auto_resolvable_files=[],
            raw_messages=[],
            execution_time_seconds=elapsed,
        )

    # Conflicts detected
    conflict_files: List[str] = []
    conflict_types: Dict[str, str] = {}
    raw_messages: List[str] = []

    for line in output_lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        raw_messages.append(stripped)
        match = RE_CONFLICT_LINE.match(stripped)
        if match:
            ctype, cfile = match.group(1).strip(), match.group(2).strip()
            # Clean up potential git conflict suffix details
            if " in " in cfile:
                cfile = cfile.split(" in ")[-1].strip()
            if cfile not in conflict_files:
                conflict_files.append(cfile)
                conflict_types[cfile] = ctype

    # Fallback parsing for lines starting with conflict markers in output
    if not conflict_files:
        for line in proc.stderr.splitlines():
            raw_messages.append(line.strip())

    auto_files = [f for f in conflict_files if _is_auto_resolvable(f)]
    is_auto = len(auto_files) == len(conflict_files) and len(conflict_files) > 0

    return PairwiseMergeResult(
        branch_a=branch_a,
        branch_b=branch_b,
        base_ref=base_ref,
        is_clean=False,
        tree_oid=tree_oid,
        conflict_files=conflict_files,
        conflict_types=conflict_types,
        conflict_count=len(conflict_files),
        auto_resolvable=is_auto,
        auto_resolvable_files=auto_files,
        raw_messages=raw_messages,
        execution_time_seconds=elapsed,
    )


def compute_pairwise_merge_matrix(
    repo_root: Path,
    base_ref: str,
    branches: List[str],
    shared_files_map: Optional[Dict[Tuple[str, str], Set[str]]] = None,
) -> Dict[str, Dict[str, PairwiseMergeResult]]:
    """
    Computes all-pairs merge simulation across a set of branches.
    Returns matrix dict mapping branch_a -> branch_b -> PairwiseMergeResult.
    """
    matrix: Dict[str, Dict[str, PairwiseMergeResult]] = {}
    branch_list = sorted(branches)

    for i, b_a in enumerate(branch_list):
        if b_a not in matrix:
            matrix[b_a] = {}
        for j, b_b in enumerate(branch_list):
            if i >= j:
                continue
            shared = None
            if shared_files_map is not None:
                shared = shared_files_map.get((b_a, b_b)) or shared_files_map.get((b_b, b_a))

            res = simulate_pairwise_merge(
                repo_root=repo_root,
                base_ref=base_ref,
                branch_a=b_a,
                branch_b=b_b,
                shared_files=shared,
            )
            matrix[b_a][b_b] = res

    return matrix
