"""tools/pr_governance_core/decision_api.py

SSOT Unified Decision API for PR Queue Prioritization and Dependency-Constrained Sequencing (Wave 3).
Unifies work priority, review readiness, merge eligibility, typed dependencies, and topological execution order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]

from tools.pr_governance_core.decision_graph import (
    DependencyEdgeType,
    compute_topological_execution_sequence,
    detect_hard_dependencies,
)
from tools.pr_governance_core.conflict_triage import analyze_branch_file_diffs
from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome
from tools.pr_governance_core.observer_evidence import discover_evidence_links as _discover_evidence_links
from tools.pr_governance_core.observer_actions import observer_next_action
from tools.pr_governance_core.queue_actions import (
    explain_blocker_and_evidence_requirement,
    explain_relative_order,
)
from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue
from tools.pr_governance_core.queue_reconcile import compute_risk_tier
from tools.pr_governance_core.snapshot_history import compute_rank_transitions
from tools.pr_priority_engine import derive_priority, load_registry, load_rubric


class MergeEligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    BLOCKED_PREREQUISITE = "BLOCKED_PREREQUISITE"
    BLOCKED_CONFLICT = "BLOCKED_CONFLICT"
    BLOCKED_CYCLE = "BLOCKED_CYCLE"
    BLOCKED_AUTHORING = "BLOCKED_AUTHORING"
    BLOCKED_GOVERNANCE = "BLOCKED_GOVERNANCE"
    BLOCKED_WIP = "BLOCKED_WIP"
    PROVISIONAL = "PROVISIONAL"
    DECOMMISSIONED = "DECOMMISSIONED"


@dataclass
class DependencyEdge:
    source_branch: str
    target_branch: str
    edge_type: str
    is_hard_prerequisite: bool
    evidence: str
    shared_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QueueDecisionResult:
    snapshot_observed_at: str
    policy_version: str
    branches: List[Dict[str, Any]]
    execution_sequence: List[str]
    blocked_branches: Dict[str, str]
    cycles: List[List[str]]
    provisional_items: List[str]
    hard_dependency_graph: Dict[str, List[str]]
    rank_transitions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        from .completion_state import summarize_queue_completion_split

        split = summarize_queue_completion_split(self.branches)
        return {
            "snapshot_observed_at": self.snapshot_observed_at,
            "policy_version": self.policy_version,
            "completed_branch_count": split["completed_branch_count"],
            "in_progress_branch_count": split["in_progress_branch_count"],
            "decommission_branch_count": split["decommission_branch_count"],
            "completed_branches": split["completed_branches"],
            "in_progress_branches": split["in_progress_branches"],
            "decommission_branches": split["decommission_branches"],
            "branches": self.branches,
            "execution_sequence": self.execution_sequence,
            "blocked_branches": self.blocked_branches,
            "cycles": self.cycles,
            "provisional_items": self.provisional_items,
            "hard_dependency_graph": self.hard_dependency_graph,
            "rank_transitions": self.rank_transitions,
        }


def _determine_branch_eligibility(
    b_name: str,
    tier: str,
    ahead: int,
    clean: bool,
    dirty: bool,
    cycles: List[List[str]],
    base_branch: str,
    readiness_receipt_pass: Optional[bool] = None,
) -> Tuple[str, List[str]]:
    notes = []
    if tier.startswith(("D", "TOMBSTONE")):
        return MergeEligibility.DECOMMISSIONED.value, ["Decommissioned or zero-unique-commit branch"]
    if tier.startswith("W"):
        return MergeEligibility.BLOCKED_WIP.value, ["Worktree preservation row; not a merge branch"]
    if any(b_name in c for c in cycles):
        return MergeEligibility.BLOCKED_CYCLE.value, ["Part of circular dependency cycle"]
    if clean is None or ahead is None:
        return MergeEligibility.BLOCKED_GOVERNANCE.value, ["Current Git qualification facts are unknown"]
    if readiness_receipt_pass is not True:
        return MergeEligibility.BLOCKED_GOVERNANCE.value, ["Current-revision readiness review is not verified"]
    if not clean:
        return MergeEligibility.BLOCKED_CONFLICT.value, [f"Merge conflict with {base_branch}"]
    if ahead == 0:
        return MergeEligibility.BLOCKED_AUTHORING.value, ["Authoring inception: zero unique commits ahead of main"]
    if dirty:
        return MergeEligibility.BLOCKED_WIP.value, ["Uncommitted changes in worktree (commit first)"]
    return MergeEligibility.ELIGIBLE.value, []


def _build_branch_item(
    b_snap: Any,
    hard_graph: Dict[str, List[str]],
    cycles: List[List[str]],
    base_branch: str,
    reg: Dict[str, Any],
    rub: Dict[str, Any],
    root: Path,
    base_sha: str = "",
) -> Tuple[Dict[str, Any], bool]:
    b_name, wt_path = b_snap.branch, getattr(b_snap, "worktree_path", None)
    ahead, behind = getattr(b_snap, "ahead", None), getattr(b_snap, "behind", None)
    clean, dirty = getattr(b_snap, "clean_merge", None), getattr(b_snap, "is_dirty", False)
    changed_files = list(getattr(b_snap, "changed_files", []))
    priority_ahead = ahead if isinstance(ahead, int) else 0
    priority_behind = behind if isinstance(behind, int) else 10**6
    priority_clean = clean is True

    meta = derive_priority(
        branch=b_name, changed_files=changed_files, overrides=reg.get("overrides", {}),
        rubric=rub, ahead=priority_ahead, behind=priority_behind, clean_merge=priority_clean,
        worktree={"dirty": dirty, "path": wt_path} if wt_path else None,
        repo_root=root,
    )
    tier = meta["tier"]
    val_score = meta.get("value", 0.0)
    risk_score = meta.get("risk", 0)
    risk_tier = compute_risk_tier(tier, {"behind": priority_behind, "clean_merge": priority_clean}, bool(dirty), b_snap.open_waves, "REVIEW_READY")
    readiness_pass = getattr(b_snap, "readiness_receipt_pass", None)
    eligibility, notes = _determine_branch_eligibility(b_name, tier, ahead, clean, dirty, cycles, base_branch, readiness_pass)
    is_provisional = eligibility == MergeEligibility.BLOCKED_CYCLE.value

    evidence_links = _discover_evidence_links(b_name, b_snap, changed_files, root)
    outcome = evaluate_branch_outcome(
        branch=b_name, tier=tier, changed_files=changed_files, repo_root=root,
        evidence_links=evidence_links, worktree_path=wt_path,
        branch_sha=b_snap.head_sha, base_sha=base_sha,
    )
    if outcome.measurement_evidence:
        evidence_links.append({"label": "Benefit Measurement Receipt", "url": f"file://{outcome.measurement_evidence}"})

    wt_path_obj = Path(wt_path) if wt_path else None
    topo = analyze_branch_file_diffs(b_name, root, base=base_branch, worktree_path=wt_path_obj)
    has_topo = bool(topo.branch_files)

    dirty_files_count = 0
    dirty_files: List[str] = []
    if wt_path_obj and wt_path_obj.exists():
        from tools.pr_priority_facts import fetch_worktree_dirty_numstat
        dirty_numstat = fetch_worktree_dirty_numstat(str(wt_path_obj))
        dirty_files_count = len(dirty_numstat)
        dirty_files = list(dirty_numstat.keys())

    from .code_review import compute_code_review
    code_rev = compute_code_review(
        b_name,
        base_branch=base_branch,
        root=root,
        use_cache=True,
        clean_merge=priority_clean,
        worktree_info={b_name: {"path": wt_path}} if wt_path else None,
    )
    code_rev_dict = code_rev.to_dict() if code_rev else {}
    sig_dict = code_rev_dict.get("signals") or {}

    from .diff_semantics import classify_branch_diff
    diff_sem = classify_branch_diff(
        changed_files + dirty_files,
        branch=b_name,
        open_waves=b_snap.open_waves,
        completed_waves=b_snap.completed_waves,
        clean_merge=priority_clean,
        behind=priority_behind,
        repo_root=root,
    )
    diff_cat = diff_sem.category
    deliverable_dirs = diff_sem.deliverable_dirs
    has_pub_receipt = diff_sem.has_publication_receipt

    item = {
        "branch": b_name, "tier": tier, "ahead": ahead, "behind": behind, "clean_merge": clean, "dirty": dirty,
        "head_sha": b_snap.head_sha, "base_sha": base_sha,
        "changed_files": changed_files, "worktree_path": wt_path,
        "dirty_files_count": dirty_files_count, "dirty_files": dirty_files,
        "benefit_category": outcome.benefit_category,
        "readiness_receipt_pass": readiness_pass,
        "value_score": getattr(outcome, "criterion_score", 0.0) or val_score,
        "risk_score": risk_score, "risk_tier": risk_tier,
        "rule_id": meta.get("rule_id", "R6.misc"), "scope": meta.get("scope", "misc"),
        "domain_impact": meta.get("explanation", ""), "explanation": meta.get("explanation", ""),
        "readiness": b_snap.readiness, "open_waves": b_snap.open_waves, "completed_waves": b_snap.completed_waves,
        "merge_eligibility": eligibility, "eligibility_reasons": notes,
        "hard_prerequisites": hard_graph.get(b_name, []), "execution_sequence_index": None,
        "next_action": "", "evidence_links": evidence_links,
        "overlap_files": topo.overlap_files if has_topo else None,
        "absorbed_files": topo.absorbed_files if has_topo else [],
        "salvageable_files": topo.salvageable_files if has_topo else [],
        "in_main_pcts": topo.in_main_pcts if has_topo else {},
        "file_numstat": topo.file_numstat if has_topo else {},
        "code_review": code_rev_dict,
        "signals": sig_dict,
        "review_verdict": code_rev_dict.get("review_verdict", "—"),
        "diff_category": diff_cat,
        "deliverable_dirs": deliverable_dirs,
        "has_publication_receipt": has_pub_receipt,
    }
    from tools.pr_governance_core.verdict_verifier import verify_branch_verdict_and_disposition
    from .completion_state import evaluate_branch_completion_state

    v_res = verify_branch_verdict_and_disposition(item, root=root)
    item["action"] = item["disposition_action"] = v_res.verified_disposition
    item["review_verdict"] = v_res.verified_review_verdict
    act_state = evaluate_branch_completion_state(item)
    item["completion_state"], item["is_completed"] = act_state.completion_state, act_state.is_completed
    item["is_in_progress"], item["in_progress_reasons"] = act_state.is_in_progress, act_state.in_progress_reasons
    return item, is_provisional


def _build_worktree_items(worktrees: List[Any], repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    items = []
    root = (repo_root or ROOT).resolve()
    for wt in worktrees:
        if getattr(wt, "is_registered", False) and not getattr(wt, "is_external", False):
            continue
        wt_path_str = getattr(wt, "path", "")
        wt_dir = Path(wt_path_str).resolve() if wt_path_str and Path(wt_path_str).is_dir() else None
        evidence_links: List[Dict[str, str]] = []
        if wt_dir:
            if (wt_dir / "output").is_dir():
                evidence_links.append({"label": "Deliverables", "url": f"file://{(wt_dir / 'output').resolve()}"})
            if (wt_dir / "implementation_plan.md").is_file():
                evidence_links.append({"label": "Plan", "url": f"file://{(wt_dir / 'implementation_plan.md').resolve()}"})
            if not evidence_links:
                evidence_links.append({"label": "Worktree", "url": f"file://{wt_dir}"})
        items.append({
            "branch": wt.name, "tier": "W", "ahead": 0, "behind": 0, "clean_merge": False, "dirty": wt.is_dirty,
            "worktree_path": wt_path_str, "changed_files": [],
            "value_score": 10.0 if "DELIVERABLES" in wt.classification else 1.0, "risk_score": 0, "risk_tier": 0,
            "rule_id": "R0.worktree", "scope": "worktree", "domain_impact": f"{wt.classification}: {wt.unique_files_count} unique files",
            "explanation": f"Worktree coverage ({wt.classification})",
            "readiness": "📂 Orphan Dir" if "ORPHAN" in wt.classification else "🗄️ Unregistered",
            "open_waves": "—", "completed_waves": "—", "merge_eligibility": MergeEligibility.BLOCKED_WIP.value,
            "eligibility_reasons": ["Worktree directory preservation"], "hard_prerequisites": [], "execution_sequence_index": None,
            "next_action": f"Preserve worktree directory ({wt.classification})",
            "evidence_links": evidence_links,
        })
    return items


def _build_stash_items(stashes: List[Any]) -> List[Dict[str, Any]]:
    items = []
    for s in stashes:
        items.append({
            "branch": s.ref, "tier": "W", "ahead": 0, "behind": 0, "clean_merge": False, "dirty": False,
            "domain": "Stash", "benefit_category": "HOUSEKEEPING",
            "value_statement": f"Preserve stash: {s.subject}",
            "value_score": 1.0, "risk_score": 2, "risk_tier": 2, "rule_id": "R0.stash", "scope": "stash",
            "domain_impact": f"{s.classification}: {s.subject}", "explanation": f"Git stash ({s.classification})",
            "readiness": "📦 Stash", "open_waves": "—", "completed_waves": "—", "merge_eligibility": MergeEligibility.BLOCKED_WIP.value,
            "eligibility_reasons": ["Stash preservation"], "hard_prerequisites": [], "execution_sequence_index": None,
            "next_action": f"Inspect and preserve stash ({s.classification})",
            "evidence_links": [],
        })
    return items


def _enrich_item_outcomes(
    items: List[Dict[str, Any]],
    blocked: Dict[str, str],
    seq: List[str],
    reg: Dict[str, Any],
    root: Path,
) -> None:
    from tools.pr_governance_core.domain import classify_domain
    for idx, it in enumerate(items):
        b = it["branch"]
        if b in blocked:
            if it["merge_eligibility"] == MergeEligibility.ELIGIBLE.value:
                it["merge_eligibility"] = MergeEligibility.BLOCKED_PREREQUISITE.value
            it["eligibility_reasons"].append(blocked[b])
        if b in seq:
            it["execution_sequence_index"] = seq.index(b) + 1

        outcome = evaluate_branch_outcome(
            branch=b, tier=it.get("tier", "P3"), changed_files=it.get("changed_files", []),
            override_data=reg.get("overrides", {}).get(b), repo_root=root,
            evidence_links=it.get("evidence_links", []), worktree_path=it.get("worktree_path"),
            branch_sha=it.get("head_sha", ""), base_sha=it.get("base_sha", ""),
        )
        it["benefit_category"] = outcome.benefit_category
        it["outcome_metadata"] = outcome.to_dict()
        it["value_statement"] = outcome.value_statement()
        it["next_action"] = observer_next_action(it, blocked.get(b))
        if "domain" not in it:
            it["domain"] = classify_domain(b, changed_files=it.get("changed_files", []), repo_root=root)
        prev_it = items[idx - 1] if idx > 0 else None
        next_it = items[idx + 1] if idx + 1 < len(items) else None
        it["relative_order_reason"] = explain_relative_order(it, prev_it, next_it)
        it["ranking_rationale"] = it["relative_order_reason"]
        b_str, req_str = explain_blocker_and_evidence_requirement(it)
        it["blocker"] = b_str
        it["completion_evidence_requirement"] = req_str
        from tools.pr_governance_core.executive_render import detect_graph_mutated
        it["graph_mutated"] = detect_graph_mutated(it, repo_root=root)


def evaluate_queue_decisions(
    snapshot: Any,
    repo_root: Optional[Path] = None,
    base_branch: str = "main",
    rubric: Optional[Dict[str, Any]] = None,
    registry: Optional[Dict[str, Any]] = None,
    declared_prereqs: Optional[Dict[str, str]] = None,
    file_overlaps: Optional[Dict[str, List[str]]] = None,
    prior_snapshot_branches: Optional[List[Dict[str, Any]]] = None,
) -> QueueDecisionResult:
    """Evaluates work priority, progress, merge eligibility, and dependency sequencing for snapshot."""
    root = repo_root or ROOT
    rub = rubric or load_rubric()
    reg = registry or load_registry()

    branch_snapshots = getattr(snapshot, "branches", [])
    ahead_map = {b.branch: getattr(b, "ahead", 0) for b in branch_snapshots}
    branch_names = [b.branch for b in branch_snapshots]

    edges, hard_graph, cycles = detect_hard_dependencies(
        branch_names=branch_names, ahead_map=ahead_map, repo_root=root,
        base_ref=base_branch, declared_prereqs=declared_prereqs, file_overlaps=file_overlaps,
    )

    items: List[Dict[str, Any]] = []
    provisional_items: List[str] = []

    for b_snap in branch_snapshots:
        item, is_prov = _build_branch_item(b_snap, hard_graph, cycles, base_branch, reg, rub, root, getattr(snapshot, "base_sha", ""))
        items.append(item)
        if is_prov:
            provisional_items.append(b_snap.branch)

    items.extend(_build_worktree_items(getattr(snapshot, "worktrees", []), repo_root=root))
    items.extend(_build_stash_items(getattr(snapshot, "stashes", [])))

    sort_queue(items)
    assign_sub_ranks(items)

    candidate_items = [it for it in items if not it["tier"].startswith(("W", "D", "TOMBSTONE"))]
    seq, blocked = compute_topological_execution_sequence(candidate_items, hard_graph, cycles)
    _enrich_item_outcomes(items, blocked, seq, reg, root)

    policy_ver = rub.get("version", "2.1")
    transitions = compute_rank_transitions(prior_snapshot_branches or [], items, policy_version=policy_ver)

    return QueueDecisionResult(
        snapshot_observed_at=getattr(snapshot, "observed_at", datetime.now(timezone.utc).isoformat()),
        policy_version=policy_ver,
        branches=items,
        execution_sequence=seq,
        blocked_branches=blocked,
        cycles=cycles,
        provisional_items=provisional_items,
        hard_dependency_graph=hard_graph,
        rank_transitions=[t.to_dict() for t in transitions],
    )
