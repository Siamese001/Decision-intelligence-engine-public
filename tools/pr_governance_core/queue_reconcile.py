"""
Queue row reconciliation (PRQ hardening, Wave 3).

The PR queue joins four independent engines (priority, disposition, code review, readiness).
This module is the single place where their outputs are forced to agree before sorting:

  * tier follows the disposition decision (PRUNE/TOMBSTONE -> D, ARCHIVE -> A, KEEP/COMMIT_FIRST never D),
  * the review verdict reflects mutable worktree state (ahead==0 -> '—', dirty -> never MERGE_READY),
  * SUPERSEDED_TOMBSTONE only stands when cherry evidence proves the production work is already in main,
  * risk tiers honour Fast-Lane and blocking review verdicts (rule §1 risk-adjusted sub-ordering).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

NO_OPEN_WAVES = ("—",)
BLOCKING_VERDICTS = ("GOVERNANCE_BLOCK", "SPLIT_REQUIRED", "REBASE_RETARGET")


@dataclass
class ReviewReconciliation:
    verdict: str
    tombstone_evidence: bool = False
    note: str = ""


def tier_from_disposition(tier: str, action: Optional[str], domain_fallback: str = "P3.1") -> str:
    """One decision for tier and Next Action (committee C5/C6)."""
    if action in ("PRUNE", "TOMBSTONE"):
        return "D.1"
    if action == "ARCHIVE":
        return "A.1"
    if action in ("KEEP", "COMMIT_FIRST", "CONSOLIDATE", "SALVAGE_DELIVERABLES") and str(tier).startswith(("D", "A", "COMMIT_FIRST")):
        return domain_fallback
    return tier


def reconcile_review(verdict: str, ahead: int, dirty: bool, signals: Optional[Dict[str, Any]]) -> ReviewReconciliation:
    """Apply post-cache worktree state to the cached, SHA-keyed review verdict."""
    sig = signals or {}
    hunk_ratio = float(sig.get("hunk_absorption_ratio", 0.0) or 0.0)
    has_hunks = bool(sig.get("file_hunks"))
    if ahead <= 0 and not (dirty and (has_hunks or hunk_ratio > 0.0 or verdict == "SUPERSEDED_TOMBSTONE")):
        return ReviewReconciliation("—", note="no commits ahead of main; nothing to review")
    if verdict == "SUPERSEDED_TOMBSTONE":
        cherry_proven = int(sig.get("commits_in_main", 0)) > 0 and int(sig.get("unique_production_commits", 0)) == 0
        hunk_proven = hunk_ratio >= 0.85
        if not (cherry_proven or hunk_proven):
            return ReviewReconciliation("REVIEW_READY", note="tombstone verdict lacked main-side cherry or hunk evidence")
        note = f"hunk absorption {int(hunk_ratio * 100)}%" if hunk_proven else "cherry commits absorbed"
        return ReviewReconciliation(verdict, tombstone_evidence=True, note=note)
    if ahead <= 0 and dirty and 0.25 <= hunk_ratio < 0.85:
        pct = int(round(hunk_ratio * 100))
        return ReviewReconciliation("REVIEW_READY", note=f"dirty worktree partial hunk absorption ({pct}% in main)")
    if ahead <= 0:
        return ReviewReconciliation("—", note="no commits ahead of main; nothing to review")
    if dirty and verdict == "MERGE_READY":
        return ReviewReconciliation("REVIEW_READY", note="uncommitted worktree changes")
    return ReviewReconciliation(verdict)


def tombstone_next_action(signals: Optional[Dict[str, Any]]) -> str:
    sig = signals or {}
    hunk_ratio = float(sig.get("hunk_absorption_ratio", 0.0) or 0.0)
    if hunk_ratio >= 0.85:
        pct = int(hunk_ratio * 100)
        return f"[CONFIRM_TOMBSTONE] {pct}% of code hunks already absorbed into main; confirm before decommission"
    return (
        f"[CONFIRM_TOMBSTONE] {sig.get('commits_in_main', 0)} commit(s) already in main (git cherry); "
        f"{sig.get('unique_commits', 0)} remaining non-production commit(s). Confirm before decommission"
    )


def compute_risk_tier(tier: str, info: Dict[str, Any], dirty: bool, open_waves: str, verdict: str) -> int:
    """0 Fast-Lane · 1 Low · 2 Moderate · 3 High (conflict / blocking verdict) · 4 Diverged · 5 decommission."""
    if str(tier).startswith(("D", "A")):
        return 5
    raw_behind = info.get("behind", 0)
    behind = int(raw_behind) if raw_behind is not None else 10**6
    clean = bool(info.get("clean_merge"))
    if behind >= 20:
        return 4
    if not clean or verdict in BLOCKING_VERDICTS:
        return 3
    if open_waves in NO_OPEN_WAVES and behind <= 5 and not dirty:
        return 0
    if behind <= 10 and not dirty:
        return 1
    return 2
