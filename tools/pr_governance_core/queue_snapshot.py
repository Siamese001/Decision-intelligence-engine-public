"""tools/pr_governance_core/queue_snapshot.py

SSOT Queue Snapshot, Plan Ownership models, and collection engine for PR governance.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .snapshot_facts import branch_git_facts as _branch_git_facts
from .snapshot_facts import collect_stashes as _collect_stashes
from .snapshot_facts import git_result as _git_result
from .snapshot_facts import worktree_dirty as _worktree_dirty
from .plan_ownership import discover_plan_ownership

SNAPSHOT_SCHEMA_VERSION = "1.0.0"


@dataclass
class PlanOwnershipRecord:
    branch_id: str
    plan_id: str
    plan_path: str
    wave_id: int
    source_hash: str
    source_type: str  # "committed", "modified", "untracked"
    declared_status: str  # "done", "open"
    verified_status: str  # "verified_done", "unverified_done", "open"
    receipt_bindings: List[str] = field(default_factory=list)


@dataclass
class WorktreeEntry:
    canonical_id: str
    name: str
    path: str
    branch: str
    head: str
    classification: str
    is_registered: bool = False
    is_external: bool = False
    is_nested: bool = False
    is_detached: bool = False
    is_dirty: bool = False
    dirty_digest: str = ""
    unique_files_count: int = 0
    error: Optional[str] = None


@dataclass
class StashEntry:
    ref: str
    commit_sha: str
    branch: str
    subject: str
    classification: str


@dataclass
class BranchSnapshot:
    branch: str
    head_sha: str
    ahead: Optional[int] = None
    behind: Optional[int] = None
    clean_merge: Optional[bool] = None
    worktree_path: Optional[str] = None
    is_dirty: bool = False
    plans: List[PlanOwnershipRecord] = field(default_factory=list)
    open_waves: str = "—"
    completed_waves: str = "—"
    readiness: str = "⏳ Authoring"
    risk_score: int = 20
    changed_files: List[str] = field(default_factory=list)
    readiness_receipt_pass: Optional[bool] = None


@dataclass
class QueueSnapshot:
    snapshot_version: str = SNAPSHOT_SCHEMA_VERSION
    repo_root: str = ""
    base_branch: str = "main"
    base_sha: str = ""
    observed_at: str = ""
    worktrees: List[WorktreeEntry] = field(default_factory=list)
    stashes: List[StashEntry] = field(default_factory=list)
    branches: List[BranchSnapshot] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    completeness_status: str = "COMPLETE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QueueSnapshot:
        wt = [WorktreeEntry(**w) for w in data.get("worktrees", [])]
        st = [StashEntry(**s) for s in data.get("stashes", [])]
        br = []
        for b in data.get("branches", []):
            b_copy = dict(b)
            plans = [PlanOwnershipRecord(**p) for p in b_copy.pop("plans", [])]
            br.append(BranchSnapshot(plans=plans, **b_copy))
        return cls(
            snapshot_version=data.get("snapshot_version", SNAPSHOT_SCHEMA_VERSION),
            repo_root=data.get("repo_root", ""),
            base_branch=data.get("base_branch", "main"),
            base_sha=data.get("base_sha", ""),
            observed_at=data.get("observed_at", ""),
            worktrees=wt,
            stashes=st,
            branches=br,
            errors=data.get("errors", []),
            completeness_status=data.get("completeness_status", "COMPLETE"),
        )


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _canonical_id(name: str, path: str) -> str:
    return f"{name}::{hashlib.sha256(path.encode()).hexdigest()[:8]}"


def _classify_worktree(
    p: Path, wt_dir: Path, is_detached: bool, is_prunable: bool
) -> Tuple[str, bool, bool]:
    exists = p.exists()
    is_ext = not str(p).startswith(str(wt_dir))
    is_nest = str(p).startswith(str(wt_dir)) and len(p.relative_to(wt_dir).parts) > 1
    if not exists or is_prunable:
        return "STALE_REGISTRATION", is_ext, is_nest
    if is_detached:
        return "DETACHED_HEAD", is_ext, is_nest
    if is_ext:
        return "EXTERNAL_REGISTERED", is_ext, is_nest
    if is_nest:
        return "NESTED_REGISTERED", is_ext, is_nest
    return "REGISTERED", is_ext, is_nest


def _parse_worktree_porcelain_block(block: str) -> Optional[Tuple[str, str, str, bool, bool]]:
    if not block.strip():
        return None
    wt_path_str, branch, head, is_detached, is_prunable = "", "", "", False, False
    for line in block.strip().splitlines():
        if line.startswith("worktree "):
            wt_path_str = line.split(" ", 1)[1].strip()
        elif line.startswith("branch "):
            branch = line.split(" ", 1)[1].strip().replace("refs/heads/", "")
        elif line.startswith("HEAD "):
            head = line.split(" ", 1)[1].strip()
        elif line.startswith("detached"):
            is_detached = True
        elif line.startswith("prunable"):
            is_prunable = True
    if not wt_path_str:
        return None
    return wt_path_str, branch, head, is_detached, is_prunable


def parse_registered_worktrees(repo_root: Path) -> List[WorktreeEntry]:
    """Parse git worktree list --porcelain into WorktreeEntry items."""
    entries: List[WorktreeEntry] = []
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode != 0:
            return []
        resolved_root, wt_dir = repo_root.resolve(), (repo_root / ".worktrees").resolve()
        for block in proc.stdout.strip().split("\n\n"):
            parsed = _parse_worktree_porcelain_block(block)
            if not parsed:
                continue
            wt_path_str, branch, head, is_detached, is_prunable = parsed
            p = Path(wt_path_str).resolve()
            if p == resolved_root:
                continue
            classification, is_ext, is_nest = _classify_worktree(p, wt_dir, is_detached, is_prunable)
            dirty, dirty_digest, dirty_error = _worktree_dirty(p) if p.is_dir() else (None, "", "worktree path is unavailable")
            entries.append(
                WorktreeEntry(
                    canonical_id=_canonical_id(p.name, str(p)),
                    name=p.name,
                    path=str(p),
                    branch=branch,
                    head=head[:8],
                    classification=classification if dirty_error is None else "UNREADABLE_WORKTREE",
                    is_registered=True,
                    is_external=is_ext,
                    is_nested=is_nest,
                    is_detached=is_detached,
                    is_dirty=dirty if dirty is not None else False,
                    dirty_digest=dirty_digest,
                    error=dirty_error,
                )
            )
    except Exception as exc:
        entries.append(WorktreeEntry(_canonical_id("unreadable", str(repo_root)), "unreadable", str(repo_root), "", "", "UNREADABLE_REGISTRY", error=str(exc)))
    return entries


def _classify_union_child(child: Path, repo_root: Path) -> Tuple[WorktreeEntry, Optional[str]]:
    cid = _canonical_id(child.name, str(child))
    if not child.is_dir():
        return WorktreeEntry(cid, child.name, str(child), "", "", "NON_DIRECTORY"), None
    try:
        from .worktree_orphan_classifier import classify_orphan_directory
        cls_name, u_files = classify_orphan_directory(child, repo_root)
        dirty, dirty_digest, dirty_error = _worktree_dirty(child)
        entry = WorktreeEntry(cid, child.name, str(child), "", "", cls_name if dirty_error is None else "UNREADABLE_WORKTREE", is_dirty=bool(dirty), dirty_digest=dirty_digest, unique_files_count=len(u_files), error=dirty_error)
        return entry, dirty_error
    except Exception as exc:
        err = f"Failed to classify worktree child {child}: {exc}"
        return WorktreeEntry(cid, child.name, str(child), "", "", "UNREADABLE_WORKTREE", error=err), err


def discover_worktrees_union(repo_root: Path) -> Tuple[List[WorktreeEntry], List[str]]:
    """Unions registered worktrees at any path with physical .worktrees entries."""
    errors: List[str] = []
    registered = parse_registered_worktrees(repo_root)
    errors.extend(e.error for e in registered if e.error)
    by_path: Dict[str, WorktreeEntry] = {e.path: e for e in registered}
    wt_dir = repo_root / ".worktrees"
    if not wt_dir.exists():
        return sorted(by_path.values(), key=lambda w: w.canonical_id), errors
    try:
        for child in sorted(wt_dir.iterdir()):
            if child.name.startswith("."):
                continue
            c_str = str(child.resolve())
            if c_str in by_path:
                continue
            entry, err = _classify_union_child(child, repo_root)
            by_path[c_str] = entry
            if err:
                errors.append(err)
    except Exception as exc:
        errors.append(f"Failed to enumerate .worktrees directory: {exc}")
    return sorted(by_path.values(), key=lambda w: w.canonical_id), errors


def collect_queue_snapshot(repo_root: Path, max_retries: int = 2) -> QueueSnapshot:
    """Collect a consistent immutable snapshot using the isolated collector."""
    from .queue_snapshot_collector import collect_queue_snapshot_impl

    return collect_queue_snapshot_impl(repo_root, max_retries)
