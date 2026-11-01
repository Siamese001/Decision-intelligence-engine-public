"""
tools/pr_governance_core/render.py

Table and diagnostic ledger rendering engine for PR queue governance.
Renders candidate feature branches and orphan worktrees according to the 7-column layout,
decision view, or wide view, with verified linked detail artifacts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import ROOT


def render_pr_queue_observer_table(
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    movements: Optional[Dict[str, str]] = None,
    recent_merges: Optional[List[Dict[str, str]]] = None,
    detail_path: Optional[Path] = None,
) -> str:
    """Render the observer's table-only output without changing legacy list views."""
    from .queue_observer_table import render_observer_table
    return render_observer_table(items, summary, movements=movements, recent_merges=recent_merges or [], detail_path=detail_path)


def format_branch_cell(
    branch: str,
    wt: Optional[Dict[str, Any]] = None,
    is_remote: bool = False,
    github_pr: Optional[Dict[str, Any]] = None,
) -> str:
    """Formats the branch column cell distinctly for local worktrees, plain branches, and remote-only branches."""
    pr_tag = ""
    if github_pr and github_pr.get("number"):
        pr_num = github_pr["number"]
        pr_url = github_pr.get("url")
        pr_tag = f" [[#{pr_num}]]({pr_url})" if pr_url else f" [#{pr_num}]"

    if is_remote or branch.startswith("origin/"):
        target_str = f"🌐 {branch}{pr_tag}"
    elif wt and "path" in wt and not wt.get("is_root"):
        target_str = f"[{branch}](file://{wt['path']}){pr_tag}"
    else:
        target_str = f"{branch}{pr_tag}"

    return f'<span style="display:inline-block;min-width:240px">{target_str}</span>'


render_branch_cell = format_branch_cell


def render_queue_table(
    global_queue: List[Dict[str, Any]],
    inv_counts: Optional[Dict[str, int]] = None,
    view_mode: str = "executive",
    repo_root: Optional[Path] = None,
    detail_path: Optional[Path] = None,
    inv: Optional[Dict[str, Any]] = None,
    show_council: bool = False,
) -> None:
    """Renders the candidate feature branch table in executive, compact, wide, or decision mode."""
    target_root = repo_root or ROOT
    if detail_path is None:
        detail_path = target_root / "reports" / "pr_queue_detail.json"

    # Guarantee linked detail artifact exists on disk before emitting links
    from .detail_artifact import generate_pr_queue_detail_artifact

    generate_pr_queue_detail_artifact(target_root, global_queue, inv=inv, target_path=detail_path)

    if view_mode == "wide":
        _render_wide_table(global_queue, inv_counts, detail_path=detail_path)
    elif view_mode == "decision":
        _render_decision_table(global_queue, inv_counts, detail_path=detail_path)
    elif view_mode == "compact":
        _render_compact_table(global_queue, inv_counts, detail_path=detail_path)
    else:
        _render_executive_table(global_queue, inv_counts, detail_path=detail_path, show_council=show_council)


def _determine_blocker_and_evidence(x: Dict[str, Any]) -> tuple[str, str | None]:
    clean_merge = x.get("clean_merge", True)
    review = x.get("review_verdict", "—")
    behind = x.get("behind", 0)
    ahead = x.get("ahead", 0)
    open_w = x.get("open_waves", "—")
    completed_w = x.get("completed_waves", "—")
    receipt_pass = x.get("readiness_receipt_pass", False)

    blockers = []
    evidence = []

    if not clean_merge or review == "RESOLVE_CONFLICTS":
        blockers.append("Merge conflicts vs main")
    elif review == "GOVERNANCE_BLOCK":
        blockers.append("Governance policy block")
    elif review == "SPLIT_REQUIRED":
        blockers.append("Split required across concerns")
    elif review == "REBASE_RETARGET":
        blockers.append("Retarget / rebase required")

    if behind >= 20:
        blockers.append(f"Severely diverged (behind {behind} commits)")
    elif behind > 5:
        blockers.append(f"Rebase needed (behind {behind} commits)")

    if ahead == 0:
        blockers.append("Zero authored commits (inception/authoring)")

    if open_w == "?" and completed_w == "?":
        evidence.append("Missing branch plan document / unparsed waves")
    elif open_w != "—" and open_w != "?":
        blockers.append(f"Open waves [{open_w}] incomplete")

    if open_w == "—" and not receipt_pass and ahead > 0:
        evidence.append("Unverified qualification (no passing Tier 2 readiness receipt)")

    blocker_str = "; ".join(blockers) if blockers else "None (clean)"
    evidence_str = "; ".join(evidence) if evidence else None
    return blocker_str, evidence_str


def _print_coverage_and_detail(
    inv_counts: Optional[Dict[str, int]] = None,
    detail_path: Optional[Path] = None,
) -> None:
    if inv_counts:
        cov_c = inv_counts.get("covered_children", inv_counts.get("covered_worktrees_children", inv_counts.get("registered", 0) + inv_counts.get("orphan_directories", 0)))
        tot_c = inv_counts.get("total_children", inv_counts.get("total_physical_children", inv_counts.get("total_worktrees_children", 0)))
        cov_s = inv_counts.get("covered_stashes", inv_counts.get("stashes_total", 0))
        tot_s = inv_counts.get("total_stashes", inv_counts.get("stashes_total", 0))
        print(f"Worktree coverage: {cov_c}/{tot_c} children · {cov_s}/{tot_s} stashes")
    if detail_path:
        print(f"Detail artifact: [reports/pr_queue_detail.json](file://{detail_path})")


def _render_compact_table(
    global_queue: List[Dict[str, Any]],
    inv_counts: Optional[Dict[str, int]] = None,
    detail_path: Optional[Path] = None,
) -> None:
    print("=" * 105)
    print("  LOCAL PR GOVERNANCE: CANDIDATE FEATURE BRANCHES (SINGLE QUEUE)")
    print("=" * 105)

    print("| Priority | Domain | Branch | Readiness | Review | Ahead | Behind |")
    print("|---|---|---|---|---|---|---|")

    stash_count = 0
    for x in global_queue:
        tier = x.get("tier")
        branch = x["branch"]
        if tier == "W" and branch.startswith("stash@{"):
            stash_count += 1
            continue

        sub_rank = x.get("sub_rank", "—")
        readiness = x.get("readiness", "—")
        review = x.get("review_verdict", "—")
        domain = x.get("domain") or "—"
        is_remote = bool(x.get("is_remote", False) or branch.startswith("origin/"))
        branch_link = format_branch_cell(
            branch,
            wt=x.get("wt"),
            is_remote=is_remote,
            github_pr=x.get("github_pr"),
        )
        ahead = x.get("ahead", 0)
        behind = x.get("behind", 0)

        print(f"| {sub_rank} | {domain} | {branch_link} | {readiness} | {review} | {ahead} | {behind} |")

    if stash_count > 0:
        target = f"file://{detail_path}" if detail_path else "reports/pr_queue_detail.json"
        print(
            f"| W.stashes | Housekeeping | [{stash_count} git stashes collapsed]({target}) | 📦 Stashed | STASH | 0 | 0 |"
        )

    branch_count = sum(1 for x in global_queue if x.get("tier") != "W")

    print()
    print("=" * 105)
    print("  DIAGNOSTIC & NEXT ACTION LEDGER")
    print("=" * 105)
    for x in global_queue:
        sub_rank = x.get("sub_rank", "—")
        branch = x["branch"]
        next_action = x.get("next_action", "—")
        rationale = x.get("ranking_rationale", "—")
        effort = x.get("rebase_effort", "NONE")
        completed = x.get("completed_waves", "—")
        open_w = x.get("open_waves", "—")
        tier = x.get("tier")

        if tier != "W" or not branch.startswith("stash@{"):
            blocker_str, evidence_str = _determine_blocker_and_evidence(x)
            val_stmt = x.get("value_statement") or "UNKNOWN: Unmeasured business value (structural heuristics only)"
            req_str = x.get("completion_evidence_requirement") or evidence_str or "Passing Tier 2 readiness receipt and wave plan completion"
            print(f"* **[{sub_rank}] `{branch}`**:")
            print(f"  * **Next Action**: {next_action}")
            print(f"  * **Value**: {val_stmt}")
            if x.get("github_pr"):
                pr = x["github_pr"]
                pr_link = f"[#{pr['number']}: {pr['title']}]({pr['url']})" if pr.get("url") else f"#{pr['number']}"
                print(f"  * **GitHub PR**: {pr_link} ({pr.get('state', 'OPEN')})")
            print(f"  * **Ranking Rationale**: {rationale}")
            print(f"  * **Blocker**: {x.get('blocker', blocker_str)}")
            print(f"  * **Completion Evidence Requirement**: {req_str}")
            print(f"  * **Effort & Waves**: effort={effort} · completed={completed} · open={open_w}")

    print()
    print(f"Total candidate branches in queue: {branch_count}")
    print(f"Total entries in queue: {len(global_queue)}")
    _print_coverage_and_detail(inv_counts, detail_path)


def _format_executive_disposition(x: Dict[str, Any]) -> tuple[str, str]:
    from .executive_render import format_executive_disposition

    return format_executive_disposition(x)


def _render_executive_table(
    global_queue: List[Dict[str, Any]],
    inv_counts: Optional[Dict[str, int]] = None,
    detail_path: Optional[Path] = None,
    show_council: bool = False,
) -> None:
    from .executive_render import render_executive_table

    render_executive_table(global_queue, inv_counts=inv_counts, detail_path=detail_path, show_council=show_council)


def _render_decision_table(
    global_queue: List[Dict[str, Any]],
    inv_counts: Optional[Dict[str, int]] = None,
    detail_path: Optional[Path] = None,
) -> None:
    print("=" * 135)
    print("  LOCAL PR GOVERNANCE: CANDIDATE FEATURE BRANCHES (DECISION VIEW)")
    print("=" * 135)

    print("| Priority | Domain | Branch | Next Action | Why This Position | Blocker / Eligibility |")
    print("|---|---|---|---|---|---|")

    stash_count = 0
    for x in global_queue:
        tier = x.get("tier")
        branch = x["branch"]
        if tier == "W" and branch.startswith("stash@{"):
            stash_count += 1
            continue

        sub_rank = x.get("sub_rank", "—")
        domain = x.get("domain") or "—"
        is_remote = bool(x.get("is_remote", False) or branch.startswith("origin/"))
        branch_link = format_branch_cell(
            branch,
            wt=x.get("wt"),
            is_remote=is_remote,
            github_pr=x.get("github_pr"),
        )
        next_action = x.get("next_action", "—")
        rationale = x.get("ranking_rationale", "—")
        blocker_str, _ = _determine_blocker_and_evidence(x)
        blocker = x.get("blocker", blocker_str)

        print(f"| {sub_rank} | {domain} | {branch_link} | {next_action} | {rationale} | {blocker} |")

    if stash_count > 0:
        target = f"file://{detail_path}" if detail_path else "reports/pr_queue_detail.json"
        print(
            f"| W.stashes | Housekeeping | [{stash_count} git stashes collapsed]({target}) | [TRIAGE_STASH] | Stash rows collapsed | Stashed work |"
        )

    branch_count = sum(1 for x in global_queue if x.get("tier") != "W")
    print()
    print(f"Total candidate branches in queue: {branch_count}")
    print(f"Total entries in queue: {len(global_queue)}")
    _print_coverage_and_detail(inv_counts, detail_path)


def _render_wide_table(
    global_queue: List[Dict[str, Any]],
    inv_counts: Optional[Dict[str, int]] = None,
    detail_path: Optional[Path] = None,
) -> None:
    print("=" * 165)
    print("  LOCAL PR GOVERNANCE: CANDIDATE FEATURE BRANCHES (SINGLE QUEUE - WIDE)")
    print("=" * 165)

    print(
        "| Priority | Domain | Branch | Next Action | Ranking Rationale | Readiness | Review | Effort | Completed Waves | Open Waves | Ahead | Behind |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")

    for x in global_queue:
        sub_rank = x.get("sub_rank", "—")
        readiness = x.get("readiness", "—")
        review = x.get("review_verdict", "—")
        effort = x.get("rebase_effort", "NONE")
        domain = x.get("domain") or "—"
        branch = x["branch"]
        is_remote = bool(x.get("is_remote", False) or branch.startswith("origin/"))
        branch_link = format_branch_cell(
            branch,
            wt=x.get("wt"),
            is_remote=is_remote,
            github_pr=x.get("github_pr"),
        )
        completed = x.get("completed_waves", "—")
        open_w = x.get("open_waves", "—")
        ahead = x.get("ahead", 0)
        behind = x.get("behind", 0)
        next_action = x.get("next_action", "—")
        rationale = x.get("ranking_rationale", "—")

        print(
            f"| {sub_rank} | {domain} | {branch_link} | {next_action} | {rationale} | {readiness} | {review} | {effort} | {completed} | {open_w} | {ahead} | {behind} |"
        )

    branch_count = sum(1 for x in global_queue if x.get("tier") != "W")
    print()
    print(f"Total candidate branches in queue: {branch_count}")
    print(f"Total entries in queue: {len(global_queue)}")
    _print_coverage_and_detail(inv_counts, detail_path)
