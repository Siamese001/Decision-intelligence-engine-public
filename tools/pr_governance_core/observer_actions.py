"""Concrete first steps for the observation-only PR queue."""
from __future__ import annotations

from typing import Any, Dict, Optional


def _open_waves(item: Dict[str, Any]) -> str:
    waves = str(item.get("open_waves", "") or "").strip()
    return waves if waves not in {"—", "?", "UNKNOWN", "N/A"} else ""


def observer_next_action(item: Dict[str, Any], dependency_issue: Optional[str] = None) -> str:
    """Recommend one immediate action; eligibility remains a separate fact."""
    branch = str(item.get("branch", "UNKNOWN"))
    tier = str(item.get("tier", ""))
    waves = _open_waves(item)
    if tier.startswith("W"):
        return str(item.get("next_action", "[INVESTIGATE] Inspect preserved worktree or stash"))
    if tier.startswith(("D", "TOMBSTONE")):
        if waves:
            return f"[CONFIRM_TOMBSTONE] Inspect open waves {waves} and confirm unique work before decommissioning {branch}"
        return f"[CONFIRM_TOMBSTONE] Verify no unique work remains before decommissioning {branch}"
    ahead, clean = item.get("ahead"), item.get("clean_merge")
    if ahead is None or clean is None:
        return f"[INVESTIGATE] Verify branch ref and merge simulation against main for {branch}"
    if item.get("action") == "SALVAGE_DELIVERABLES" or item.get("disposition_action") == "SALVAGE_DELIVERABLES":
        salvageable_files = item.get("salvageable_files") or []
        count_sal = len(salvageable_files)
        if count_sal:
            return f"[SALVAGE_DELIVERABLES] Extract {count_sal} unique authored file(s) and decommission {branch}"
        return f"[SALVAGE_DELIVERABLES] Extract unique authored deliverables and decommission {branch}"
    if item.get("dirty"):
        behind_val = item.get("behind", 0) if isinstance(item.get("behind"), int) else 0
        if ahead == 0 and behind_val >= 20:
            dirty_files = item.get("dirty_files") or []
            from tools.pr_governance_core.diff_semantics import is_code_file
            if any(is_code_file(f) for f in dirty_files):
                return f"[SALVAGE_DELIVERABLES] Stale workspace ({behind_val} commits behind); extract unique changes to prevent mainline code reversion"
        followup = "; then finish open waves before review" if waves else "; then recheck merge readiness"
        return f"[COMMIT_FIRST] Commit authored changes on {branch}{followup}"
    if dependency_issue:
        if "cycle" in dependency_issue.lower():
            return f"[RESOLVE_DEPENDENCY] Untangle the hard dependency cycle involving {branch}"
        return f"[RESOLVE_DEPENDENCY] Resolve the unmerged prerequisite for {branch}: {dependency_issue}"
    if clean is False:
        return f"[RESOLVE_CONFLICTS] Rebase {branch} onto main and resolve merge conflicts"
    diff_cat = str(item.get("diff_category") or "")
    is_docs_plan = bool(item.get("is_docs_only_plan", False))
    behind = item.get("behind")
    behind_val = behind if isinstance(behind, int) else 0

    if (diff_cat == "DOCS_ONLY_UNSTARTED" and behind_val >= 20) or (is_docs_plan and behind_val >= 20):
        return f"[CONFIRM_TOMBSTONE] Verify whether unstarted planning doc on {branch} ({behind_val} commits behind) is superseded before decommissioning"
    if diff_cat == "EMPTY_STUB" and (ahead or 0) > 0:
        return f"[CONFIRM_TOMBSTONE] Verify no unique work remains before decommissioning empty stub {branch}"
    if waves:
        return f"[CONTINUE_WAVES] Finish waves {waves} on {branch} before review"
    if ahead == 0:
        return f"[INVESTIGATE] Confirm intended work on {branch} before review or decommission"
    behind = item.get("behind")
    if behind is None:
        return f"[INVESTIGATE] Verify divergence from main for {branch}"

    overlap_files = item.get("overlap_files")
    absorbed_files = item.get("absorbed_files") or []
    salvageable_files = item.get("salvageable_files") or []

    if diff_cat == "SUPERSEDED_CODE" or (absorbed_files and not salvageable_files):
        count_abs = len(absorbed_files)
        abs_names = ", ".join(absorbed_files[:2])
        if count_abs > 2:
            abs_names += f" (+{count_abs - 2} more)"
        abs_detail = f" ({count_abs} file(s) absorbed: {abs_names})" if count_abs else ""
        return f"[CONFIRM_TOMBSTONE] Verify no unique work remains before decommissioning {branch}{abs_detail}"

    if absorbed_files and salvageable_files:
        salv_names = ", ".join(salvageable_files[:2])
        if len(salvageable_files) > 2:
            salv_names += f" (+{len(salvageable_files) - 2} more)"
        abs_names = ", ".join(absorbed_files[:2])
        if len(absorbed_files) > 2:
            abs_names += f" (+{len(absorbed_files) - 2} more)"
        return f"[EXTRACT_AND_ARCHIVE] Extract unique files ({salv_names}) from {branch}; {len(absorbed_files)} file(s) already absorbed into main ({abs_names})"

    if overlap_files:
        o_count = len(overlap_files)
        short_overlap = ", ".join(overlap_files[:2])
        if o_count > 2:
            short_overlap += f" (+{o_count - 2} more)"
        return f"[REBASE] Rebase {branch} onto main ({behind_val} commits behind, {o_count} shared file(s) changed on main: {short_overlap}), then run review"

    if behind_val >= 20:
        return f"[REBASE] Rebase {branch} onto main ({behind_val} commits behind), then run review"

    if behind_val > 0 and overlap_files is not None and len(overlap_files) == 0:
        if item.get("readiness_receipt_pass") is not True:
            return f"[REVIEW] Run current-revision readiness review for {branch} (0 overlapping files with main, {behind_val} commits behind; eligible for Merge + PR)"
        return f"[MERGE] Merge + PR for {branch} (0 overlapping files with main, {behind_val} commits behind)"

    if behind_val > 0:
        return f"[REBASE] Rebase {branch} onto main ({behind_val} commits behind), then run review"

    if item.get("readiness_receipt_pass") is not True:
        return f"[REVIEW] Run current-revision readiness review for {branch}"
    return f"[MERGE] Use governed PR merge for {branch}"
