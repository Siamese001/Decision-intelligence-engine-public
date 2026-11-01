"""
tools/pr_governance_core/executive_render.py

High-signal 10-column Executive Disposition Table rendering engine for PR queue governance.
Renders active candidate branches with concise, actionable disposition verbs, explicit
refactoring complexity sizing (XS, S, M, L, XL), and 6-Agent Council hardened action items.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .complexity import evaluate_refactoring_complexity
from .council import deliberate_branch, deliberate_council_queue
from .render import _determine_blocker_and_evidence, _print_coverage_and_detail, format_branch_cell
from .verdict_verifier import verify_branch_verdict_and_disposition


def format_deliverable_promotion_action(x: Dict[str, Any], stat_str: str = "") -> Optional[str]:
    """Formats commit/ancestry provenance and exact rebase/merge/finish commands for finalized deliverable branches."""
    diff_cat = str(x.get("diff_category") or "")
    deliv_dirs = x.get("deliverable_dirs") or (x.get("diff_semantics") or {}).get("deliverable_dirs") or []
    code_cnt = int(x.get("code_files_count", (x.get("diff_semantics") or {}).get("code_files_count", 0)))
    if diff_cat != "DELIVERABLE_PACKAGE_READY" and not (deliv_dirs and code_cnt == 0):
        return None

    branch = str(x.get("branch", ""))
    behind = int(x.get("behind", 0))
    tip_sha = str(x.get("tip_sha") or "").strip()
    base_sha = str(x.get("merge_base_sha") or "").strip()
    main_sha = str(x.get("main_head_sha") or "").strip()
    has_pub = bool(x.get("has_publication_receipt") or (x.get("diff_semantics") or {}).get("has_publication_receipt"))
    raw_subj = str(x.get("last_commit_subject") or f"feat(ingest): finalize {branch.split('/')[-1]} deliverables")
    subject = raw_subj.replace('"', "'")

    deliv_str = f" in {deliv_dirs[0]}/" if deliv_dirs else ""
    receipt_str = " (publication_coverage_receipt.json verified)" if has_pub else " (01-03 triad verified)"
    sha_str = f" in {tip_sha}" if tip_sha else ""
    if base_sha and main_sha and behind > 0:
        ancestry_str = f" (based on {base_sha} vs main {main_sha}, {behind} behind)"
    else:
        ancestry_str = f" ({behind} commits behind main)" if behind > 0 else (f" (synced with main {main_sha})" if main_sha else "")

    act = f"Rebase onto main ('python tools/pr_governance.py rebase {branch}') and merge" if behind > 0 else "Governed merge into main"
    return (
        f"Committed{sha_str}{ancestry_str}; finalized deliverables{deliv_str}{receipt_str}. "
        f"{act} via 'python tools/pr_governance.py merge {branch} --title \"{subject}\"'{stat_str}"
    )


def _eval_decommission_or_salvage(x: Dict[str, Any], verified_disp: str, stat_str: str) -> Optional[Tuple[str, str, str, str, str]]:
    ahead, behind = int(x.get("ahead") or 0), int(x.get("behind") or 0)
    dirty = bool(x.get("dirty", False))
    next_action = str(x.get("next_action") or "")
    open_w = str(x.get("open_waves", "—"))
    tier, diff_cat = str(x.get("tier", "")), str(x.get("diff_category") or "")
    is_docs_plan = bool(x.get("is_docs_only_plan", False))
    absorbed_files, salvageable_files = x.get("absorbed_files") or [], x.get("salvageable_files") or []
    branch = str(x.get("branch", ""))

    if str(x.get("review_verdict") or "") == "GOVERNANCE_BLOCK":
        return ("N", "Y" if behind > 0 else "N", "Y", "Hold / Blocked", "Resolve failing governance gate or linter, then rerun readiness review")

    if (absorbed_files and salvageable_files) or verified_disp == "Salvage Subset & Archive":
        salv_count, abs_count = len(salvageable_files), len(absorbed_files)
        salv_names = ", ".join(salvageable_files[:2]) + (f" (+{salv_count - 2} more)" if salv_count > 2 else "")
        abs_names = ", ".join(absorbed_files[:2]) + (f" (+{abs_count - 2} more)" if abs_count > 2 else "")
        code_done = "N" if dirty or (open_w not in ("—", "?", "", "None") and open_w.strip()) else "Y"
        hitl_tag = "[HITL_CLARIFICATION_REQUIRED] " if x.get("requires_hitl_clarification", True) else ""
        return (code_done, "Y" if behind > 0 else "N", "Y (Partial)", "Salvage Subset & Archive", f"{hitl_tag}Extract {salv_count} unique file(s) ({salv_names}); {abs_count} file(s) already absorbed into main ({abs_names}){stat_str}")

    if x.get("action") == "SALVAGE_DELIVERABLES" or "[SALVAGE" in next_action or (x.get("has_uncommitted_deliverables") and x.get("has_stale_code_edits") and behind >= 20):
        deliv_count = int(x.get("deliverable_files_count") or len(salvageable_files) or 1)
        hitl_tag = "[HITL_CLARIFICATION_REQUIRED] " if x.get("requires_hitl_clarification") else ""
        return ("N", "Y" if behind > 0 else "N", "Y (Partial)", "Salvage Subset & Archive", f"{hitl_tag}Extract {deliv_count} deliverable file(s) into ingest branch; archive stale scratch code edits{stat_str}")

    if tier.startswith(("D", "TOMBSTONE")) or "[PRUNE]" in next_action or "[TOMBSTONE]" in next_action:
        disp_reason = (
            str(x.get("disposition_reason") or "").strip()
            or next_action.replace("[PRUNE]", "").replace("[TOMBSTONE]", "").strip()
            or str(x.get("ranking_rationale") or "superseded or abandoned zero-commit branch")
        )
        force_flag = " --force" if dirty or ahead == 0 else ""
        return ("N/A", "N/A", "N", "Decommission (No Value)", f"Safely prune branch/worktree ({disp_reason}); discard via 'python tools/pr_governance.py finish {branch}{force_flag}'")
    if (diff_cat == "DOCS_ONLY_UNSTARTED" or is_docs_plan) and behind >= 20:
        return ("N/A", "N/A", "N", "Decommission (No Value)", f"Safely prune stale unstarted planning worktree ({behind} commits behind main)")
    if diff_cat == "EMPTY_STUB" and not dirty and open_w in ("—", "?", "", "None"):
        force_flag = " --force" if ahead == 0 else ""
        return ("N/A", "N/A", "N", "Decommission (No Value)", f"Safely prune redundant worktree (zero diff delta against main); discard via 'python tools/pr_governance.py finish {branch}{force_flag}'")
    if diff_cat == "SUPERSEDED_CODE" or (absorbed_files and not salvageable_files):
        abs_count = len(absorbed_files)
        abs_names = ", ".join(absorbed_files[:2]) + (f" (+{abs_count - 2} more)" if abs_count > 2 else "")
        abs_detail = f"; {abs_count} file(s) fully absorbed into main ({abs_names})" if abs_count else " (superseded by main)"
        if dirty:
            return ("N", "N/A", "N", "Decommission (No Value)", f"Commit {int(x.get('dirty_files_count', 1))} authored file(s), tag archive, and prune worktree{abs_detail}")
        return ("N/A", "N/A", "N", "Decommission (No Value)", f"Safely prune worktree and branch ('tools/pr_governance.py finish {branch}'){abs_detail}")
    return None


def evaluate_stage_gates_and_disposition(x: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    """Derive clean, high-signal 5-tuple:
    (code_completed, rebase_required, pr_to_main_required, value_disposition, immediate_action)
    """
    ahead, behind = int(x.get("ahead") or 0), int(x.get("behind") or 0)
    dirty, clean_merge = bool(x.get("dirty", False)), bool(x.get("clean_merge", True))
    review = str(x.get("review_verdict", "—"))
    next_action = str(x.get("next_action") or "")
    open_w = str(x.get("open_waves", "—"))
    overlap_files = x.get("overlap_files")
    readiness_receipt_pass = bool(x.get("readiness_receipt_pass", False))
    file_numstat = x.get("file_numstat") or {}
    total_add = sum(s[0] for s in file_numstat.values()) if file_numstat else 0
    total_del = sum(s[1] for s in file_numstat.values()) if file_numstat else 0
    stat_str = f" (+{total_add}/-{total_del})" if (total_add or total_del) else ""

    from .completion_state import evaluate_branch_completion_state
    act_state = evaluate_branch_completion_state(x)
    x["completion_state"], x["is_completed"] = act_state.completion_state, act_state.is_completed
    x["is_in_progress"], x["in_progress_reasons"] = act_state.is_in_progress, act_state.in_progress_reasons

    v_verif = x.get("verdict_verification") or verify_branch_verdict_and_disposition(x).to_dict()
    verified_disp = str(v_verif.get("verified_disposition") or "")

    decomm = _eval_decommission_or_salvage(x, verified_disp, stat_str)
    if decomm is not None:
        return decomm

    if act_state.has_git_lock or act_state.active_pids:
        pid_str = f" (active PID(s): {', '.join(map(str, act_state.active_pids[:2]))})" if act_state.active_pids else " (active git lock/rebase)"
        return ("N", "Y" if behind > 0 else "N", "Y", "Complete In-Flight Work", f"Active session in progress{pid_str}; wait for completion before rebasing onto main")

    if dirty or "[COMMIT_FIRST]" in next_action:
        if ahead == 0 or "[COMMIT_FIRST]" in next_action and not dirty:
            return ("N", "Y" if behind > 0 else "N", "Y", "Complete In-Flight Work", "Commit authored inception work before rebasing onto main")
        effort_tail = f" (effort: {x.get('rebase_effort', 'MEDIUM')})" if not clean_merge else ""
        return ("N", "Y" if behind > 0 else "N", "Y", "Complete In-Flight Work", f"Commit {int(x.get('dirty_files_count', 1))} authored file(s) before rebasing onto main{effort_tail}")

    if open_w not in ("—", "", "None") and open_w.strip():
        if open_w == "?":
            if ahead == 0:
                return ("N/A", "N/A", "N", "Decommission (No Value)", f"Safely prune worktree and branch ('tools/pr_governance.py finish {x.get('branch', '')} --force')")
            return ("N", "Y" if behind > 0 else "N", "Y", "Complete In-Flight Work", "Author implementation_plan.md and define wave acceptance criteria")
        return ("N", "Y" if behind > 0 else "N", "Y", "Complete In-Flight Work", f"Complete open wave(s) [{open_w}] and seal wave runtime receipts")

    if not clean_merge or review == "RESOLVE_CONFLICTS" or "[RESOLVE_CONFLICTS" in next_action:
        effort = x.get("rebase_effort", "MEDIUM")
        conf_files = x.get("conflicted_files") or x.get("conflict_files") or []
        code_done = "Y" if (ahead > 0 and (readiness_receipt_pass or (open_w in ("—", "", "None") and not dirty))) else "N"
        disp = "Promote to Main" if ahead > 0 else "Complete In-Flight Work"
        f_tail = ""
        if conf_files:
            file_names = [f["path"] if isinstance(f, dict) else getattr(f, "path", str(f)) for f in conf_files]
            classes = [f.get("conflict_class", "LOGIC") if isinstance(f, dict) else getattr(getattr(f, "conflict_class", None), "value", "") for f in conf_files]
            c_str = f" [{classes[0]}]" if classes and classes[0] else ""
            f_tail = f" and resolve conflicting file(s): {', '.join(file_names[:2])}{f' (+{len(file_names)-2} more)' if len(file_names) > 2 else ''}{c_str}"
        return (code_done, "Y", "Y", disp, f"Rebase onto main (effort: {effort}){f_tail or ' and resolve conflicting files'}")

    deliv_action = format_deliverable_promotion_action(x, stat_str)
    if deliv_action and ahead > 0:
        return ("Y", "Y" if behind > 0 else "N", "Y", "Promote to Main", deliv_action)

    branch = str(x.get("branch", ""))
    if ahead == 0:
        return ("N/A", "N/A", "N", "Decommission (No Value)", f"Safely prune worktree and branch ('tools/pr_governance.py finish {branch} --force')")

    if behind > 0:
        code_done = "Y" if (ahead > 0 and not dirty and open_w in ("—", "", "None")) else "N"
        o_str = f", {len(overlap_files)} shared file(s) changed on main: {', '.join(overlap_files[:2])}{f' (+{len(overlap_files)-2} more)' if len(overlap_files) > 2 else ''}" if overlap_files else ""
        tail = f"{o_str}){stat_str}" if overlap_files else f"), verify readiness, and merge{stat_str}"
        return (code_done, "Y", "Y", "Promote to Main", f"Rebase onto main ({behind} commits behind{tail}")

    if ahead > 0 and clean_merge and not dirty and behind == 0:
        return ("Y", "N", "Y", "Promote to Main", f"Stage 3 Ready: run readiness review and governed merge into main{stat_str}")

    clean_action = next_action.replace("[", "").replace("]", "").strip() if next_action else "Review and proceed"
    blocker_str, _ = _determine_blocker_and_evidence(x)
    code_done = "Y" if (ahead > 0 and not dirty and open_w in ("—", "", "None")) else "N"
    return (code_done, "Y" if behind > 0 else "N", "Y", "Promote to Main" if ahead > 0 else "Complete In-Flight Work", str(x.get("blocker") or blocker_str or clean_action))


def _inspect_receipt_for_graph_mutation(path_str: str, raw_text: str) -> bool:
    if not raw_text:
        return False
    fname = Path(path_str).name
    if fname == "graph_mutation_breakdown.md":
        if any(k in raw_text for k in ("Pre-Mutation Diagnostic", "Pre-mutation diagnostic scan (unmutated)")):
            return False
        return any(k in raw_text for k in ("Authoritative Publication & Activation", "**Active Generation:** `generation-", "GRAPH_MUTATED"))
    try:
        data = json.loads(raw_text)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if fname == "backtest_promotion_receipt.json":
        if data.get("status") in ("PROMOTED", "ACTIVATED"):
            return True
        pub = data.get("publish_result") if isinstance(data.get("publish_result"), dict) else {}
        ms = pub.get("mutation_summary") if isinstance(pub.get("mutation_summary"), dict) else {}
        return ms.get("status") == "GRAPH_MUTATED" or bool(pub.get("active_generation") or pub.get("activated_generation_id"))
    if fname == "mutation_approval_receipt.json":
        return data.get("status") in ("ACTIVATED", "PROMOTED") or bool(data.get("activated_generation_id"))
    if fname == "publication_coverage_receipt.json":
        cs = data.get("coverage_summary") if isinstance(data.get("coverage_summary"), dict) else {}
        return bool(data.get("activated_generation_id") or int(cs.get("committed_count", 0) or 0) > 0)
    if fname == "pipeline_execution_receipt.json":
        if data.get("graph_mutated") is True or data.get("active_generation") or data.get("activated_generation_id"):
            return True
        stages = data.get("stages") if isinstance(data.get("stages"), dict) else {}
        details = (stages.get("EVALS_RELEASE") or {}).get("details")
        return bool(isinstance(details, dict) and (details.get("graph_mutated") is True or details.get("active_generation")))
    return False


def detect_graph_mutated(x: Dict[str, Any], repo_root: Optional[Path] = None) -> str:
    """Return 'Y' if the branch or worktree has authoritative evidence of mutating the Knowledge Graph, else 'N'."""
    explicit = x.get("graph_mutated")
    if explicit in ("Y", "N"):
        return str(explicit)
    if isinstance(explicit, bool):
        return "Y" if explicit else "N"

    raw_cf = x.get("changed_files")
    files: List[str] = [str(f) for f in raw_cf] if isinstance(raw_cf, (list, tuple, set)) else []
    for extra in ((x.get("info") or {}).get("changed_files") or []) if isinstance(x.get("info"), dict) else []:
        if extra not in files:
            files.append(str(extra))
    for df in x.get("dirty_files") or []:
        if df not in files:
            files.append(str(df))

    if any(f.endswith((".sqlite", ".db", "active_pair.json")) for f in files):
        return "Y"

    wt_path_str = x.get("worktree_path") or (x.get("wt", {}) or {}).get("path")
    wt_dir = Path(wt_path_str) if wt_path_str else None
    root = repo_root or Path(__file__).resolve().parents[2]
    receipt_names = (
        "backtest_promotion_receipt.json", "mutation_approval_receipt.json",
        "publication_coverage_receipt.json", "graph_mutation_breakdown.md", "pipeline_execution_receipt.json",
    )
    candidate_paths = [f for f in files if any(f.endswith(rn) for rn in receipt_names)]

    if wt_dir:
        session_dirs = {
            Path(f).parent for f in files
            if f.startswith("output/") and not f.startswith("output/transcript_cache/") and len(Path(f).parts) >= 3
        }
        for s_dir in session_dirs:
            full_s_dir = wt_dir / s_dir
            if full_s_dir.is_dir():
                for rn in receipt_names:
                    for p in full_s_dir.rglob(rn):
                        try:
                            rel = str(p.relative_to(wt_dir))
                            if rel not in candidate_paths:
                                candidate_paths.append(rel)
                        except Exception:
                            pass

    branch = str(x.get("branch", ""))
    for rel_path in candidate_paths:
        raw_text = ""
        for base_dir in ([wt_dir, root] if wt_dir else [root]):
            if base_dir and (base_dir / rel_path).is_file():
                try:
                    raw_text = (base_dir / rel_path).read_text(encoding="utf-8", errors="replace")
                    break
                except Exception:
                    pass
        if not raw_text and branch and not branch.startswith("stash@{"):
            try:
                from .common import run_git
                raw_text = run_git(["show", f"{branch}:{rel_path}"], cwd=root, check=False) or ""
            except Exception:
                raw_text = ""
        if _inspect_receipt_for_graph_mutation(rel_path, raw_text):
            return "Y"
    return "N"


def format_executive_disposition(x: Dict[str, Any]) -> Tuple[str, str]:
    """Backwards compatibility adapter returning (value_disposition, immediate_action)."""
    _, _, _, disposition, action = evaluate_stage_gates_and_disposition(x)
    return disposition, action


def evaluate_executive_row(x: Dict[str, Any], idx: int) -> Tuple[Any, str, str, str, str, str, str, str, str, str]:
    """Single-pass evaluation for 10-column Executive/Observer row cells."""
    branch = str(x.get("branch", "UNKNOWN"))
    domain = str(x.get("domain") or "Enhancement")
    is_remote = bool(x.get("is_remote", False) or branch.startswith("origin/"))
    wt_path = x.get("worktree_path")
    wt_dict = {"path": wt_path, "is_root": False} if wt_path else x.get("wt")
    branch_link = format_branch_cell(branch.replace("|", "&#124;"), wt=wt_dict, is_remote=is_remote, github_pr=x.get("github_pr"))

    comp = evaluate_refactoring_complexity(x)
    x["complexity"] = comp.to_dict()
    code_done, rebase_req, pr_req, disposition, _ = evaluate_stage_gates_and_disposition(x)
    graph_mut = detect_graph_mutated(x)
    x["graph_mutated"] = graph_mut
    council_res = deliberate_branch(x)
    p_val = x.get("sub_rank") or idx
    return p_val, branch_link, domain, comp.size, code_done, rebase_req, pr_req, graph_mut, disposition, council_res.hardened_action


def render_executive_table(
    global_queue: List[Dict[str, Any]],
    inv_counts: Optional[Dict[str, int]] = None,
    detail_path: Optional[Path] = None,
    show_council: bool = False,
) -> None:
    """Renders the candidate feature branch queue in 10-column executive disposition format."""
    print("=" * 145)
    print("  LOCAL PR GOVERNANCE: EXECUTIVE CANDIDATE QUEUE (SCHEMA 4.0.0)")
    print("=" * 145)
    print("| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |")
    print("|:---:|:----------------------------------:|:---|:---:|:---:|:---:|:---:|:---:|:---|:---|")

    active_branches, collapsed_stashes, collapsed_stubs = [], 0, 0
    for x in global_queue:
        if x.get("tier") == "W":
            if str(x.get("branch", "")).startswith("stash@{"):
                collapsed_stashes += 1
            else:
                collapsed_stubs += 1
            continue
        active_branches.append(x)

    for idx, x in enumerate(active_branches, start=1):
        p_val, b_link, dom, c_size, c_done, r_req, pr_req, g_mut, disp, h_act = evaluate_executive_row(x, idx)
        print(f"| **{p_val}** | {b_link} | {dom} | **{c_size}** | **{c_done}** | **{r_req}** | **{pr_req}** | **{g_mut}** | **{disp}** | {h_act} |")

    target = f"file://{detail_path}" if detail_path else "reports/pr_queue_detail.json"
    if collapsed_stashes > 0 or collapsed_stubs > 0:
        parts = ([f"{collapsed_stubs} session stub(s)"] if collapsed_stubs else []) + ([f"{collapsed_stashes} git stash(es)"] if collapsed_stashes else [])
        print(f"| — | [{' & '.join(parts)} collapsed]({target}) | Housekeeping | — | **N/A** | **N** | **N** | **N** | **Quarantine / Triage** | Sealed in reports/pr_queue_detail.json |")

    from .completion_state import summarize_queue_completion_split
    split = summarize_queue_completion_split(active_branches)
    print(f"\nActive candidate branches: {len(active_branches)}")
    print(f"Completed & ready branches: {split['completed_branch_count']} · In-progress branches: {split['in_progress_branch_count']} · Decommission: {split['decommission_branch_count']}")
    print(f"Total entries tracked: {len(global_queue)}")
    _print_coverage_and_detail(inv_counts, detail_path)

    if show_council and active_branches:
        deliberation = deliberate_council_queue(active_branches)
        print("\n" + "=" * 145)
        print("  6-AGENT COUNCIL DELIBERATION & MERGE SEQUENCE RATIONALIZATION")
        print("=" * 145)
        for r in deliberation.debate_rationales:
            print(f"• {r}")
