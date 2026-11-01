"""Bounded, retryable queue snapshot collection implementation."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .plan_ownership import discover_plan_ownership
from .queue_snapshot import (
    BranchSnapshot, QueueSnapshot, StashEntry, WorktreeEntry,
    _branch_git_facts, _collect_stashes, _git_result, _worktree_dirty,
    discover_worktrees_union,
)


def _start_state(repo_root: Path) -> dict[str, Any]:
    errors = []
    base, base_error = _git_result(repo_root, ["rev-parse", "main"])
    head, head_error = _git_result(repo_root, ["rev-parse", "HEAD"])
    errors.extend(error for error in (base_error, head_error) if error)
    worktrees, worktree_errors = discover_worktrees_union(repo_root)
    errors.extend(worktree_errors)
    branch_output, branch_error = _git_result(repo_root, ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
    if branch_error:
        errors.append(branch_error)
    branches = {branch.strip() for branch in (branch_output or "").splitlines() if branch.strip()}
    stash_rows, stash_error = _collect_stashes(repo_root, branches)
    stashes = [StashEntry(row["ref"], row["commit_sha"], row["branch"], row["subject"], row["classification"]) for row in stash_rows]
    if stash_error:
        errors.append(stash_error)
    _, dirty_digest, dirty_error = _worktree_dirty(repo_root)
    if dirty_error:
        errors.append(dirty_error)
    return {"base": base, "head": head, "errors": errors, "worktrees": worktrees, "branches": branches, "stashes": stashes, "dirty_digest": dirty_digest}


def _branch_progress(plans: list[Any]) -> tuple[str, str]:
    completed = sorted({plan.wave_id for plan in plans if plan.verified_status == "verified_done"})
    opened = {plan.wave_id for plan in plans if plan.declared_status == "open"}
    opened.update(plan.wave_id for plan in plans if plan.declared_status == "done" and plan.verified_status != "verified_done")
    complete_text = ", ".join(map(str, completed)) if completed else "—"
    open_text = ", ".join(map(str, sorted(opened))) if opened else ("?" if not plans else "—")
    return complete_text, open_text


def _branch_snapshot(repo_root: Path, branch: str, worktree: WorktreeEntry | None, current_branch: str) -> tuple[BranchSnapshot, list[str]]:
    from .inspection import inspect_branch_status
    from .plan_ownership import discover_plan_ownership
    from .common import run_git

    errors: list[str] = []
    head, head_error = _git_result(repo_root, ["rev-parse", f"refs/heads/{branch}"])
    if head_error:
        errors.append(head_error)
        head = "UNKNOWN"
    wt_path = Path(worktree.path) if worktree and Path(worktree.path).exists() else (repo_root if branch == current_branch else None)
    dirty, _, dirty_error = _worktree_dirty(wt_path) if wt_path else (False, "", None)
    if dirty_error:
        errors.append(dirty_error)
    changed, changed_error = _git_result(repo_root, ["diff", "--name-only", f"main...{branch}"])
    if changed_error:
        errors.append(changed_error)
    def show(ref: str, path: str) -> str:
        return run_git(["show", f"{ref}:{path}"], cwd=repo_root, check=False) or ""
    ahead, behind, clean_merge, fact_errors = _branch_git_facts(repo_root, branch, "main")
    errors.extend(fact_errors)
    from .wave_status import compute_wave_columns
    opened, completed = compute_wave_columns(branch, show, [line for line in (changed or "").splitlines() if line], wt_path)
    plans = discover_plan_ownership(branch, repo_root, wt_path, show_fn=show)
    try:
        readiness = inspect_branch_status(branch, repo_root=repo_root).get("readiness_receipt_pass")
    except Exception as exc:
        readiness = None
        errors.append(f"Readiness evidence unavailable for {branch}: {exc}")
    snapshot = BranchSnapshot(
        branch=branch, head_sha=head or "UNKNOWN", ahead=ahead, behind=behind, clean_merge=clean_merge,
        worktree_path=str(wt_path) if wt_path else None, is_dirty=bool(dirty), plans=plans,
        open_waves=opened, completed_waves=completed,
        changed_files=[line for line in (changed or "").splitlines() if line] if not changed_error else [],
        readiness_receipt_pass=readiness,
    )
    return snapshot, errors


def _collect_branch_snapshots(repo_root: Path, state: dict[str, Any]) -> tuple[list[BranchSnapshot], list[str]]:
    errors: list[str] = []
    by_branch = {worktree.branch: worktree for worktree in state["worktrees"] if worktree.branch}
    selected = sorted(branch for branch in state["branches"] if branch != "main" and not branch.startswith("backup-"))
    current = _git_result(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])[0]
    snapshots = []
    for branch in selected:
        snapshot, branch_errors = _branch_snapshot(repo_root, branch, by_branch.get(branch), current)
        snapshots.append(snapshot)
        errors.extend(branch_errors)
    return snapshots, errors


def _changed_during_collection(repo_root: Path, state: dict[str, Any]) -> tuple[bool, str]:
    base_end = _git_result(repo_root, ["rev-parse", "main"])[0]
    head_end = _git_result(repo_root, ["rev-parse", "HEAD"])[0]
    end_refs = _git_result(repo_root, ["for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"])[0]
    start_refs = "\n".join(f"{branch} {_git_result(repo_root, ['rev-parse', f'refs/heads/{branch}'])[0]}" for branch in sorted(state["branches"]))
    worktrees, worktree_errors = discover_worktrees_union(repo_root)
    stashes, stash_error = _collect_stashes(repo_root, state["branches"])
    _, dirty_end, dirty_error = _worktree_dirty(repo_root)
    changed = any((
        state["base"] != base_end, state["head"] != head_end, end_refs != start_refs,
        [(w.canonical_id, w.dirty_digest) for w in state["worktrees"]] != [(w.canonical_id, w.dirty_digest) for w in worktrees],
        [(s.commit_sha, s.branch) for s in state["stashes"]] != [(s["commit_sha"], s["branch"]) for s in stashes],
        state["dirty_digest"] != dirty_end, bool(dirty_error), bool(worktree_errors), bool(stash_error),
    ))
    return changed, base_end or "UNKNOWN"


def _collect_attempt(repo_root: Path) -> tuple[QueueSnapshot, bool]:
    state = _start_state(repo_root)
    branches, branch_errors = _collect_branch_snapshots(repo_root, state)
    errors = [*state["errors"], *branch_errors]
    changed, base_end = _changed_during_collection(repo_root, state)
    status = "INCONSISTENT" if changed else ("PARTIAL" if errors else "COMPLETE")
    if changed:
        errors.append("Concurrent refs, worktree, stash, or registry change detected during snapshot collection.")
    snapshot = QueueSnapshot(
        repo_root=str(repo_root), base_sha=base_end if changed else (state["base"] or "UNKNOWN"),
        observed_at=datetime.now(timezone.utc).isoformat(), worktrees=state["worktrees"],
        stashes=state["stashes"], branches=branches, errors=errors, completeness_status=status,
    )
    return snapshot, changed


def collect_queue_snapshot_impl(repo_root: Path, max_retries: int = 2) -> QueueSnapshot:
    """Retry a snapshot only when its source changed during collection."""
    snapshot = QueueSnapshot(repo_root=str(repo_root), completeness_status="INCONSISTENT")
    for attempt in range(max_retries + 1):
        snapshot, changed = _collect_attempt(repo_root)
        if not changed or attempt == max_retries:
            return snapshot
    return snapshot
