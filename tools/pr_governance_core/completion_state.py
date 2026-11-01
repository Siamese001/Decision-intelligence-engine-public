"""tools/pr_governance_core/completion_state.py

Deterministic Branch Completion vs. In-Progress Activity Classifier.
Separates candidate branches into three mutually exclusive operational states:
  1. COMPLETED ("COMPLETED" / Code Completed? == "Y" / completion_rank == 0):
     Clean working tree, all declared waves complete, ahead > 0, no active git lock
     or live OS process mutating the worktree.
  2. IN_PROGRESS ("IN_PROGRESS" / Code Completed? == "N" / completion_rank == 1):
     Active work in flight (uncommitted files, open waves, zero-commit authoring,
     active git lock / mid-rebase, or active OS process / recent worktree edit).
  3. DECOMMISSION ("DECOMMISSION" / Code Completed? == "N/A" / completion_rank == 2):
     Superseded, empty stub, abandoned zero-commit ancestor, or worktree/stash row.
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .common import get_immune_pids, run_git


@dataclass
class BranchActivityAssessment:
    branch: str
    completion_state: str  # "COMPLETED", "IN_PROGRESS", "DECOMMISSION"
    is_completed: bool
    is_in_progress: bool
    code_completed_flag: str  # "Y", "N", "N/A"
    completion_rank: int  # 0 = COMPLETED, 1 = IN_PROGRESS, 2 = DECOMMISSION
    status_badge: str
    in_progress_reasons: List[str] = field(default_factory=list)
    active_pids: List[int] = field(default_factory=list)
    has_git_lock: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _resolve_wt_path(item: Dict[str, Any]) -> Optional[Path]:
    wt_str = item.get("worktree_path")
    if not wt_str:
        wt = item.get("wt")
        if isinstance(wt, dict):
            wt_str = wt.get("path")
    if not wt_str:
        return None
    p = Path(str(wt_str))
    return p if p.is_dir() else None


def detect_git_worktree_lock(wt_path: Optional[Path]) -> bool:
    """Return True if the worktree's gitdir has an active index.lock, rebase, or merge marker."""
    if not wt_path:
        return False
    markers = ("index.lock", "HEAD.lock", "rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD")
    dot_git = wt_path / ".git"
    if dot_git.is_dir() and any((dot_git / m).exists() for m in markers):
        return True
    if dot_git.is_file():
        try:
            raw = dot_git.read_text(encoding="utf-8", errors="replace").strip()
            if raw.startswith("gitdir:"):
                g_str = raw.split("gitdir:", 1)[1].strip()
                g_dir = Path(g_str) if Path(g_str).is_absolute() else (wt_path / g_str).resolve()
                if g_dir.is_dir() and any((g_dir / m).exists() for m in markers):
                    return True
        except Exception:
            pass
    try:
        git_dir_out = run_git(["rev-parse", "--git-dir"], cwd=wt_path, check=False).strip()
        if not git_dir_out:
            return False
        git_dir = Path(git_dir_out) if Path(git_dir_out).is_absolute() else (wt_path / git_dir_out)
        return any((git_dir / m).exists() for m in markers)
    except Exception:
        return False


def detect_active_worktree_pids(wt_path: Optional[Path], probe_os: bool = False) -> List[int]:
    """Return non-immune non-daemon OS PIDs holding open descriptors in wt_path when probe_os is enabled."""
    if not wt_path or not probe_os:
        return []
    try:
        proc = subprocess.run(
            ["lsof", "-t", "+D", str(wt_path)],
            capture_output=True, text=True, check=False, timeout=2.0,
        )
        if not proc.stdout.strip():
            return []
        immune = get_immune_pids()
        pids = sorted({int(ln.strip()) for ln in proc.stdout.splitlines() if ln.strip().isdigit() and int(ln.strip()) not in immune})
        return pids
    except Exception:
        return []


def _is_decommission_row(item: Dict[str, Any]) -> bool:
    tier = str(item.get("tier") or "")
    branch = str(item.get("branch") or "")
    next_act = str(item.get("next_action") or "")
    diff_cat = str(item.get("diff_category") or "")
    disp = str(item.get("recommended_disposition") or item.get("disposition") or "")
    is_docs = bool(item.get("is_docs_only_plan", False))
    behind = int(item.get("behind") or 0)
    ahead = int(item.get("ahead") or 0)
    dirty = bool(item.get("dirty", False))
    open_w = str(item.get("open_waves") or "—").strip()
    absorbed = item.get("absorbed_files") or []
    salvageable = item.get("salvageable_files") or []

    if tier.startswith(("W", "D", "TOMBSTONE", "A")) or branch.startswith("stash@{"):
        return True
    if disp == "Decommission (No Value)" or "[PRUNE]" in next_act or "[TOMBSTONE]" in next_act:
        return True
    if (diff_cat == "DOCS_ONLY_UNSTARTED" or is_docs) and behind >= 20 and not dirty:
        return True
    if diff_cat == "EMPTY_STUB" and not dirty and open_w in ("—", "?", "", "None"):
        return True
    if (diff_cat == "SUPERSEDED_CODE" or (absorbed and not salvageable)) and not dirty:
        return True
    if ahead == 0 and not dirty and open_w in ("—", "?", "", "None"):
        return True
    return False


def _collect_in_progress_reasons(
    item: Dict[str, Any], wt_path: Optional[Path], probe_os: bool
) -> tuple[List[str], List[int], bool]:
    reasons: List[str] = []
    dirty = bool(item.get("dirty", False))
    open_w = str(item.get("open_waves") or "—").strip()
    ahead_raw = item.get("ahead")
    ahead = int(ahead_raw) if isinstance(ahead_raw, int) else 0
    next_act = str(item.get("next_action") or "")

    has_lock = bool(item.get("mid_rebase", False) or item.get("has_git_lock", False) or detect_git_worktree_lock(wt_path))
    explicit_pids = item.get("active_pids")
    pids = [int(p) for p in explicit_pids] if isinstance(explicit_pids, list) else detect_active_worktree_pids(wt_path, probe_os=probe_os)

    if pids:
        reasons.append(f"active_process_pids:{','.join(map(str, pids[:3]))}")
    if has_lock:
        reasons.append("active_git_lock_or_rebase")
    if dirty or "[COMMIT_FIRST]" in next_act:
        reasons.append("uncommitted_changes")
    if open_w not in ("—", "", "None"):
        if open_w == "?":
            if ahead > 0:
                reasons.append("missing_wave_plan")
        else:
            reasons.append(f"open_waves:{open_w}")
    if ahead == 0 and (dirty or open_w not in ("—", "?", "", "None")):
        reasons.append("authoring_inception")
    if item.get("action") == "SALVAGE_DELIVERABLES" and dirty:
        reasons.append("uncommitted_salvage_deliverables")
    return reasons, pids, has_lock


def _badge_for_reasons(reasons: List[str]) -> str:
    if any(r.startswith("active_process_pids") or r == "active_git_lock_or_rebase" for r in reasons):
        return "[IN PROGRESS — ACTIVE SESSION]"
    if "uncommitted_changes" in reasons or "authoring_inception" in reasons:
        return "[IN PROGRESS — UNCOMMITTED EDITS]"
    if any(r.startswith("open_waves:") or r == "missing_wave_plan" for r in reasons):
        return "[IN PROGRESS — OPEN WAVES]"
    return "[IN PROGRESS]"


def evaluate_branch_completion_state(item: Dict[str, Any], probe_os: bool = False) -> BranchActivityAssessment:
    """Deterministically classifies a branch row into COMPLETED, IN_PROGRESS, or DECOMMISSION."""
    branch = str(item.get("branch") or "UNKNOWN")
    if _is_decommission_row(item):
        return BranchActivityAssessment(
            branch=branch,
            completion_state="DECOMMISSION",
            is_completed=False,
            is_in_progress=False,
            code_completed_flag="N/A",
            completion_rank=2,
            status_badge="[DECOMMISSION]",
        )

    wt_path = _resolve_wt_path(item)
    reasons, pids, has_lock = _collect_in_progress_reasons(item, wt_path, probe_os=probe_os)
    if reasons:
        return BranchActivityAssessment(
            branch=branch,
            completion_state="IN_PROGRESS",
            is_completed=False,
            is_in_progress=True,
            code_completed_flag="N",
            completion_rank=1,
            status_badge=_badge_for_reasons(reasons),
            in_progress_reasons=reasons,
            active_pids=pids,
            has_git_lock=has_lock,
        )

    return BranchActivityAssessment(
        branch=branch,
        completion_state="COMPLETED",
        is_completed=True,
        is_in_progress=False,
        code_completed_flag="Y",
        completion_rank=0,
        status_badge="[COMPLETED — READY]",
    )


def prefix_action_with_completion_badge(action: str, assessment: BranchActivityAssessment) -> str:
    """Prefixes an action string with the branch's completion vs. in-progress badge if not already present."""
    clean = (action or "").strip()
    if assessment.completion_state == "DECOMMISSION":
        return clean
    if clean.startswith(("[COMPLETED", "[IN PROGRESS")):
        return clean
    return f"{assessment.status_badge} {clean}" if clean else assessment.status_badge


def summarize_queue_completion_split(items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes aggregate counts and branch lists for Completed vs. In-Progress vs. Decommission items."""
    completed: List[str] = []
    in_progress: List[str] = []
    decommission: List[str] = []

    for it in items:
        tier = str(it.get("tier") or "")
        branch = str(it.get("branch") or "")
        if tier in ("W", "MERGED", "SUMMARY") or branch.startswith("stash@{"):
            continue
        state = evaluate_branch_completion_state(it)
        if state.completion_state == "COMPLETED":
            completed.append(branch)
        elif state.completion_state == "IN_PROGRESS":
            in_progress.append(branch)
        else:
            decommission.append(branch)

    return {
        "completed_branch_count": len(completed),
        "in_progress_branch_count": len(in_progress),
        "decommission_branch_count": len(decommission),
        "completed_branches": completed,
        "in_progress_branches": in_progress,
        "decommission_branches": decommission,
    }
