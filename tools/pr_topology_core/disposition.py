from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.pr_governance_core.common import ROOT, run_git
from tools.pr_governance_core.container_triage import are_branch_stashes_superseded_on_main
from .matrix import BranchDiff, TopologyMatrix
from .supersession import (
    evaluate_commit_supersession,
    evaluate_dirty_file_coverage,
    is_ancestor,
)

# Sentinel for "no more specific rule applied"; the queue renderer replaces it
# with a concrete step from tools/pr_governance_core/queue_actions.py.
GENERIC_KEEP_REASON = "Active branch with unique work"


def _load_tombstones_file(path: Path, res: Dict[str, Dict[str, Any]]) -> None:
    if not path.is_file():
        return
    try:
        import json
        d = json.loads(path.read_text(encoding="utf-8"))
        for b, m in d.get("tiers", {}).get("TOMBSTONE", {}).get("branches", {}).items():
            res.setdefault(b, m if isinstance(m, dict) else {"rationale": str(m)})
        for b, m in d.get("overrides", {}).items():
            if isinstance(m, dict) and m.get("tier") == "TOMBSTONE":
                res.setdefault(b, m)
    except Exception:
        pass


def _extract_registry_tombstones(root: Path, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    res: Dict[str, Dict[str, Any]] = {}
    for b, d in (overrides or {}).items():
        if (d.get("tier") if isinstance(d, dict) else str(d)) == "TOMBSTONE":
            res[b] = d if isinstance(d, dict) else {"tier": "TOMBSTONE", "rationale": str(d)}
    for reg in dict.fromkeys([root / "config" / "pr_priority_registry.json", ROOT / "config" / "pr_priority_registry.json"]):
        _load_tombstones_file(reg, res)
    return res


@dataclass
class BranchDisposition:
    branch: str
    action: str  # CONSOLIDATE, TOMBSTONE, PRUNE, KEEP, COMMIT_FIRST, ARCHIVE
    reason: str
    target: Optional[str] = None
    target_sha: Optional[str] = None
    superseded_by: Optional[Dict[str, Any]] = None
    confidence: str = "HIGH"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _evaluate_guard_dispositions(
    matrix: TopologyMatrix,
    worktree_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, BranchDisposition]:
    """Applies global safety guards (backup branches, unknown git errors, mid-rebase)."""
    dispositions: Dict[str, BranchDisposition] = {}
    for branch, diff in matrix.branch_diffs.items():
        if branch.startswith("backup/"):
            dispositions[branch] = BranchDisposition(branch=branch, action="ARCHIVE", reason="Backup branch preserved (never pruned or tombstoned)")
        elif diff.ahead == -1 or getattr(diff, "is_unknown", False):
            dispositions[branch] = BranchDisposition(branch=branch, action="KEEP", reason="unknown commit state / git failure (fail closed)")
        elif worktree_info and worktree_info.get(branch):
            wt = worktree_info[branch]
            if wt.get("mid_rebase"):
                dispositions[branch] = BranchDisposition(branch=branch, action="KEEP", reason="mid-rebase worktree; rebase in progress")
            elif wt.get("status") == "UNKNOWN" or str(wt.get("summary", "")).startswith("UNKNOWN"):
                dispositions[branch] = BranchDisposition(branch=branch, action="KEEP", reason=f"Worktree state unknown ({wt.get('summary', 'unknown')})")
    return dispositions


def _check_inception_disposition(
    branch: str,
    diff: BranchDiff,
    wt: Optional[Dict[str, Any]],
    wt_path: Optional[Path],
    root: Path,
    base_branch: str,
) -> Optional[BranchDisposition]:
    if not wt_path:
        return None
    from tools.pr_topology_core.birth import inspect_branch_birth
    birth_info = inspect_branch_birth(branch, root=root, worktree_path=wt_path)
    if not birth_info.is_inception_active or diff.behind >= 20:
        return None
    is_cov, ev, residual = evaluate_dirty_file_coverage(
        wt_path, base_branch, root=root, ignore_review_artifacts=True
    )
    if is_cov and ev and ev[0].evidence_type == "CANONICAL_MEETING_SUPERSEDED":
        return BranchDisposition(
            branch=branch, action="PRUNE", confidence="HIGH", reason=ev[0].description,
            target=ev[0].target_ref, superseded_by={"kind": "main", "ref": ev[0].target_ref, "confidence": "HIGH"},
        )
    if not is_cov and residual:
        return BranchDisposition(
            branch=branch, action="COMMIT_FIRST", confidence="HIGH",
            reason=f"Fresh branch in inception ({birth_info.age_desc}) with {len(residual)} uncommitted file(s); commit work before rebasing onto {base_branch}",
        )
    stash_cnt = wt.get("stash_count", 0) if wt else 0
    if stash_cnt > 0:
        return BranchDisposition(
            branch=branch, action="COMMIT_FIRST", confidence="HIGH",
            reason=f"Fresh branch in inception ({birth_info.age_desc}) with {stash_cnt} stash entry/entries; preserve or restore stash before rebasing onto {base_branch}",
        )
    if diff.behind > 0:
        return BranchDisposition(
            branch=branch, action="KEEP",
            reason=f"Fresh branch in inception ({birth_info.age_desc}); behind {diff.behind} commits, rebase onto {base_branch}",
        )
    return BranchDisposition(
        branch=branch, action="KEEP",
        reason=f"Fresh branch in inception ({birth_info.age_desc}); authoring plan/code in progress",
    )


def _check_waves_disposition(
    branch: str,
    wt_path: Optional[Path],
    waves_info: Optional[Dict[str, Any]],
    diff: Optional[BranchDiff] = None,
) -> Optional[BranchDisposition]:
    if not waves_info or branch not in waves_info:
        return None
    w_info = waves_info[branch]
    open_waves = w_info.get("open_waves") if isinstance(w_info, dict) else str(w_info)
    # Stale branches with ahead == 0 and behind >= 20 must NOT receive KEEP immunity under '?' or open waves
    if diff and getattr(diff, "ahead", None) == 0 and getattr(diff, "behind", 0) >= 20:
        return None
    if open_waves == "?" and wt_path:
        return BranchDisposition(branch=branch, action="KEEP", reason="Inception planning in progress; author implementation_plan.md")
    if open_waves and open_waves not in ("—", "?"):
        return BranchDisposition(branch=branch, action="KEEP", reason=f"Active wave in flight (Wave {open_waves} open)")
    return None


def _check_ahead_zero_worktree_safety(
    branch: str,
    diff: BranchDiff,
    wt: Optional[Dict[str, Any]],
    wt_path: Optional[Path],
    waves_info: Optional[Dict[str, Any]],
    root: Path,
    base_branch: str,
    lifecycle_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[BranchDisposition]:
    """Checks lifecycle tombstone, inception immunity, open waves, stashes, and dirty file safety."""
    tomb_reg = _extract_registry_tombstones(root, lifecycle_overrides)
    if branch in tomb_reg:
        t_meta = tomb_reg[branch]
        reason = t_meta.get("reason") or t_meta.get("rationale") or "Flagged for tombstone decommissioning in registry"
        sup_by = t_meta.get("superseded_by")
        return BranchDisposition(
            branch=branch, action="TOMBSTONE", reason=reason, target=sup_by,
            superseded_by={"ref": sup_by, "kind": "branch", "confidence": "HIGH"} if sup_by else None,
            confidence="HIGH",
        )
    incept = _check_inception_disposition(branch, diff, wt, wt_path, root, base_branch)
    if incept:
        return incept
    w_disp = _check_waves_disposition(branch, wt_path, waves_info, diff=diff)
    if w_disp:
        return w_disp
    stash_cnt = wt.get("stash_count", 0) if wt else 0
    if stash_cnt > 0 and not are_branch_stashes_superseded_on_main(branch, root):
        return BranchDisposition(branch=branch, action="COMMIT_FIRST", reason=f"ahead==0 but {stash_cnt} stash entry/entries; restore or drop stash before prune")
    if wt and wt.get("dirty"):
        if wt_path:
            is_cov, ev, residual = evaluate_dirty_file_coverage(wt_path, base_branch, root=root, ignore_review_artifacts=True)
            if is_cov and ev and ev[0].evidence_type in ("CANONICAL_MEETING_SUPERSEDED", "BLOB_SAME_PATH", "HUNK_ABSORBED"):
                target_ref = ev[0].target_ref or base_branch
                return BranchDisposition(
                    branch=branch, action="PRUNE", confidence="HIGH", reason=ev[0].description,
                    target=target_ref, superseded_by={"kind": "main", "ref": target_ref, "confidence": "HIGH"},
                )
            if not is_cov and ev and ev[0].evidence_type == "SALVAGEABLE_DELIVERABLES":
                return BranchDisposition(
                    branch=branch, action="SALVAGE_DELIVERABLES", confidence="HIGH", reason=ev[0].description,
                    target=base_branch,
                )
            if not is_cov and ev and ev[0].evidence_type == "PARTIAL" and ev[0].files_covered and residual:
                cov_cnt = len(ev[0].files_covered)
                res_cnt = len(residual)
                return BranchDisposition(
                    branch=branch, action="SALVAGE_DELIVERABLES", confidence="MEDIUM",
                    reason=f"ahead==0 partial absorption: {cov_cnt} dirty file(s) already in {base_branch}, {res_cnt} unique file(s) remaining ({', '.join(residual[:2])})",
                    target=base_branch,
                )
            if not is_cov:
                from tools.pr_governance_core.diff_semantics import is_code_file
                if diff.behind >= 20 and any(is_code_file(f) for f in residual):
                    return BranchDisposition(
                        branch=branch, action="SALVAGE_DELIVERABLES", confidence="HIGH",
                        reason=f"ahead==0 and severely diverged ({diff.behind} commits behind {base_branch}) with {len(residual)} dirty file(s) including code; salvage unique changes rather than rebasing stale code",
                        target=base_branch,
                    )
                return BranchDisposition(branch=branch, action="COMMIT_FIRST", confidence="HIGH", reason=f"ahead==0 but {len(residual)} uncommitted authored file(s); commit before prune")
        else:
            cnt = wt.get("authored_files_count") or wt.get("dirty_files_count", 0)
            if diff.behind >= 20:
                return BranchDisposition(branch=branch, action="SALVAGE_DELIVERABLES", confidence="HIGH", reason=f"ahead==0 and severely diverged ({diff.behind} commits behind {base_branch}) with uncommitted files; salvage unique changes before prune")
            return BranchDisposition(branch=branch, action="COMMIT_FIRST", confidence="HIGH", reason=f"ahead==0 but {cnt} uncommitted authored file(s); commit before prune")
    return None


def _evaluate_ahead_zero_branch(
    branch: str,
    diff: BranchDiff,
    wt: Optional[Dict[str, Any]],
    waves_info: Optional[Dict[str, Any]],
    root: Path,
    main_sha: str,
    main_tree: str,
    base_branch: str = "main",
    lifecycle_overrides: Optional[Dict[str, Any]] = None,
) -> BranchDisposition:
    """Evaluates an ahead==0 branch with Inception Immunity, Open Wave Invariant, Stash Check, and Dirty Blob Check."""
    wt_path = Path(wt["path"]) if wt and wt.get("path") else None
    safety_disp = _check_ahead_zero_worktree_safety(
        branch, diff, wt, wt_path, waves_info, root, base_branch, lifecycle_overrides
    )
    if safety_disp is not None:
        return safety_disp

    from tools.pr_governance_core.common import slugify_branch
    slug = slugify_branch(branch)
    merge_receipt = root / "reports" / f"pr_merge_{slug}_receipt.json"
    if merge_receipt.exists():
        return BranchDisposition(
            branch=branch, action="PRUNE", target=base_branch, target_sha=main_sha[:8], confidence="HIGH",
            reason=f"ahead==0 (Already merged into {base_branch} per {merge_receipt.name})",
        )

    try:
        branch_sha = run_git(["rev-parse", branch], cwd=root, check=False).strip()
    except Exception:
        branch_sha = ""
    if not branch_sha:
        return BranchDisposition(branch=branch, action="PRUNE", reason="ahead==0 (Deleted branch ref)", confidence="HIGH")

    target, _, _ = evaluate_commit_supersession(
        branch, branch_sha, main_sha, main_tree, {}, root=root, base_branch=base_branch
    )
    if target and target.confidence == "HIGH":
        sup_sha = target.sha
        sup_dict = {"kind": "main", "ref": base_branch, "sha": sup_sha, "confidence": "HIGH"}
        return BranchDisposition(
            branch=branch, action="PRUNE", reason=f"ahead==0 (Merged into {base_branch}@{sup_sha})",
            target=base_branch, target_sha=sup_sha, superseded_by=sup_dict, confidence="HIGH",
        )

    return BranchDisposition(
        branch=branch, action="PRUNE", target=base_branch, target_sha=main_sha[:8], confidence="HIGH",
        reason=f"ahead==0 (No unique commits vs {base_branch}, abandoned zero-commit branch)",
    )


def _evaluate_tombstone_candidates(
    candidates: List[Dict[str, Any]],
    worktree_info: Optional[Dict[str, Any]],
    branch_health: Dict[str, str],
    root: Path,
) -> Dict[str, BranchDisposition]:
    """Evaluates tombstone candidates ensuring superseder is healthy and dirty work is covered."""
    dispositions: Dict[str, BranchDisposition] = {}

    for cand in candidates:
        older = cand["older_branch"]
        newer = cand["newer_branch"]
        newer_sha = cand.get("newer_sha")
        if not newer_sha:
            try:
                newer_sha = run_git(["rev-parse", newer], cwd=root).strip()[:8]
            except Exception:
                newer_sha = newer[:8]

        if older in dispositions:
            continue

        # Invariant I5: Superseding branch must be a healthy survivor
        health = branch_health.get(newer, "HEALTHY")
        if health != "HEALTHY":
            dispositions[older] = BranchDisposition(
                branch=older,
                action="KEEP",
                reason=f"Candidate superseder {newer} has merge conflicts with main; investigate before tombstone",
                confidence="LOW",
            )
            continue

        # Check dirty state of older branch
        wt = worktree_info.get(older) if worktree_info else None
        if wt:
            if wt.get("path"):
                wt_path = Path(wt["path"])
                is_dirty_covered, _, residual = evaluate_dirty_file_coverage(
                    wt_path, newer, root=root, ignore_review_artifacts=True
                )
                if not is_dirty_covered:
                    authored = len(residual)
                    stash = wt.get("stash_count", 0)
                    dispositions[older] = BranchDisposition(
                        branch=older,
                        action="COMMIT_FIRST",
                        reason=(
                            f"superseded by {newer} but {authored} uncommitted authored file(s)"
                            f"{f' and {stash} stash entry/entries' if stash else ''}; commit before tombstone"
                        ),
                        target=newer,
                        target_sha=newer_sha,
                        confidence="HIGH",
                    )
                    continue
            elif wt.get("dirty") or wt.get("stash_count", 0) > 0 or wt.get("authored_files_count", 0) > 0:
                authored = wt.get("authored_files_count", 0)
                stash = wt.get("stash_count", 0)
                dispositions[older] = BranchDisposition(
                    branch=older,
                    action="COMMIT_FIRST",
                    reason=(
                        f"superseded by {newer} but {authored} uncommitted authored file(s)"
                        f"{f' and {stash} stash entry/entries' if stash else ''}; commit before tombstone"
                    ),
                    target=newer,
                    target_sha=newer_sha,
                    confidence="HIGH",
                )
                continue

        conf = cand.get("confidence", "HIGH")
        dispositions[older] = BranchDisposition(
            branch=older, action="TOMBSTONE", reason=f"Superseded by {newer}@{newer_sha}",
            target=newer, target_sha=newer_sha, confidence=conf,
            superseded_by={"kind": "branch", "ref": newer, "sha": newer_sha, "confidence": conf},
        )

    return dispositions


def compute_dispositions(
    matrix: TopologyMatrix,
    worktree_info: Optional[Dict[str, Any]] = None,
    waves_info: Optional[Dict[str, Any]] = None,
    branch_health: Optional[Dict[str, str]] = None,
    root: Path = ROOT,
    lifecycle_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, BranchDisposition]:
    """Computes deterministic dispositions for all branches in the topology matrix."""
    # 0. Global safety guards
    dispositions = _evaluate_guard_dispositions(matrix, worktree_info)

    # 0.5. Explicit lifecycle tombstones from registry
    for branch, t_meta in _extract_registry_tombstones(root, lifecycle_overrides).items():
        if branch in matrix.branches and branch not in dispositions:
            reason = t_meta.get("reason") or t_meta.get("rationale") or "Flagged for tombstone decommissioning in registry"
            sup_by = t_meta.get("superseded_by")
            dispositions[branch] = BranchDisposition(
                branch=branch, action="TOMBSTONE", reason=reason, target=sup_by,
                superseded_by={"ref": sup_by, "kind": "branch", "confidence": "HIGH"} if sup_by else None,
                confidence="HIGH",
            )

    base_branch = matrix.base_branch
    main_sha = run_git(["rev-parse", base_branch], cwd=root).strip()
    main_tree = run_git(["rev-parse", f"{base_branch}^{{tree}}"], cwd=root).strip()
    health_map = branch_health or {}

    # 1. Ahead==0 branches
    for branch, diff in matrix.branch_diffs.items():
        if branch in dispositions:
            continue
        if diff.ahead == 0 and not getattr(diff, "is_unknown", False):
            wt = worktree_info.get(branch) if worktree_info else None
            dispositions[branch] = _evaluate_ahead_zero_branch(
                branch, diff, wt, waves_info, root, main_sha, main_tree, base_branch, lifecycle_overrides
            )

    # 2. TOMBSTONE superseded branches
    tombstones = _evaluate_tombstone_candidates(
        matrix.superseded_candidates, worktree_info, health_map, root
    )
    for branch, disp in tombstones.items():
        if branch not in dispositions:
            dispositions[branch] = disp

    # 3. KEEP remaining active branches with unique work
    for branch in matrix.branches:
        if branch not in dispositions:
            dispositions[branch] = BranchDisposition(branch=branch, action="KEEP", reason=GENERIC_KEEP_REASON)

    return dispositions
