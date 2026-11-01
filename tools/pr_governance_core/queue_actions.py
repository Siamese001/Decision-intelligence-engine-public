"""
Explicit, operator-readable Next Action text for PR queue rows.

Replaces the generic "[KEEP] Active branch with unique work" with a concrete step.
All text is produced here (never hand-written in chat) so the table stays auditable.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from tools.pr_governance_core.common import PLAN_DOC_FILES
from tools.pr_governance_core.conflict_triage import (
    ConflictTriageResult,
    triage_branch_conflicts,
)

MAX_FILES_SHOWN = 3
_VERSION_SUFFIX_RE = re.compile(r"-v(\d+)$")


def get_conflict_files(branch: str, root: Path, base: str = "main") -> List[str]:
    """Files that would conflict when merging `branch` into `base` (git merge-tree, no worktree touched)."""
    try:
        res = subprocess.run(
            ["git", "merge-tree", "--write-tree", base, branch],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if res.returncode == 0:
        return []
    stderr = getattr(res, "stderr", "")
    if res.returncode > 1 or (isinstance(stderr, str) and "not something we can merge" in stderr):
        return ["<UNKNOWN_GIT_ERROR>"]
    stdout = getattr(res, "stdout", "")
    if not isinstance(stdout, str) or not stdout.strip():
        return []

    lines = stdout.splitlines()
    files: List[str] = []
    # Line 0 is the tree OID; conflicted paths follow until the first blank line.
    for line in lines[1:]:
        if not line.strip():
            break
        parts = line.strip().split("\t", 1)
        path = parts[1].strip() if len(parts) == 2 else parts[0].strip()
        if path and path not in files:
            files.append(path)
    return files


def sibling_stem(branch: str) -> str:
    """'feat/x-v3' -> 'feat/x' (versioned iterations share a stem)."""
    return _VERSION_SUFFIX_RE.sub("", branch)


def _version(branch: str) -> int:
    m = _VERSION_SUFFIX_RE.search(branch)
    return int(m.group(1)) if m else 1


def is_doc_only(files: Sequence[str]) -> bool:
    from tools.pr_governance_core.common import is_plan_doc_file
    return bool(files) and all(is_plan_doc_file(f) for f in files)


def find_siblings(branch: str, all_files: Dict[str, Sequence[str]]) -> List[str]:
    stem = sibling_stem(branch)
    return sorted(b for b in all_files if b != branch and sibling_stem(b) == stem)


def _short(files: Sequence[str]) -> str:
    shown = ", ".join(files[:MAX_FILES_SHOWN])
    extra = len(files) - MAX_FILES_SHOWN
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _check_doc_only_iteration(
    branch: str,
    files: Sequence[str],
    siblings: Sequence[str],
    all_files: Dict[str, Sequence[str]],
) -> Optional[str]:
    """Handles iteration series of doc-only plans editing the same file."""
    if not is_doc_only(files):
        return None
    doc_sibs = [s for s in siblings if is_doc_only(all_files.get(s, []))]
    if not doc_sibs:
        return None
    group = sorted([branch, *doc_sibs], key=_version)
    newest = group[-1]
    if branch != newest:
        return (
            f"[CONFIRM_TOMBSTONE] doc-only plan (implementation_plan.md); newer iteration {newest} "
            f"edits the same file. Do not rebase. Tombstone this branch if {newest} replaces it"
        )
    return (
        f"[RESOLVE_CONFLICTS] newest doc-only plan iteration; older: {', '.join(group[:-1])}. "
        f"Confirm older ones are tombstoned, then rebase onto main and keep only this plan"
    )


def _check_overlap_action(ctx: Dict[str, Any], branch: str) -> Optional[str]:
    """Evaluates overlap findings with strict precedence: DEDUP > REORDER > CHERRY_PICK > REBASE."""
    if ctx.get("duplicate_of"):
        dup_target = ctx["duplicate_of"]
        dup_sha = ctx.get("duplicate_sha", "")
        sha_str = f"@{dup_sha[:8]}" if dup_sha else ""
        return f"[DEDUP] identical changes to {dup_target}{sha_str}; drop or consolidate"
    if ctx.get("duplicate_blobs"):
        blobs = ctx["duplicate_blobs"]
        return f"[DEDUP] {len(blobs)} blob(s) identical to sibling {blobs[0].get('sibling', '')}; consolidate"
    if ctx.get("reorder_after"):
        return f"[REORDER] merge after {ctx['reorder_after']} to avoid contention"
    if ctx.get("cherry_pick_extract"):
        cp = ctx["cherry_pick_extract"]
        clean_cnt = cp.get("clean_count", 0)
        total_cnt = cp.get("total_count", 0)
        if 0 < clean_cnt < total_cnt:
            return f"[CHERRY_PICK] {clean_cnt}/{total_cnt} commits clean; land extract set first"
    if ctx.get("rebase_onto"):
        onto = ctx["rebase_onto"]
        return f"[REBASE] stacked branch; git rebase --onto main {onto} {branch}"
    return None


def explain_next_action(
    item: Dict[str, Any],
    conflict_files: Sequence[str] = (),
    siblings: Sequence[str] = (),
    all_files: Optional[Dict[str, Sequence[str]]] = None,
    conflict_triage: Optional[ConflictTriageResult] = None,
    overlap_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Return a concrete next-action string for a branch that the disposition engine marked KEEP.

    Strict Precedence:
      [COMMIT_FIRST] > [RESOLVE_CONFLICTS] > [DEDUP] > [REORDER] > [CHERRY_PICK] > [REBASE]
    """
    branch = item["branch"]
    all_files_map = all_files or {}
    files = list(all_files_map.get(branch, []))

    if branch.startswith("origin/"):
        local_name = branch[len("origin/"):]
        return (
            f"[CHECKOUT_OR_TRACK] Remote-only branch with no local counterpart; "
            f"checkout via `git checkout -b {local_name} {branch}`"
        )

    if branch.startswith("backup/"):
        return "[ARCHIVE] backup snapshot; no merge action. Delete only after operator confirms restore is not needed"

    # 1. [COMMIT_FIRST]
    if item.get("dirty"):
        return "[COMMIT_FIRST] uncommitted changes; commit, then rebase and run review"

    doc_action = _check_doc_only_iteration(branch, files, siblings, all_files_map)
    if doc_action:
        return doc_action

    if "<UNKNOWN_GIT_ERROR>" in conflict_files:
        return "[INVESTIGATE] merge simulation error (git merge-tree failed with error); inspect branch ref"

    # 2. [RESOLVE_CONFLICTS]
    doc_sibs = [s for s in siblings if is_doc_only(all_files_map.get(s, []))]
    code_sibs = [s for s in siblings if s not in doc_sibs]
    note = f" Contains code changes; doc-only sibling plans: {', '.join(doc_sibs)}." if doc_sibs else (
        f" Sibling code branches: {', '.join(code_sibs)}." if code_sibs else ""
    )

    if conflict_triage and conflict_triage.conflicted_files:
        return f"{conflict_triage.action_summary}.{note}" if note else conflict_triage.action_summary

    if conflict_files:
        return f"[RESOLVE_CONFLICTS] rebase onto main; {len(conflict_files)} conflicting file(s): {_short(conflict_files)}.{note}"

    # 3. [DEDUP] / [REORDER] / [CHERRY_PICK] / stacked [REBASE]
    overlap_act = _check_overlap_action(overlap_context or {}, branch)
    if overlap_act:
        return overlap_act

    if item.get("open_waves") not in (None, "—", "?", ""):
        return f"[CONTINUE_WAVES] Wave {item['open_waves']} still open; finish before review"

    abs_files = item.get("absorbed_files") or []
    salv_files = item.get("salvageable_files") or []
    if abs_files and salv_files:
        return f"[EXTRACT_AND_ARCHIVE] extract {len(salv_files)} unique file(s) ({_short(salv_files)}); {len(abs_files)} file(s) already absorbed into main ({_short(abs_files)})"

    overlap_files = item.get("overlap_files")
    behind = int(item.get("behind", 0))
    if behind > 0:
        if overlap_files:
            return f"[REBASE] {behind} commits behind main; {len(overlap_files)} shared file(s) modified on main: {_short(overlap_files)}. Rebase onto latest main, then run review"
        return f"[REBASE] {behind} commits behind main. Stage 1–2 rebase onto latest main required before merge qualification"
    return "[REVIEW] clean and up to date; run `python tools/pr_governance.py review <branch>`"


def build_siblings_map(all_files: Dict[str, Sequence[str]]) -> Dict[str, List[str]]:
    return {b: find_siblings(b, all_files) for b in all_files}


def optional_conflicts(
    branch: str, root: Path, clean_merge: bool, base: str = "main"
) -> Optional[List[str]]:
    """Only pay for merge-tree on branches already known to conflict."""
    if clean_merge:
        return []
    return get_conflict_files(branch, root, base)


def optional_conflict_triage(
    branch: str, root: Path, clean_merge: bool, base: str = "main", behind: int = 0
) -> Optional[ConflictTriageResult]:
    """Only triage branches already known to conflict."""
    if clean_merge:
        return None
    return triage_branch_conflicts(branch, root, base=base, behind=behind)


def explain_worktree_action(item: Dict[str, Any]) -> str:  # noqa: C901
    """Computes specific Next Action text for orphan worktree directories and git stashes."""
    classification = str(item.get("classification", ""))
    live_status = str(item.get("live_meeting_status", ""))

    if classification == "RECORDING_IN_PROGRESS" or live_status == "RECORDING_IN_PROGRESS":
        return "[WAIT_RECORDING] Live recording in progress; do not commit or prune"
    if classification == "SYNTHESIS_PENDING" or live_status == "SYNTHESIS_PENDING":
        audio_n = item.get("audio_chunks_count", item.get("unique_files_count", 0))
        return (
            f"[RUN_SYNTHESIS] Audio recorded ({audio_n} chunk(s)); "
            f"execute Stage U0 synthesis via nee ingest-meeting or prompt synthesis"
        )
    if classification == "STAGE_U0_READY" or live_status == "STAGE_U0_READY":
        return "[COMMIT_DELIVERABLES] Artifacts 01-03 generated; commit deliverables before branch merge"
    if classification == "ORPHAN_DELIVERABLES":
        unique = item.get("unique_files", [])
        names = [f["path"] for f in unique[:2]]
        s = ", ".join(names)
        if len(unique) > 2:
            s += f" (+{len(unique) - 2} more)"
        return f"[RECOVER_DELIVERABLES] {len(unique)} files only here ({s}); recover to feat/live-<slug>"
    if classification in ("ORPHAN_AUDIO_ONLY", "ORPHANED_AUDIO") or live_status == "ORPHANED_AUDIO":
        n = item.get("audio_chunks_count", item.get("unique_files_count", 0))
        return f"[QUARANTINE_AUDIO] Audio chunks only ({n} files); safe to quarantine or ingest"
    if classification in ("ORPHAN_STUB", "ABANDONED_STUB") or live_status == "ABANDONED_STUB":
        n = item.get("unique_files_count", 0)
        return f"[QUARANTINE_STUB] Empty session stub ({n} files); safe to quarantine"
    if classification == "STALE_REGISTRATION":
        return "[REPAIR_REGISTRATION] Stale git registration; run git worktree prune"
    if classification == "NON_DIRECTORY":
        return "[REMOVE_STRAY] Non-directory file in .worktrees/ container; remove file"
    if classification.startswith("STASH_"):
        ref = item.get("stash_ref", item.get("branch", "stash"))
        subj = item.get("subject", "")
        if classification == "STASH_DELETED_BRANCH":
            return f"[TRIAGE_STASH] {ref}: branch deleted; pop or drop ({subj[:30]})"
        if classification == "STASH_MAIN":
            return f"[TRIAGE_STASH] {ref}: on main; review and apply or drop ({subj[:30]})"
        return f"[TRIAGE_STASH] {ref}: live branch active ({subj[:30]})"
    return "[AUDIT_WORKTREE] Inspect worktree directory"


def explain_relative_order(
    item: Dict[str, Any],
    prev_item: Optional[Dict[str, Any]] = None,
    next_item: Optional[Dict[str, Any]] = None,
) -> str:
    """Answers: Why this position relative to alternatives in the queue?"""
    tier = str(item.get("tier", "—"))
    sub_rank = str(item.get("sub_rank", "—"))
    branch = str(item.get("branch", ""))

    if prev_item is None:
        return f"Ranked #{sub_rank}: Top priority candidate in queue ({tier} tier)"

    prev_tier = str(prev_item.get("tier", "—"))
    prev_rank = str(prev_item.get("sub_rank", "—"))

    # Different tier group
    if tier != prev_tier:
        return f"Below {prev_rank}: lower tier priority ({tier} vs {prev_tier})"

    # Same tier: compare risk score
    item_risk = int(item.get("risk_score", 0))
    prev_risk = int(prev_item.get("risk_score", 0))
    if item_risk > prev_risk:
        behind = int(item.get("behind", 0))
        prev_behind = int(prev_item.get("behind", 0))
        behind_note = f" (behind {behind} vs {prev_behind})" if behind != prev_behind else ""
        return f"Below {prev_rank}: higher risk penalty{behind_note} ({item_risk} vs {prev_risk})"

    # Compare value score
    item_val = float(item.get("value_score", 0.0))
    prev_val = float(prev_item.get("value_score", 0.0))
    if item_val < prev_val:
        return f"Below {prev_rank}: lower structural score ({item_val:.1f} vs {prev_val:.1f})"

    # Tie-break
    return f"Below {prev_rank}: same {tier}, risk, value; branch-name tie-break"


def explain_blocker_and_evidence_requirement(item: Dict[str, Any]) -> Tuple[str, str]:
    """Answers: What blocks it? What evidence is required before merge?"""
    eligibility = str(item.get("merge_eligibility", "ELIGIBLE"))
    behind = int(item.get("behind", 0))
    clean = bool(item.get("clean_merge", True))
    dirty = bool(item.get("dirty", False))
    ahead = int(item.get("ahead", 0))
    open_w = str(item.get("open_waves", "—"))
    prereqs = item.get("hard_prerequisites", [])

    blockers: List[str] = []
    if eligibility == "BLOCKED_PREREQUISITE" or prereqs:
        blockers.append(f"Blocked by unmerged prerequisite: {', '.join(prereqs)}")
    if eligibility == "BLOCKED_CYCLE":
        blockers.append("Circular dependency cycle detected")
    if not clean or eligibility == "BLOCKED_CONFLICT":
        blockers.append("Merge conflicts vs main")
    if ahead == 0 or eligibility == "BLOCKED_AUTHORING":
        blockers.append("Zero authored commits (inception/authoring)")
    if dirty or eligibility == "BLOCKED_WIP":
        blockers.append("Uncommitted changes in worktree (commit first)")
    if behind >= 20:
        blockers.append(f"Severely diverged (behind {behind} commits)")
    elif behind > 5:
        blockers.append(f"Rebase needed (behind {behind} commits)")
    if open_w not in ("—", "?", ""):
        blockers.append(f"Open waves [{open_w}] incomplete")

    blocker_str = "; ".join(blockers) if blockers else "None (clean)"

    req = item.get("completion_evidence_requirement")
    if not req:
        if not clean:
            req = "Resolve conflicts and verify clean git merge-tree simulation"
        elif ahead == 0:
            req = "Commit initial implementation and wave plan"
        elif open_w not in ("—", "?", ""):
            req = f"Complete open wave(s) [{open_w}] and seal wave runtime receipts"
        elif behind > 5:
            req = "Rebase onto main and re-run Tier 2 readiness verification"
        else:
            req = "Passing Tier 2 readiness receipt and wave plan completion"

    return blocker_str, req

