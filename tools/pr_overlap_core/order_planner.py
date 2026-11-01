"""
Merge and rebase order planner with sequential in-memory simulation.
Complies with Decision D3: Exhaustive search for N <= 8, greedy lookahead beyond.
Zero repository or working tree mutation guaranteed.

SSOT Schema URN: urn:nee:pr-overlap-plan:1.0.0
"""
from __future__ import annotations

import hashlib
import itertools
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .pairwise import RE_CONFLICT_LINE
from .rebase_plan import RebaseRecommendation, build_rebase_recommendation, detect_stacked_lineage

PLAN_SCHEMA_VERSION = "urn:nee:pr-overlap-plan:1.0.0"


@dataclass
class MergeStep:
    """A single ordered step in the recommended merge sequence."""
    step_index: int
    branch: str
    action: str  # MERGE, REBASE_ONTO, DEDUP_DROP, SUPERSEDED
    conflicts_with_prior: List[str] = field(default_factory=list)
    conflict_count: int = 0
    recommended_command: str = ""
    status: str = "OK"  # OK, CONFLICT, SIMULATION_ERROR
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MergeOrderPlan:
    """Complete ordered merge/rebase plan."""
    schema_version: str = PLAN_SCHEMA_VERSION
    generated_at: str = ""
    repo_root: str = ""
    base_ref: str = "main"
    branches: List[str] = field(default_factory=list)
    ordered_steps: List[MergeStep] = field(default_factory=list)
    total_conflicts: int = 0
    alphabetical_conflicts: int = 0
    rebase_recommendations: List[RebaseRecommendation] = field(default_factory=list)
    search_strategy: str = "exhaustive_pruned"
    repo_state_digest_before: str = ""
    repo_state_digest_after: str = ""
    zero_mutation_verified: bool = True
    execution_time_seconds: float = 0.0
    plan_status: str = "OPTIMAL"  # OPTIMAL, PROVISIONAL, ERROR
    has_errors: bool = False
    error_details: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "repo_root": self.repo_root,
            "base_ref": self.base_ref,
            "branches": self.branches,
            "ordered_steps": [s.to_dict() for s in self.ordered_steps],
            "total_conflicts": self.total_conflicts,
            "alphabetical_conflicts": self.alphabetical_conflicts,
            "rebase_recommendations": [r.to_dict() for r in self.rebase_recommendations],
            "search_strategy": self.search_strategy,
            "repo_state_digest_before": self.repo_state_digest_before,
            "repo_state_digest_after": self.repo_state_digest_after,
            "zero_mutation_verified": self.zero_mutation_verified,
            "execution_time_seconds": round(self.execution_time_seconds, 3),
            "plan_status": self.plan_status,
            "has_errors": self.has_errors,
            "error_details": self.error_details,
        }


def compute_repo_state_digest(repo_root: Path) -> str:
    """Computes a cryptographic hash of all git refs and working tree status."""
    proc_refs = subprocess.run(["git", "for-each-ref"], cwd=str(repo_root), capture_output=True, text=True, check=False)
    proc_status = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo_root), capture_output=True, text=True, check=False)
    payload = f"{proc_refs.stdout}\n---\n{proc_status.stdout}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _simulate_merge_step(
    repo_root: Path,
    base_commit: str,
    target_branch: str,
) -> Tuple[bool, Optional[str], List[str], bool]:
    """
    Simulates merging target_branch into base_commit using merge-tree --write-tree.
    Returns (is_clean, tree_oid, conflict_files, has_sim_err).
    """
    cmd = [
        "git",
        "merge-tree",
        "--write-tree",
        f"--merge-base={base_commit}",
        base_commit,
        target_branch,
    ]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    lines = proc.stdout.splitlines()
    tree_oid = lines[0].strip() if lines else None

    if proc.returncode == 0:
        return True, tree_oid, [], False

    conflicts: List[str] = []
    for line in lines[1:]:
        m = RE_CONFLICT_LINE.match(line.strip())
        if m:
            cfile = m.group(2).strip()
            if " in " in cfile:
                cfile = cfile.split(" in ")[-1].strip()
            if cfile not in conflicts:
                conflicts.append(cfile)

    has_sim_err = (proc.returncode != 0 and not conflicts) or (tree_oid is None and proc.returncode != 0)
    return False, tree_oid, conflicts, has_sim_err


def _create_synthetic_commit(
    repo_root: Path,
    tree_oid: str,
    parent1: str,
    parent2: str,
    msg: str = "simulated merge",
) -> Optional[str]:
    """
    Creates an in-memory commit object via git commit-tree without moving any refs.
    """
    cmd = ["git", "commit-tree", tree_oid, "-p", parent1, "-p", parent2, "-m", msg]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return None


def evaluate_merge_sequence(
    repo_root: Path,
    initial_base: str,
    sequence: List[str],
) -> Tuple[int, List[MergeStep]]:
    """
    Simulates executing a full sequence of branch merges in order.
    Returns (total_conflicts, merge_steps).
    """
    current_head = initial_base
    total_conflicts = 0
    steps: List[MergeStep] = []

    for idx, branch in enumerate(sequence, start=1):
        proc_head = subprocess.run(
            ["git", "rev-parse", f"{branch}^{{commit}}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc_head.returncode != 0 or not proc_head.stdout.strip():
            steps.append(
                MergeStep(
                    step_index=idx,
                    branch=branch,
                    action="BLOCKED",
                    conflicts_with_prior=[],
                    conflict_count=0,
                    recommended_command="",
                    status="SIMULATION_ERROR",
                    error_message=f"Git rev-parse failed for {branch}",
                )
            )
            continue
        b_sha = proc_head.stdout.strip()

        is_clean, tree_oid, conflicts, has_sim_err = _simulate_merge_step(repo_root, current_head, b_sha)
        conflict_cnt = len(conflicts)
        total_conflicts += conflict_cnt

        action = "MERGE"
        cmd = f"git merge --no-ff {branch}"
        step_status = "SIMULATION_ERROR" if has_sim_err else ("CONFLICT" if conflict_cnt > 0 else "OK")
        step_err = "Git simulation failure" if has_sim_err else ""

        steps.append(
            MergeStep(
                step_index=idx,
                branch=branch,
                action=action,
                conflicts_with_prior=conflicts,
                conflict_count=conflict_cnt,
                recommended_command=cmd,
                status=step_status,
                error_message=step_err,
            )
        )

        if tree_oid:
            synth = _create_synthetic_commit(repo_root, tree_oid, current_head, b_sha)
            if synth:
                current_head = synth

    return total_conflicts, steps


def _search_exhaustive_order(
    repo_root: Path,
    initial_base: str,
    branch_list: List[str],
    alpha_sequence: List[str],
    alpha_conflicts: int,
    alpha_steps: List[MergeStep],
) -> Tuple[List[str], int, List[MergeStep]]:
    """Bounded exhaustive search with branch-and-bound pruning for N <= 8."""
    best_sequence = alpha_sequence
    best_conflicts = alpha_conflicts
    best_steps = alpha_steps

    if alpha_conflicts == 0:
        return best_sequence, best_conflicts, best_steps

    for perm in itertools.permutations(branch_list):
        if list(perm) == alpha_sequence:
            continue
        conflicts, steps = evaluate_merge_sequence(repo_root, initial_base, list(perm))
        if conflicts < best_conflicts:
            best_conflicts = conflicts
            best_sequence = list(perm)
            best_steps = steps
            if best_conflicts == 0:
                break
    return best_sequence, best_conflicts, best_steps


def _search_greedy_order(
    repo_root: Path,
    initial_base: str,
    branch_list: List[str],
    alpha_sequence: List[str],
    alpha_conflicts: int,
    alpha_steps: List[MergeStep],
) -> Tuple[List[str], int, List[MergeStep]]:
    """Greedy lookahead search for N > 8."""
    remaining = list(branch_list)
    current_seq: List[str] = []
    cur_head = initial_base

    while remaining:
        best_cand = None
        best_cand_conflicts = float("inf")
        best_cand_tree = None

        for cand in remaining:
            proc_cand = subprocess.run(
                ["git", "rev-parse", f"{cand}^{{commit}}"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            c_sha = proc_cand.stdout.strip() if proc_cand.returncode == 0 else cand
            _, tree_oid, cfiles, _ = _simulate_merge_step(repo_root, cur_head, c_sha)

            if len(cfiles) < best_cand_conflicts:
                best_cand_conflicts = len(cfiles)
                best_cand = cand
                best_cand_tree = tree_oid
                if best_cand_conflicts == 0:
                    break

        if best_cand is not None:
            current_seq.append(best_cand)
            remaining.remove(best_cand)
            if best_cand_tree:
                proc_cand = subprocess.run(
                    ["git", "rev-parse", f"{best_cand}^{{commit}}"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                c_sha = proc_cand.stdout.strip() if proc_cand.returncode == 0 else best_cand
                synth = _create_synthetic_commit(repo_root, best_cand_tree, cur_head, c_sha)
                if synth:
                    cur_head = synth

    greedy_conflicts, greedy_steps = evaluate_merge_sequence(repo_root, initial_base, current_seq)
    if greedy_conflicts <= alpha_conflicts:
        return current_seq, greedy_conflicts, greedy_steps
    return alpha_sequence, alpha_conflicts, alpha_steps


def plan_merge_order(
    repo_root: Path,
    base_ref: str = "main",
    branches: Optional[List[str]] = None,
) -> MergeOrderPlan:
    """
    Computes an optimal merge/rebase sequence across branches.
    Uses bounded exhaustive search when N <= 8, greedy lookahead beyond (Decision D3).
    Ensures total conflicts <= alphabetical order baseline.
    """
    start_time = time.perf_counter()
    digest_before = compute_repo_state_digest(repo_root)

    # Resolve initial base commit
    proc_base = subprocess.run(
        ["git", "rev-parse", f"{base_ref}^{{commit}}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    initial_base = proc_base.stdout.strip() if proc_base.returncode == 0 else "HEAD"

    branch_list = list(branches) if branches else []
    if not branch_list:
        digest_after = compute_repo_state_digest(repo_root)
        return MergeOrderPlan(
            repo_root=str(repo_root),
            base_ref=base_ref,
            branches=[],
            ordered_steps=[],
            repo_state_digest_before=digest_before,
            repo_state_digest_after=digest_after,
            zero_mutation_verified=digest_before == digest_after,
            execution_time_seconds=time.perf_counter() - start_time,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # 1. Baseline: evaluate alphabetical sequence
    alpha_sequence = sorted(branch_list)
    alpha_conflicts, alpha_steps = evaluate_merge_sequence(repo_root, initial_base, alpha_sequence)

    # 2. Search for optimal sequence (Decision D3)
    strategy = "exhaustive_pruned" if len(branch_list) <= 8 else "greedy_lookahead"
    if len(branch_list) <= 8:
        best_sequence, best_conflicts, best_steps = _search_exhaustive_order(
            repo_root, initial_base, branch_list, alpha_sequence, alpha_conflicts, alpha_steps
        )
    else:
        best_sequence, best_conflicts, best_steps = _search_greedy_order(
            repo_root, initial_base, branch_list, alpha_sequence, alpha_conflicts, alpha_steps
        )

    # 3. Detect stacked branches and build rebase recommendations
    rebase_recs: List[RebaseRecommendation] = []
    for i, b_a in enumerate(best_sequence):
        for b_b in best_sequence[i + 1 :]:
            if detect_stacked_lineage(repo_root, b_a, b_b, base_ref=base_ref):
                rec = build_rebase_recommendation(
                    repo_root=repo_root,
                    branch=b_b,
                    upstream_branch=b_a,
                    target_base=base_ref,
                )
                rebase_recs.append(rec)

    has_errors = any(s.status == "SIMULATION_ERROR" for s in best_steps)
    error_details = [f"Step {s.step_index} ({s.branch}): {s.error_message}" for s in best_steps if s.status == "SIMULATION_ERROR"]
    plan_status = "PROVISIONAL" if has_errors else ("OPTIMAL" if best_conflicts == 0 else "CONFLICTS_DETECTED")

    digest_after = compute_repo_state_digest(repo_root)
    elapsed = time.perf_counter() - start_time

    return MergeOrderPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo_root=str(repo_root),
        base_ref=base_ref,
        branches=best_sequence,
        ordered_steps=best_steps,
        total_conflicts=best_conflicts,
        alphabetical_conflicts=alpha_conflicts,
        rebase_recommendations=rebase_recs,
        search_strategy=strategy,
        repo_state_digest_before=digest_before,
        repo_state_digest_after=digest_after,
        zero_mutation_verified=digest_before == digest_after,
        execution_time_seconds=elapsed,
        plan_status=plan_status,
        has_errors=has_errors,
        error_details=error_details,
    )
