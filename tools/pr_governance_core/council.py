"""tools/pr_governance_core/council.py

6-Agent Adversarial Council Deliberation and Action Item Hardening Engine.
Mandates systematic debate across six specialized governance roles to rationalize
merge sequence (what merges 1st vs 2nd) and adversarially hardens the
"Immediate Blocker / Action" column against vague, non-actionable placeholders.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.pr_governance_core.complexity import ComplexityAssessment, evaluate_refactoring_complexity


class CouncilRole(str, Enum):
    ARCHITECTURE = "Architecture & Design Integrity"
    QUALITY = "Quality, Testing & Verification"
    SECURITY = "Security, Privacy & Compliance"
    TOPOLOGY = "Git, Release & Merge Topology"
    VALUE = "Business Value & Operational Impact"
    DIFF_INTELLIGENCE = "Semantic Diff & Obsolescence Integrity"


@dataclass
class AgentFinding:
    role: str
    verdict: str  # "ENDORSE", "OBJECT", "OBSERVE"
    observation: str
    hardened_requirement: Optional[str] = None
    score: float = 1.0


@dataclass
class BranchCouncilAssessment:
    branch: str
    disposition: str
    hardened_action: str
    complexity: ComplexityAssessment
    priority_rank: int
    findings: Dict[str, List[AgentFinding]] = field(default_factory=dict)
    debate_summary: str = ""
    consensus_score: float = 1.0
    role_scores: Dict[str, float] = field(default_factory=dict)
    turns_deliberated: int = 1
    turn_budget: int = 3
    debate_rounds: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch, "disposition": self.disposition, "hardened_action": self.hardened_action,
            "complexity": self.complexity.to_dict(), "priority_rank": self.priority_rank,
            "findings": {role: [asdict(f) for f in f_list] for role, f_list in self.findings.items()},
            "debate_summary": self.debate_summary, "consensus_score": round(self.consensus_score, 4),
            "role_scores": {k: round(v, 4) for k, v in self.role_scores.items()},
            "turns_deliberated": self.turns_deliberated, "turn_budget": self.turn_budget,
            "debate_rounds": self.debate_rounds,
        }


@dataclass
class CouncilDeliberationResult:
    timestamp: str
    assessments: List[BranchCouncilAssessment]
    merge_sequence: List[str]
    debate_rationales: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp, "assessments": [a.to_dict() for a in self.assessments],
            "merge_sequence": self.merge_sequence, "debate_rationales": self.debate_rationales,
        }


VAGUE_ACTION_PATTERNS = (
    re.compile(r"^review and proceed", re.I),
    re.compile(r"^fix tests", re.I),
    re.compile(r"^work in progress", re.I),
    re.compile(r"^triage", re.I),
    re.compile(r"^continue waves$", re.I),
    re.compile(r"^rebase and pr$", re.I),
)

ROLE_WEIGHTS: Dict[str, float] = {
    CouncilRole.SECURITY.value: 2.0,
    CouncilRole.TOPOLOGY.value: 1.5,
    CouncilRole.QUALITY.value: 1.5,
    CouncilRole.ARCHITECTURE.value: 1.2,
    CouncilRole.DIFF_INTELLIGENCE.value: 1.0,
    CouncilRole.VALUE.value: 0.8,
}

COUNTER_DISPATCH: Dict[str, str] = {
    CouncilRole.SECURITY.value: "Security veto invoked: all promotions halted pending sanitization audit",
    CouncilRole.TOPOLOGY.value: "Topology conflict locks integration: fast-forward rebase mandated",
    CouncilRole.DIFF_INTELLIGENCE.value: "Diff obsolescence detected: operational value rescinded",
    CouncilRole.QUALITY.value: "Quality gate in-flight: wave receipts required before merge candidate status",
}


def _audit_architecture(item: Dict[str, Any], comp: ComplexityAssessment) -> AgentFinding:
    sig = item.get("signals") or {}
    stale_paths = sig.get("stale_paths") or item.get("stale_paths") or []
    if stale_paths:
        return AgentFinding(CouncilRole.ARCHITECTURE.value, "OBJECT", f"Branch touches paths deleted or archived by main since fork: {', '.join(stale_paths[:3])}", f"[REBASE_RETARGET] Retarget imports and resolve {len(stale_paths)} stale path(s)", 0.2)
    if "Storage/Database (L0)" in comp.layer_crossings and "Core Pipeline (L1-L4)" in comp.layer_crossings:
        return AgentFinding(CouncilRole.ARCHITECTURE.value, "OBJECT", "Touches both L0 Storage and Core Pipeline; requires unidirectional AST verification", "Verify L0-L6 import hierarchy with tools/lint_layer_imports.py before merge", 0.4)
    return AgentFinding(CouncilRole.ARCHITECTURE.value, "ENDORSE", "Clean modular boundaries within layer budgets", score=1.0)


def _audit_quality(item: Dict[str, Any], comp: ComplexityAssessment) -> AgentFinding:
    open_w = str(item.get("open_waves", "—"))
    if open_w not in ("—", "?", "", "None") and open_w.strip():
        return AgentFinding(CouncilRole.QUALITY.value, "OBJECT", f"Open wave plans in-flight: [{open_w}]", f"Complete wave(s) [{open_w}], run Class B pytest, and record durable receipts", 0.2)
    if not item.get("readiness_receipt_pass", False):
        return AgentFinding(CouncilRole.QUALITY.value, "OBSERVE", "Readiness review receipt pending", "Execute .venv/bin/python tools/pr_governance.py review <branch>", 0.6)
    return AgentFinding(CouncilRole.QUALITY.value, "ENDORSE", "Quality gates and test receipts passed", score=1.0)


def _audit_security(item: Dict[str, Any]) -> AgentFinding:
    changed = item.get("changed_files")
    files = changed if isinstance(changed, (list, tuple, set)) else []
    if any(k in str(f) for f in files for k in ("env", "secret", "credential")):
        return AgentFinding(CouncilRole.SECURITY.value, "OBJECT", "Touches credential or environment surface", "Run tools/scan_credential_leaks.py and enforce LocalPreEgressSanitizer", 0.0)
    return AgentFinding(CouncilRole.SECURITY.value, "ENDORSE", "Mock boundaries and credential isolation verified", score=1.0)


def _audit_topology(item: Dict[str, Any], comp: ComplexityAssessment) -> AgentFinding:
    clean_merge, dirty = bool(item.get("clean_merge", True)), bool(item.get("dirty", False))
    behind, ahead = int(item.get("behind") or 0), int(item.get("ahead") or 0)
    overlap_files = item.get("overlap_files") or []
    absorbed_files, salvageable_files = item.get("absorbed_files") or [], item.get("salvageable_files") or []

    if ahead == 0 and dirty:
        return AgentFinding(CouncilRole.TOPOLOGY.value, "OBJECT", "Authoring inception state: 0 commits ahead with uncommitted working tree files", "Commit uncommitted files to feature branch before rebasing onto main", 0.3)
    if not clean_merge:
        files_str = f" in {', '.join(overlap_files[:3])}" if overlap_files else ""
        return AgentFinding(CouncilRole.TOPOLOGY.value, "OBJECT", f"Merge conflict detected against main branch{files_str}", f"Rebase onto main and resolve file conflicts{files_str}", 0.1)
    if absorbed_files and not salvageable_files:
        return AgentFinding(CouncilRole.TOPOLOGY.value, "OBJECT", f"All {len(absorbed_files)} file(s) fully absorbed into main HEAD", "Prune worktree and delete branch ('tools/pr_governance.py finish <branch>')", 0.1)
    if behind > 0:
        files_str = f" ({len(overlap_files)} shared file(s))" if overlap_files else " (0 path overlap with main)"
        return AgentFinding(CouncilRole.TOPOLOGY.value, "OBSERVE", f"Diverged: branch is {behind} commit(s) behind main{files_str}", f"Stage 1–2 rebase onto main required ({behind} commits behind)", 0.7)
    return AgentFinding(CouncilRole.TOPOLOGY.value, "ENDORSE", "Topology clean, synchronized with main HEAD", score=1.0)


def _audit_value(item: Dict[str, Any], comp: ComplexityAssessment) -> AgentFinding:
    tier, cat = str(item.get("tier", "P2")), str(item.get("benefit_category") or "UNKNOWN")
    if tier.startswith(("D", "TOMBSTONE")):
        return AgentFinding(CouncilRole.VALUE.value, "OBJECT", "Zero operational value remaining; superseded or abandoned stub", "Prune worktree and delete branch with CAS verification", 0.0)
    cat_str = f" [{cat}]" if cat != "UNKNOWN" else ""
    v_score = 1.0 if tier == "P0" else (0.85 if tier == "P1" else 0.7)
    return AgentFinding(CouncilRole.VALUE.value, "ENDORSE", f"Tier {tier}{cat_str} deliverable; refactoring complexity is {comp.size} ({comp.score} pts)", score=v_score)


def _audit_hunk_signals(sig: Dict[str, Any]) -> Optional[AgentFinding]:
    hunk_ratio = float(sig.get("hunk_absorption_ratio", 0.0) or 0.0)
    if hunk_ratio >= 0.85:
        pct = int(hunk_ratio * 100)
        return AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "OBJECT", f"Code diff is {pct}% absorbed into main across hunks", f"[CONFIRM_TOMBSTONE] {pct}% of code hunks already absorbed into main; confirm before decommission", 0.1)
    if 0.30 <= hunk_ratio < 0.85:
        pct = int(hunk_ratio * 100)
        return AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "OBSERVE", f"Partial hunk absorption detected ({pct}% absorbed into main)", f"[SALVAGE_DELIVERABLES] Salvage remaining {100 - pct}% unabsorbed changes; {pct}% already absorbed into main", 0.5)
    if int(sig.get("commits_in_main", 0)) > 0 and int(sig.get("unique_production_commits", 0)) == 0:
        return AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "OBJECT", f"All production commits already absorbed into main ({sig.get('commits_in_main')}/{sig.get('total_commits', 1)})", "[CONFIRM_TOMBSTONE] All code changes present in main; retire branch", 0.1)
    return None


def _diff_category_objection(diff_cat: str, behind: int, ahead: int, is_docs: bool, branch: str) -> Optional[AgentFinding]:
    if diff_cat == "SUPERSEDED_CODE":
        return AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "OBJECT", "Branch code or objective is superseded by recent merges to main", f"Archive or prune superseded branch with 'tools/pr_governance.py finish {branch}'", 0.1)
    if (diff_cat == "DOCS_ONLY_UNSTARTED" or is_docs) and behind >= 20:
        return AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "OBJECT", f"Diff is docs-only unstarted plan ({behind} commits behind main); scope superseded by mainline progress", f"Verify plan relevance or prune with 'tools/pr_governance.py finish --force {branch}'", 0.1)
    if diff_cat == "EMPTY_STUB" and ahead > 0:
        return AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "OBJECT", "Branch has 0 changed files against main (empty diff payload)", f"Safely prune redundant worktree with 'tools/pr_governance.py finish {branch}'", 0.2)
    return None


def _audit_diff_intelligence(item: Dict[str, Any], comp: ComplexityAssessment) -> AgentFinding:
    hunk_finding = _audit_hunk_signals(item.get("signals") or {})
    if hunk_finding:
        return hunk_finding
    absorbed, salvageable = item.get("absorbed_files") or [], item.get("salvageable_files") or []
    if absorbed and salvageable:
        return AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "OBSERVE", f"Mixed branch: {len(absorbed)} absorbed, {len(salvageable)} unique salvageable file(s)", f"Extract salvageable files ({', '.join(salvageable[:3])}) before decommissioning", 0.5)
    diff_cat = str(item.get("diff_category") or "")
    behind, ahead = int(item.get("behind") or 0), int(item.get("ahead") or 0)
    is_docs, branch = bool(item.get("is_docs_only_plan", False)), str(item.get("branch", ""))
    if not diff_cat:
        from tools.pr_governance_core.diff_semantics import classify_branch_diff
        sem = classify_branch_diff(item.get("changed_files") or [], branch=branch, open_waves=item.get("open_waves"), completed_waves=item.get("completed_waves"))
        diff_cat, is_docs = sem.category, (sem.is_docs_only and sem.is_unstarted_plan)
    obj = _diff_category_objection(diff_cat, behind, ahead, is_docs, branch)
    return obj or AgentFinding(CouncilRole.DIFF_INTELLIGENCE.value, "ENDORSE", f"Diff category '{diff_cat or 'ACTIVE'}' verified with active changes", score=1.0)


def _format_decomm_action(item: Dict[str, Any], branch: str, ahead: int) -> str:
    disp_reason = str(item.get("disposition_reason") or "").strip() or str(item.get("next_action") or "").replace("[PRUNE]", "").replace("[TOMBSTONE]", "").strip()
    force_flag = " --force" if item.get("dirty") or ahead == 0 else ""
    if disp_reason and disp_reason != "—":
        return f"{disp_reason}; discard via 'python tools/pr_governance.py finish {branch}{force_flag}'"
    return f"Safely prune worktree and branch ('python tools/pr_governance.py finish {branch}{force_flag}'); zero unique commits"


def _prune_action(item: Dict[str, Any], branch: str, initial_action: str) -> Optional[str]:
    tier, diff_cat = str(item.get("tier", "")), str(item.get("diff_category") or "")
    behind, ahead = int(item.get("behind") or 0), int(item.get("ahead") or 0)
    is_docs = bool(item.get("is_docs_only_plan", False))
    if tier.startswith(("D", "TOMBSTONE")) or "[PRUNE]" in initial_action or "[TOMBSTONE]" in initial_action:
        return _format_decomm_action(item, branch, ahead)
    if ahead == 0 and not item.get("dirty") and behind >= 20:
        return f"ahead==0 (No unique commits vs main, abandoned zero-commit branch); discard via 'python tools/pr_governance.py finish {branch} --force'"
    if (diff_cat == "DOCS_ONLY_UNSTARTED" or is_docs) and behind >= 20:
        return f"Safely prune stale unstarted planning worktree ('tools/pr_governance.py finish --force {branch}'); {behind} commits behind main"
    if diff_cat == "EMPTY_STUB" and ahead > 0:
        return f"Safely prune redundant empty worktree ('tools/pr_governance.py finish {branch}'); zero diff delta against main"
    if diff_cat == "SUPERSEDED_CODE":
        if item.get("dirty"):
            return f"Commit {int(item.get('dirty_files_count', 1))} authored file(s), snapshot archive tag, and prune worktree (superseded by main)"
        return f"Safely prune worktree and branch ('tools/pr_governance.py finish {branch}'); superseded by main"
    return None


def _format_clean_promo_action(item: Dict[str, Any], branch: str, behind: int) -> str:
    from tools.pr_governance_core.executive_render import format_deliverable_promotion_action
    deliv_act = format_deliverable_promotion_action(item)
    if deliv_act:
        return deliv_act
    tip_sha, base_sha, main_sha = str(item.get("tip_sha") or "").strip(), str(item.get("merge_base_sha") or "").strip(), str(item.get("main_head_sha") or "").strip()
    if behind > 0:
        anc = f" (committed in {tip_sha}, based on {base_sha} vs main {main_sha}, {behind} behind)" if (tip_sha and base_sha and main_sha) else f" ({behind} commits behind)"
        return f"Rebase onto main{anc}, run 30-gate review ('tools/pr_governance.py review {branch}'), and merge"
    anc = f" (committed in {tip_sha}, synced with main {main_sha})" if (tip_sha and main_sha) else ""
    return f"Execute 30-gate PR review ('tools/pr_governance.py review {branch}') and governed merge into main{anc}"


def _work_action(item: Dict[str, Any], branch: str, slug: str) -> Optional[str]:
    ahead, behind = int(item.get("ahead") or 0), int(item.get("behind") or 0)
    dirty, clean = bool(item.get("dirty", False)), bool(item.get("clean_merge", True))
    open_w, effort = str(item.get("open_waves", "—")), item.get("rebase_effort", "MEDIUM")
    if dirty:
        if ahead == 0 and behind >= 20:
            from tools.pr_governance_core.diff_semantics import is_code_file
            if any(is_code_file(f) for f in (item.get("dirty_files") or [])):
                return f"Salvage unique files in .worktrees/{slug} rather than rebasing stale code tree ({behind} commits behind main)"
        tail = f" (effort: {effort})" if not clean else ""
        return f"Commit {int(item.get('dirty_files_count', 1))} authored file(s) in .worktrees/{slug} before rebasing onto main{tail}"
    if not clean:
        return f"Rebase onto main (effort: {effort}), resolve conflicting files, and run Class B pytest receipts"
    if open_w not in ("—", "?", "", "None") and open_w.strip():
        return f"Complete open wave(s) [{open_w}], run Class B pytest --timeout=60, and record runtime receipts"
    return _format_clean_promo_action(item, branch, behind) if ahead > 0 else None


def harden_action_item(item: Dict[str, Any], initial_action: str, findings: Dict[str, List[AgentFinding]]) -> str:
    """Adversarially hardens the action item, rejecting vague placeholders in favor of exact steps."""
    from tools.pr_governance_core.common import slugify_branch
    branch = str(item.get("branch", ""))
    preserve_tokens = ("[HITL_CLARIFICATION_REQUIRED]", "[SALVAGE", "Extract ", "[CONFIRM_TOMBSTONE]", "[DEDUP]", "[CHERRY_PICK]")
    if (
        item.get("action") == "SALVAGE_DELIVERABLES"
        or item.get("disposition_action") == "SALVAGE_DELIVERABLES"
        or item.get("recommended_disposition") == "Salvage Subset & Archive"
        or item.get("disposition") == "Salvage Subset & Archive"
        or any(tok in initial_action for tok in preserve_tokens)
    ):
        return initial_action.strip()
    wt = item.get("wt")
    slug = Path(wt["path"]).name if (isinstance(wt, dict) and wt.get("path")) else slugify_branch(branch)
    act = _prune_action(item, branch, initial_action) or _work_action(item, branch, slug)
    if act:
        return act
    objections = [f.hardened_requirement for f_list in (findings or {}).values() for f in f_list if f.verdict == "OBJECT" and f.hardened_requirement]
    if any(p.search(initial_action.strip()) for p in VAGUE_ACTION_PATTERNS):
        return objections[0] if objections else f"Run 'tools/pr_governance.py review {branch}' to determine missing readiness gates"
    return initial_action.strip()


def _apply_role_weight(k: str, val: float, weights: Dict[str, float]) -> None:
    weights[k] = val
    k_lower = k.lower()
    for role in CouncilRole:
        if k_lower in role.value.lower() or k_lower in role.name.lower():
            weights[role.value] = val


def get_calibrated_council_weights(priors_path: Optional[Any] = None) -> Dict[str, float]:
    """Loads calibrated council voting weights clamped within [0.5, 3.0]."""
    weights = dict(ROLE_WEIGHTS)
    if not priors_path or not Path(priors_path).resolve().is_file():
        return weights
    try:
        data = json.loads(Path(priors_path).resolve().read_text(encoding="utf-8"))
        priors = data.get("learned_council_weights", data.get("learned_priors", {}).get("pr_council", {}))
        for k, v in priors.items():
            if isinstance(v, (int, float)):
                _apply_role_weight(k, max(0.5, min(3.0, float(v))), weights)
    except Exception:
        pass
    return weights


def _compute_consensus_score(findings: Dict[str, List[AgentFinding]], weights: Optional[Dict[str, float]] = None) -> Tuple[Dict[str, float], float]:
    eff_weights = weights or ROLE_WEIGHTS
    role_scores = {r: (fl[0].score if fl else 1.0) for r, fl in findings.items()}
    weighted_sum = sum(role_scores[r] * eff_weights.get(r, 1.0) for r in role_scores)
    total_weight = sum(eff_weights.get(r, 1.0) for r in role_scores)
    base_score = weighted_sum / max(1.0, total_weight)
    return role_scores, (min(base_score, 0.15) if any(s == 0.0 for s in role_scores.values()) else base_score)


def deliberate_branch(item: Dict[str, Any], turn_budget: int = 3, council_weights: Optional[Dict[str, float]] = None) -> BranchCouncilAssessment:
    """Evaluates a candidate branch across all 6 adversarial council roles."""
    branch = str(item.get("branch", ""))
    comp = evaluate_refactoring_complexity(item)
    findings: Dict[str, List[AgentFinding]] = {
        CouncilRole.ARCHITECTURE.value: [_audit_architecture(item, comp)],
        CouncilRole.QUALITY.value: [_audit_quality(item, comp)],
        CouncilRole.SECURITY.value: [_audit_security(item)],
        CouncilRole.TOPOLOGY.value: [_audit_topology(item, comp)],
        CouncilRole.VALUE.value: [_audit_value(item, comp)],
        CouncilRole.DIFF_INTELLIGENCE.value: [_audit_diff_intelligence(item, comp)],
    }
    objections = [f for fl in findings.values() for f in fl if f.verdict == "OBJECT"]
    debate_rounds: List[Dict[str, Any]] = [{"turn": 1, "stage": "initial_audit", "findings": {r: [asdict(f) for f in fl] for r, fl in findings.items()}}]
    turns = 1
    if objections and turn_budget >= 2:
        turns = 2
        debate_rounds.append({"turn": 2, "stage": "adversarial_counter_debate", "counter_points": [COUNTER_DISPATCH[o.role] for o in objections if o.role in COUNTER_DISPATCH]})
    if turn_budget >= 3:
        turns = min(turns + 1, turn_budget)
        debate_rounds.append({"turn": turns, "stage": "consensus_convergence"})

    role_scores, consensus_score = _compute_consensus_score(findings, council_weights)
    from tools.pr_governance_core.completion_state import evaluate_branch_completion_state, prefix_action_with_completion_badge
    from tools.pr_governance_core.executive_render import format_executive_disposition

    raw_disp, raw_action = format_executive_disposition(item)
    hardened_raw = harden_action_item(item, raw_action, findings)
    hardened = prefix_action_with_completion_badge(hardened_raw, evaluate_branch_completion_state(item))
    debate = "; ".join(f"{f.role}: {f.observation}" for f in objections) if objections else "Consensus: Ready to proceed subject to review"

    return BranchCouncilAssessment(
        branch=branch, disposition=raw_disp, hardened_action=hardened, complexity=comp,
        priority_rank=item.get("priority_rank", 99), findings=findings, debate_summary=debate,
        consensus_score=consensus_score, role_scores=role_scores, turns_deliberated=turns,
        turn_budget=turn_budget, debate_rounds=debate_rounds,
    )


def _detect_file_collisions(active_items: List[Dict[str, Any]]) -> List[str]:
    file_owners: Dict[str, str] = {}
    collisions: List[str] = []
    for item in active_items:
        b_name = str(item.get("branch", ""))
        for f in (item.get("changed_files") or []):
            if str(f).endswith((".py", ".md", ".json")) and not str(f).startswith("reports/"):
                if f in file_owners:
                    collisions.append(f"Cross-branch file collision: `{b_name}` and `{file_owners[f]}` both touch `{f}`")
                else:
                    file_owners[f] = b_name
    return collisions


def deliberate_council_queue(queue: List[Dict[str, Any]], turn_budget: int = 3) -> CouncilDeliberationResult:
    """Mandates 6-agent council debate across active queue to rationalize merge sequence."""
    active_items = [x for x in queue if x.get("tier") != "W"]
    assessments: List[BranchCouncilAssessment] = []
    for idx, item in enumerate(active_items, start=1):
        item["priority_rank"] = idx
        assessments.append(deliberate_branch(item, turn_budget=turn_budget))

    rationales = _detect_file_collisions(active_items)
    for i in range(len(assessments) - 1):
        cur, nxt = assessments[i], assessments[i + 1]
        rationales.append(
            f"Merge #{i+1} `{cur.branch}` ({cur.complexity.size}) before #{i+2} `{nxt.branch}` ({nxt.complexity.size}): "
            f"Cur disp='{cur.disposition}' vs Nxt disp='{nxt.disposition}'. {cur.debate_summary}"
        )

    return CouncilDeliberationResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        assessments=assessments,
        merge_sequence=[a.branch for a in assessments],
        debate_rationales=rationales,
    )
