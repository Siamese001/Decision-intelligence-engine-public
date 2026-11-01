"""Wave status extraction for the PR queue (Completed Waves / Open Waves columns).

Reads wave status live from the branch's own plan documents on every invocation:
  1. Branch-local plan files in the branch worktree (committed, modified, untracked).
  2. Plan files changed by the branch vs main (*plan*.md / docs/plans, plus root implementation_plan.md
     only if the branch itself modified it, since every branch inherits main's copy).
  3. Status is detected in ANY table cell of a Wave N row (OPEN beats completion words in descriptions).
  4. Two plans' Wave 1 records do not collide; reopening remains visible.
Returns ("?", "?") only when no plan document with waves exists (unknown, never silently —).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_WAVE_RE = re.compile(r"\bWave\s*(\d+)\b", re.IGNORECASE)
_OPEN = ("⏳", "🚫", "open", "blocked", "not_started", "not started", "in progress", "pending", "incomplete", "reopened", "reopen")
_DONE = ("✅", "complete", "completed", "done", "pass", "passed")


def _status_of(text: str) -> str | None:
    t = text.lower()
    # Explicit OPEN tokens always beat completion words
    if any(m in t for m in _OPEN):
        return "open"
    if any(m in t for m in _DONE) and not any(m in t for m in ("not complete", "incomplete", "not done")):
        return "done"
    return None


def _status_of_row(cells: List[str]) -> str | None:
    # 1. Any cell with an explicit OPEN indicator beats completion words in other cells
    for cell in cells:
        if _status_of(cell) == "open":
            return "open"
    # 2. Check for explicit DONE indicator
    for cell in cells:
        if _status_of(cell) == "done":
            return "done"
    return None


def _parse_table_line(stripped: str) -> Optional[Tuple[int, str]]:
    if not stripped.startswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if not cells:
        return None
    m = _WAVE_RE.search(cells[0])
    if not m and re.fullmatch(r"\d+", cells[0]) and len(cells) > 2:
        m = re.match(r"(\d+)", cells[0])
    if not m:
        return None
    wave_num = int(m.group(1))
    status = _status_of_row(cells[1:]) or "open"
    return wave_num, status


def _parse_header_or_checkbox_line(stripped: str) -> Optional[Tuple[int, str]]:
    is_header = bool(re.match(r"^#{1,6}\s", stripped))
    is_checkbox = bool(re.match(r"^[-*]\s*\[[ xX]\]", stripped))
    if not (is_header or is_checkbox):
        return None
    m = _WAVE_RE.search(stripped)
    if not m:
        return None
    n = int(m.group(1))
    if re.match(r"^[-*]\s*\[[xX]\]", stripped):
        return n, "done"
    return n, (_status_of(stripped) or "open")


def parse_plan_text(text: str) -> Dict[int, str]:
    """Return {wave_number: 'done'|'open'} from a plan document."""
    waves: Dict[int, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        parsed = _parse_table_line(stripped) or _parse_header_or_checkbox_line(stripped)
        if parsed:
            wave_num, status = parsed
            if wave_num in waves and waves[wave_num] == "done" and status == "open" and not _status_of(stripped):
                continue
            waves[wave_num] = status
    return waves


def candidate_plan_paths(changed_files: List[str], branch: str | None = None) -> List[str]:
    plans = [
        f for f in changed_files
        if f.endswith(".md") and ("plan" in f.lower()) and not f.startswith(("output/", ".agents/", "tests/"))
    ]
    if branch:
        from tools.pr_governance_core.common import slugify_branch
        slug = slugify_branch(branch)
        branch_plan = f"plans/{slug}/implementation_plan.md"
        if branch_plan in changed_files and branch_plan not in plans:
            plans.append(branch_plan)
    if "implementation_plan.md" in changed_files and "implementation_plan.md" not in plans:
        plans.append("implementation_plan.md")
    return sorted(plans)


def _merge_waves(target: Dict[int, str], source: Dict[int, str]) -> None:
    for n, st in source.items():
        if st == "open" or target.get(n) != "done":
            target[n] = st


def _collect_waves_from_paths(
    branch: str,
    show: Callable[[str, str], str],
    paths: List[str],
    stop_on_first: bool = False,
) -> Dict[int, str]:
    merged: Dict[int, str] = {}
    for path in paths:
        waves = parse_plan_text(show(branch, path) or "")
        if waves:
            _merge_waves(merged, waves)
            if stop_on_first:
                break
    return merged


def _check_untracked_plans(wt_path: Path, branch: Optional[str] = None) -> Dict[int, str]:
    import subprocess
    from tools.pr_governance_core.common import slugify_branch

    slug = slugify_branch(branch) if branch else ""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode != 0:
            return {}
        for line in res.stdout.splitlines():
            if not line.strip():
                continue
            fpath_rel = line[3:].strip()
            if "plan" in fpath_rel.lower() and fpath_rel.endswith(".md") and not fpath_rel.startswith(("output/", "inputs/", "input/")):
                if branch and not (fpath_rel == "implementation_plan.md" or (slug and slug in fpath_rel.lower())):
                    continue
                full_p = wt_path / fpath_rel
                if full_p.is_file():
                    waves = parse_plan_text(full_p.read_text(encoding="utf-8", errors="replace"))
                    if waves:
                        return waves
    except Exception:
        pass
    return {}


def _check_scoped_plan(wt_path: Path, slug: str) -> Dict[int, str]:
    candidates = [
        wt_path / f"plans/{slug}/implementation_plan.md",
        wt_path / f"docs/plans/{slug}.md",
        wt_path / f"plans/{slug}.md",
    ]
    for scoped_p in candidates:
        if scoped_p.is_file():
            try:
                waves = parse_plan_text(scoped_p.read_text(encoding="utf-8", errors="replace"))
                if waves:
                    return waves
            except Exception:
                pass
    return {}


def _apply_wave_receipts(wt_path: Path, waves: Dict[int, str], branch: Optional[str] = None) -> None:
    rep_dir = wt_path / "reports"
    if not rep_dir.is_dir() or not waves:
        return
    import json

    for rf in rep_dir.glob("wave*.json"):
        try:
            data = json.loads(rf.read_text(encoding="utf-8", errors="ignore"))
            r_branch = data.get("branch")
            if branch and r_branch and r_branch != branch:
                continue
            w_num = data.get("wave")
            if isinstance(w_num, int) and w_num in waves and (
                data.get("status") in ("COMPLETE", "PASS")
                or data.get("terminal_outcome") in ("PASS", "PACKAGE_READY")
            ):
                waves[w_num] = "done"
        except Exception:
            pass


def _discover_worktree_plans(branch: str, wt_path: Optional[Path]) -> Dict[int, str]:
    if not wt_path or not wt_path.exists():
        return {}
    from tools.pr_governance_core.common import slugify_branch

    waves = _check_untracked_plans(wt_path, branch=branch)
    if not waves:
        slug = slugify_branch(branch)
        waves = _check_scoped_plan(wt_path, slug)

    if waves:
        _apply_wave_receipts(wt_path, waves, branch=branch)
    return waves


def _is_pure_deliverable_or_live_branch(branch: str, changed_files: List[str]) -> bool:
    """Return True when branch is a live-meeting/deliverable branch with no engineering code or plan files."""
    cleaned = [f.strip() for f in changed_files if f.strip()]
    has_plan = any("plan" in f.lower() and f.endswith(".md") and not f.startswith(("output/", "inputs/", "input/")) for f in cleaned)
    if has_plan:
        return False
    from tools.pr_governance_core.diff_semantics import is_code_file, is_deliverable_file
    has_code = any(is_code_file(f) for f in cleaned)
    if has_code:
        return False
    if cleaned and any(is_deliverable_file(f) for f in cleaned):
        return True
    b_low = (branch or "").lower()
    return b_low.startswith(("audio/", "feat/live-", "live/", "transcript/", "ingest/meeting-", "ingest/doc-"))


def compute_wave_columns(
    branch: str,
    show: Callable[[str, str], str],
    changed_files: List[str],
    wt_path: Optional[Path] = None,
) -> Tuple[str, str]:
    """Return (open_str, completed_str) for a branch; show(branch, path) returns file text or ''."""
    if _is_pure_deliverable_or_live_branch(branch, changed_files):
        from tools.pr_governance_core.common import slugify_branch
        slug = slugify_branch(branch)
        scoped = _check_scoped_plan(wt_path, slug) if wt_path else {}
        if not scoped:
            return "—", "—"

    # 1. Prefer branch-local worktree plan if available (untracked/modified or exact-slug scoped)
    merged = _discover_worktree_plans(branch, wt_path)

    # 2. Check changed files in branch diff vs main
    if not merged:
        candidates = candidate_plan_paths(changed_files, branch=branch)
        merged = _collect_waves_from_paths(branch, show, candidates)

    # 3. Fallback to exact branch-scoped plan path in git tree
    if not merged:
        from tools.pr_governance_core.common import slugify_branch
        slug = slugify_branch(branch)
        fallback_paths = [
            f"plans/{slug}/implementation_plan.md",
            f"docs/plans/{slug}.md",
            f"plans/{slug}.md",
        ]
        merged = _collect_waves_from_paths(branch, show, fallback_paths, stop_on_first=True)

    if wt_path and merged:
        _apply_wave_receipts(wt_path, merged, branch=branch)

    if not merged:
        if _is_pure_deliverable_or_live_branch(branch, changed_files):
            return "—", "—"
        return "?", "?"
    open_w = ", ".join(str(n) for n in sorted(merged) if merged[n] == "open") or "—"
    done_w = ", ".join(str(n) for n in sorted(merged) if merged[n] == "done") or "—"
    return open_w, done_w

