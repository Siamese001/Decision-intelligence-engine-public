"""
Evidence extraction and pairwise branch overlap evaluation engine.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cache import EvidenceCache
from .duplicates import (
    check_identical_file_changes,
    compute_blob_sha,
    compute_commit_patch_id,
    detect_duplicate_blobs,
    detect_duplicate_commits,
)
from .hunks import compare_file_hunks, parse_unified_diff_u0
from .models import (
    BranchEvidence,
    CommitPatchEvidence,
    EvidenceReport,
    OverlapVerdict,
    PairwiseEvidence,
)
from .symbols import analyze_file_symbols, is_python_file


def _run_git_cmd(args: List[str], cwd: Path) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, proc.stdout
    except Exception as e:
        return 1, str(e)


def extract_branch_evidence(
    repo_root: Path,
    branch: str,
    base_ref: str = "main",
    cache: Optional[EvidenceCache] = None,
) -> BranchEvidence:
    """
    Extracts complete commit, diff, hunk, and AST symbol evidence for a branch
    relative to its common merge base with base_ref.
    """
    rc, head_sha_out = _run_git_cmd(["rev-parse", f"{branch}^{{commit}}"], repo_root)
    head_sha = head_sha_out.strip() if rc == 0 else ""

    rc_base, base_sha_out = _run_git_cmd(["rev-parse", f"{base_ref}^{{commit}}"], repo_root)
    base_sha = base_sha_out.strip() if rc_base == 0 else "HEAD"

    rc_mb, mb_out = _run_git_cmd(["merge-base", base_sha, head_sha or branch], repo_root)
    merge_base = mb_out.strip() if rc_mb == 0 else base_sha

    if cache and head_sha and merge_base:
        cached = cache.get(branch, head_sha, merge_base)
        if cached is not None:
            return cached

    # 1. Commit log extraction
    commits: List[CommitPatchEvidence] = []
    if head_sha and merge_base:
        rc, log_out = _run_git_cmd(
            ["log", "--format=%H%x1f%s%x1f%an%x1f%aI", f"{merge_base}..{head_sha}"],
            repo_root,
        )
        if rc == 0 and log_out.strip():
            for line in log_out.strip().splitlines():
                parts = line.split("\x1f")
                if len(parts) >= 4:
                    c_sha, subj, author, ts = parts[0], parts[1], parts[2], parts[3]
                    patch_id = compute_commit_patch_id(repo_root, c_sha) or ""
                    # List files touched by this commit
                    rc_f, files_out = _run_git_cmd(
                        ["diff-tree", "--no-commit-id", "--name-only", "-r", c_sha],
                        repo_root,
                    )
                    c_files = [f.strip() for f in files_out.strip().splitlines() if f.strip()]
                    commits.append(
                        CommitPatchEvidence(
                            commit_sha=c_sha,
                            patch_id=patch_id,
                            subject=subj,
                            author=author,
                            timestamp=ts,
                            files=c_files,
                        )
                    )

    # 2. Hunk and diff extraction
    files_map = {}
    symbols_map = {}
    if head_sha and merge_base:
        rc, diff_out = _run_git_cmd(["diff", "-U0", f"{merge_base}..{head_sha}"], repo_root)
        if rc == 0 and diff_out:
            files_map = parse_unified_diff_u0(diff_out)

        # Populate blob shas and extract Python symbols
        for path, file_diff in files_map.items():
            if file_diff.status != "D":
                blob = compute_blob_sha(repo_root, head_sha, path)
                file_diff.blob_sha = blob

                if is_python_file(path):
                    rc_cnt, content = _run_git_cmd(["show", f"{head_sha}:{path}"], repo_root)
                    if rc_cnt == 0:
                        file_syms = analyze_file_symbols(path, content, file_diff.hunks)
                        symbols_map[path] = file_syms

    evidence = BranchEvidence(
        branch=branch,
        merge_base=merge_base,
        head_sha=head_sha,
        commit_count=len(commits),
        commits=commits,
        files=files_map,
        symbols=symbols_map,
    )

    if cache and head_sha and merge_base:
        cache.set(evidence)

    return evidence


def compare_branches(
    branch_a: BranchEvidence,
    branch_b: BranchEvidence,
) -> PairwiseEvidence:
    """
    Compares two branches and evaluates their pairwise code-level overlap verdict.
    """
    shared_files = sorted(set(branch_a.files.keys()) & set(branch_b.files.keys()))
    duplicate_commits = detect_duplicate_commits(branch_a.commits, branch_b.commits)
    duplicate_blobs = detect_duplicate_blobs(branch_a.files, branch_b.files)

    disjoint_hunk_files: List[str] = []
    overlapping_hunk_files: List[str] = []
    same_symbol_files: List[str] = []

    for path in shared_files:
        fa = branch_a.files[path]
        fb = branch_b.files[path]

        # Check if both are deleted
        if fa.status == "D" and fb.status == "D":
            disjoint_hunk_files.append(path)
            continue

        # Check hunks
        is_disjoint, _ = compare_file_hunks(fa.hunks, fb.hunks)
        if is_disjoint:
            disjoint_hunk_files.append(path)
        else:
            overlapping_hunk_files.append(path)

        # Check AST symbols for Python files
        if is_python_file(path) and path in branch_a.symbols and path in branch_b.symbols:
            touched_a = {s.symbol_name for s in branch_a.symbols[path].touched_symbols}
            touched_b = {s.symbol_name for s in branch_b.symbols[path].touched_symbols}
            shared_syms = touched_a & touched_b
            if shared_syms:
                same_symbol_files.append(path)

    # Determine Verdict
    is_identical = check_identical_file_changes(branch_a.files, branch_b.files)
    if is_identical:
        verdict = OverlapVerdict.IDENTICAL
    elif len(duplicate_commits) > 0 or (
        len(duplicate_blobs) > 0
        and len(duplicate_blobs) == len(shared_files)
        and len(shared_files) > 0
    ):
        verdict = OverlapVerdict.DUPLICATE_OF
    elif len(overlapping_hunk_files) > 0:
        verdict = OverlapVerdict.OVERLAPPING_HUNKS
    elif len(same_symbol_files) > 0:
        verdict = OverlapVerdict.SAME_SYMBOL_DISJOINT_HUNKS
    elif len(shared_files) > 0:
        verdict = OverlapVerdict.DISJOINT_HUNKS
    else:
        verdict = OverlapVerdict.DISJOINT_FILES

    return PairwiseEvidence(
        branch_a=branch_a.branch,
        branch_b=branch_b.branch,
        shared_files=shared_files,
        disjoint_hunk_files=disjoint_hunk_files,
        overlapping_hunk_files=overlapping_hunk_files,
        same_symbol_files=same_symbol_files,
        duplicate_commits=duplicate_commits,
        duplicate_blobs=duplicate_blobs,
        verdict=verdict,
        details={
            "shared_file_count": len(shared_files),
            "disjoint_hunk_count": len(disjoint_hunk_files),
            "overlapping_hunk_count": len(overlapping_hunk_files),
            "same_symbol_count": len(same_symbol_files),
            "duplicate_commit_count": len(duplicate_commits),
            "duplicate_blob_count": len(duplicate_blobs),
        },
    )


def build_evidence_report(
    repo_root: Path,
    branches: List[str],
    base_ref: str = "main",
    cache_dir: Optional[Path] = None,
) -> EvidenceReport:
    """
    Builds the complete EvidenceReport across all requested branches,
    including branch evidence and the full pairwise comparison matrix.
    """
    start_time = time.perf_counter()
    cache = EvidenceCache(cache_dir=cache_dir) if cache_dir else None

    branch_evidence: Dict[str, BranchEvidence] = {}
    for b in branches:
        branch_evidence[b] = extract_branch_evidence(
            repo_root=repo_root,
            branch=b,
            base_ref=base_ref,
            cache=cache,
        )

    pairwise_matrix: Dict[str, Dict[str, PairwiseEvidence]] = {}
    summary_counts: Dict[str, int] = {v.value: 0 for v in OverlapVerdict}

    branch_list = sorted(branch_evidence.keys())
    for i, b_a in enumerate(branch_list):
        if b_a not in pairwise_matrix:
            pairwise_matrix[b_a] = {}
        for j, b_b in enumerate(branch_list):
            if i >= j:
                continue
            pair = compare_branches(branch_evidence[b_a], branch_evidence[b_b])
            pairwise_matrix[b_a][b_b] = pair
            v_val = pair.verdict.value if isinstance(pair.verdict, OverlapVerdict) else str(pair.verdict)
            summary_counts[v_val] = summary_counts.get(v_val, 0) + 1

    elapsed = time.perf_counter() - start_time

    return EvidenceReport(
        repo_root=str(repo_root),
        branches=branch_evidence,
        pairwise_matrix=pairwise_matrix,
        summary_counts=summary_counts,
        cached_items=len(cache._memory_cache) if cache else 0,
        execution_time_seconds=elapsed,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
