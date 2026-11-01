"""
tools/audit_worktree_hygiene_core/reporting.py

Receipt construction and reporting helpers for worktree hygiene audits.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _is_worktree_lingering(
    wt: Dict[str, str],
    repo_root: Path,
    root_resolved: Path,
    scoped_branch: Optional[str],
    is_branch_merged_fn: Callable[[str, Path, str], bool],
    is_worktree_clean_fn: Callable[[Path], bool],
) -> Optional[str]:
    wt_path_str = wt.get("path")
    wt_branch_ref = wt.get("branch")
    if not wt_path_str or not wt_branch_ref:
        return None
    if Path(wt_path_str).resolve() == root_resolved:
        return None
    branch = wt_branch_ref.replace("refs/heads/", "")
    if branch in ("main", "master"):
        return None
    if scoped_branch is not None and branch != scoped_branch:
        return None
    if not is_branch_merged_fn(branch, repo_root, "main"):
        return None
    if scoped_branch is not None or is_worktree_clean_fn(Path(wt_path_str)):
        return branch
    return None


def check_lingering_merged_worktrees(
    repo_root: Path,
    worktrees: List[Dict[str, str]],
    scoped_branch: Optional[str],
    is_branch_merged_fn: Callable[[str, Path, str], bool],
    is_worktree_clean_fn: Callable[[Path], bool],
    errors: List[str],
) -> None:
    """Checks for unpruned merged worktrees and adds errors for lingering worktrees."""
    root_resolved = repo_root.resolve()
    for wt in worktrees:
        lingering = _is_worktree_lingering(
            wt, repo_root, root_resolved, scoped_branch, is_branch_merged_fn, is_worktree_clean_fn
        )
        if lingering:
            errors.append(
                f"[HYGIENE_VIOLATION] Merged worktree '{lingering}' at {wt.get('path')} is lingering. "
                f"Run 'pr_governance.py finish {lingering}'."
            )


def build_hygiene_receipt(report: Any, command: str) -> Dict[str, Any]:
    """Generate signed runtime receipt for worktree hygiene."""
    receipt = {
        "$schema": "urn:nee:worktree-hygiene-receipt:1.0.0",
        "timestamp_utc": report.timestamp_utc,
        "command": command,
        "status": "PASSED" if report.is_hygiene_compliant else "FAILED",
        "metrics": {
            "active_worktrees_count": len(report.active_worktrees),
            "dangling_tmp_files_count": len(report.dangling_tmp_files),
            "cleaned_tmp_files_count": report.cleaned_tmp_files_count,
            "cleaned_merged_worktrees_count": len(report.cleaned_merged_worktrees),
            "cleaned_orphaned_worktrees_count": len(report.cleaned_orphaned_worktrees),
            "cleaned_zero_commit_worktrees_count": len(report.cleaned_zero_commit_worktrees),
            "output_hierarchy_violations_count": len(report.output_hierarchy_violations),
            "worktree_registry_violations_count": len(report.worktree_registry_violations),
            "is_hygiene_compliant": report.is_hygiene_compliant,
        },
        "errors": report.errors,
        "active_worktrees": report.active_worktrees,
        "cleaned_merged_worktrees": report.cleaned_merged_worktrees,
    }
    receipt["canonical_digest"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return receipt
