"""
tools/pr_governance_core/detail_artifact.py

Generates and manages the complete linked detail artifact for PR queue governance.
Ensures every emitted detail link exists on disk with complete progress, review verdicts,
sync status, worktree coverage, and stable git stash identities.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def generate_pr_queue_detail_artifact(
    repo_root: Path,
    global_queue: List[Dict[str, Any]],
    inv: Optional[Dict[str, Any]] = None,
    target_path: Optional[Path] = None,
) -> Path:
    """Writes a complete linked detail artifact to disk and returns its Path."""
    if target_path is None:
        target_path = repo_root / "reports" / "pr_queue_detail.json"

    target_path.parent.mkdir(parents=True, exist_ok=True)

    from .completion_state import evaluate_branch_completion_state, summarize_queue_completion_split

    branches_detail: List[Dict[str, Any]] = []
    stashes_detail: List[Dict[str, Any]] = []

    for item in global_queue:
        branch = item.get("branch", "")
        tier = item.get("tier", "")
        if tier == "W" and branch.startswith("stash@{"):
            stashes_detail.append({
                "ref": branch,
                "tier": tier,
                "sub_rank": item.get("sub_rank", "—"),
                "classification": item.get("classification", "STASH"),
                "subject": item.get("review_note") or item.get("domain_impact", ""),
                "next_action": item.get("next_action", "—"),
                "ranking_rationale": item.get("ranking_rationale", "—"),
            })
        else:
            val_stmt = item.get("value_statement") or "UNKNOWN: Unmeasured business value (structural heuristics only)"
            req_str = item.get("completion_evidence_requirement") or "Passing Tier 2 readiness receipt and wave plan completion"
            blocker_str = item.get("blocker", "None (clean)")
            act_state = evaluate_branch_completion_state(item)
            branches_detail.append({
                "branch": branch,
                "tier": tier,
                "sub_rank": item.get("sub_rank", "—"),
                "domain": item.get("domain", "—"),
                "readiness": item.get("readiness", "—"),
                "review_verdict": item.get("review_verdict", "—"),
                "rebase_effort": item.get("rebase_effort", "NONE"),
                "ahead": item.get("ahead", 0),
                "behind": item.get("behind", 0),
                "clean_merge": item.get("clean_merge", True),
                "dirty": item.get("dirty", False),
                "completed_waves": item.get("completed_waves", "—"),
                "open_waves": item.get("open_waves", "—"),
                "completion_state": act_state.completion_state,
                "is_completed": act_state.is_completed,
                "is_in_progress": act_state.is_in_progress,
                "in_progress_reasons": act_state.in_progress_reasons,
                "next_action": item.get("next_action", "—"),
                "ranking_rationale": item.get("ranking_rationale", "—"),
                "blocker": blocker_str,
                "value_statement": val_stmt,
                "completion_evidence_requirement": req_str,
                "outcome_metadata": item.get("outcome_metadata"),
                "verdict_verification": item.get("verdict_verification"),
                "requires_hitl_clarification": bool(item.get("requires_hitl_clarification", False)),
                "hitl_question_payload": item.get("hitl_question_payload"),
                "github_pr": item.get("github_pr"),
            })

    if inv and "stashes" in inv:
        raw_stashes: List[Dict[str, Any]] = []
        for s in inv["stashes"]:
            raw_stashes.append({
                "ref": s.get("ref", ""),
                "head_sha": s.get("head_sha", s.get("sha", "")),
                "parent_sha": s.get("parent_sha", ""),
                "date": s.get("date", ""),
                "classification": s.get("classification", ""),
                "branch": s.get("branch", ""),
                "subject": s.get("subject", ""),
            })
        if raw_stashes:
            stashes_detail = raw_stashes

    coverage_summary = inv.get("counts") if inv else {
        "candidate_branches": len(branches_detail),
        "stashes_total": len(stashes_detail),
    }

    split = summarize_queue_completion_split(global_queue)
    payload = {
        "artifact_version": "1.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "total_entries": len(global_queue),
        "candidate_branch_count": len(branches_detail),
        "completed_branch_count": split["completed_branch_count"],
        "in_progress_branch_count": split["in_progress_branch_count"],
        "decommission_branch_count": split["decommission_branch_count"],
        "completed_branches": split["completed_branches"],
        "in_progress_branches": split["in_progress_branches"],
        "decommission_branches": split["decommission_branches"],
        "collapsed_stash_count": len(stashes_detail),
        "coverage_summary": coverage_summary,
        "branches": branches_detail,
        "stashes": stashes_detail,
    }

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return target_path
