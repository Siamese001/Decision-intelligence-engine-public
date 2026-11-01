"""tools/pr_governance_core/verdict_verifier.py

Rigorous Per-Verdict File-Diff Verification and HITL Disposition Escalation Engine.
Enforces deterministic file-diff proof for every canonical value disposition
and ReviewVerdict, computing calibrated confidence scores (c_top, c_second)
and ambiguity margin delta = c_top - c_second. Whenever delta <= 0.20 or
conflicting file-diff signals exist (partial hunk absorption or dirty worktrees
mixing absorbed and unique files), marks requires_hitl_clarification=True with
a plain-English HITL question and confidence-calibrated options.
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.pr_governance_core.common import run_git

HITL_MARGIN_THRESHOLD: float = 0.20

CANONICAL_DISPOSITIONS: Tuple[str, ...] = (
    "Promote to Main",
    "Complete In-Flight Work",
    "Salvage Subset & Archive",
    "Hold / Blocked",
    "Decommission (No Value)",
)


@dataclass
class VerdictCandidateScore:
    disposition: str
    score: float
    tier: str
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VerdictVerificationResult:
    branch: str
    verified_disposition: str
    verified_review_verdict: str
    c_top: float
    c_second: float
    margin_delta: float
    confidence_tier: str
    requires_hitl_clarification: bool
    ambiguity_reasons: List[str] = field(default_factory=list)
    diff_proof_summary: str = ""
    absorbed_files: List[str] = field(default_factory=list)
    unabsorbed_files: List[str] = field(default_factory=list)
    in_main_pcts: Dict[str, float] = field(default_factory=dict)
    total_hunks: int = 0
    hunk_absorption_ratio: float = 0.0
    candidates: List[VerdictCandidateScore] = field(default_factory=list)
    hitl_question_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["candidates"] = [c.to_dict() for c in self.candidates]
        return d


def _score_to_tier(score: float) -> str:
    if score >= 0.80:
        return "HIGH"
    if score >= 0.50:
        return "MEDIUM"
    return "LOW"


def needs_operator_decision(c_top: float, c_second: float, margin_threshold: float = HITL_MARGIN_THRESHOLD) -> bool:
    """Return True when top-two disposition confidence margin is <= threshold or top confidence < 0.70."""
    return (c_top - c_second) <= margin_threshold or c_top < 0.70


def extract_untracked_worktree_hunks(worktree_path: Path) -> List[Any]:
    """Extracts synthetic unified diff hunks for untracked authored files in worktree_path."""
    from .diff_semantics import FileDiffHunkSpec
    from .worktree import is_ephemeral_worktree_path, parse_porcelain_v2_z

    extra_hunks: List[Any] = []
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=str(worktree_path), capture_output=True, check=False, timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout:
            return extra_hunks
        for entry in parse_porcelain_v2_z(proc.stdout):
            rel_p = entry.get("path", "") if entry.get("type") == "?" else ""
            if not rel_p or is_ephemeral_worktree_path(rel_p):
                continue
            full_p = worktree_path / rel_p
            if not full_p.is_file() or full_p.is_symlink():
                continue
            raw_lines = full_p.read_text(encoding="utf-8", errors="replace").splitlines()
            if raw_lines:
                extra_hunks.append(FileDiffHunkSpec(rel_p, 0, 0, 1, len(raw_lines), "untracked_file", raw_lines, []))
    except Exception:
        pass
    return extra_hunks


def collect_dirty_worktree_files(wt_dir: Optional[Path], existing_files: Set[str]) -> Set[str]:
    """Collects non-ephemeral dirty and untracked file paths in wt_dir not already in existing_files."""
    dirty_only: Set[str] = set()
    if not wt_dir or not wt_dir.is_dir():
        return dirty_only
    try:
        from .worktree import is_ephemeral_worktree_path, parse_porcelain_v2_z

        proc = subprocess.run(
            ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=str(wt_dir), capture_output=True, check=False, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout:
            for entry in parse_porcelain_v2_z(proc.stdout):
                p = entry.get("path", "")
                if p and not is_ephemeral_worktree_path(p) and p not in existing_files:
                    dirty_only.add(p)
    except Exception:
        pass
    return dirty_only


def compute_dirty_file_in_main_pct(path: str, wt_path: Path, base: str, root: Path) -> Tuple[float, Tuple[int, int]]:
    """Computes absorption percentage and (added, deleted) numstat for a dirty/untracked worktree file against base."""
    full_p = wt_path / path
    try:
        if not full_p.is_file() or full_p.is_symlink():
            if not full_p.is_symlink() and not full_p.exists():
                obj_type = run_git(["cat-file", "-t", f"{base}:{path}"], cwd=root, check=False).strip()
                return (1.0 if obj_type == "blob" else 0.0), (0, 0)
            return 0.0, (0, 0)
        is_tracked = bool(run_git(["ls-files", "--error-unmatch", path], cwd=wt_path, check=False).strip())
        if is_tracked:
            d = run_git(["diff", "-U0", "HEAD", "--", path], cwd=wt_path, check=False)
            if d.strip():
                added = [ln[1:].strip() for ln in d.splitlines() if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()]
                deleted = sum(1 for ln in d.splitlines() if ln.startswith("-") and not ln.startswith("---") and ln[1:].strip())
            else:
                added = []
                deleted = 0
        else:
            raw_text = full_p.read_text(encoding="utf-8", errors="replace")
            added = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            deleted = 0
        show_out = run_git(["show", f"{base}:{path}"], cwd=root, check=False)
        if not added:
            return (1.0 if show_out else 0.0), (0, deleted)
        if not show_out:
            return 0.0, (len(added), deleted)
        main_lines = {ln.strip() for ln in show_out.splitlines() if ln.strip()}
        return round(sum(1 for ln in added if ln in main_lines) / len(added), 4), (len(added), deleted)
    except Exception:
        return 0.0, (0, 0)


def _sanitize_branch_label(branch: str) -> str:
    cleaned = branch.replace("/", " ").replace("_", " ").replace("-", " ")
    for tok in ("feat ", "fix ", "chore ", "ingest ", "docs ", "test "):
        if cleaned.lower().startswith(tok):
            cleaned = cleaned[len(tok):]
            break
    return " ".join(cleaned.split()).strip() or "candidate branch"


def _build_hitl_question_payload(
    branch: str,
    c_top_cand: VerdictCandidateScore,
    c_sec_cand: VerdictCandidateScore,
    absorbed_count: int,
    unabsorbed_count: int,
    behind: int,
    has_conflicts: bool,
) -> Dict[str, Any]:
    from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder

    label = _sanitize_branch_label(branch)
    if absorbed_count > 0 and unabsorbed_count > 0:
        q = (
            f"The {label} branch has {absorbed_count} change set already in mainline "
            f"and {unabsorbed_count} unique change set remaining. Which action should we take?"
        )
        builder = QuestionBuilder(question=q[:300], metadata={"calibrate": False, "category": "branch_disposition"})
        builder.add_option(
            label=f"Salvage the {unabsorbed_count} unique change set into a clean branch",
            plain_english_summary="Salvage unique edits and retire the rest.",
            impact_statement="Preserves new tests and rules without duplicating mainline.",
            confidence_score=c_top_cand.score,
            recommended=True,
        )
        builder.add_option(
            label=f"Retire the entire {label} branch without salvaging",
            plain_english_summary=f"Discard the {unabsorbed_count} remaining local edits.",
            impact_statement="Removes queue clutter and keeps mainline unchanged.",
            confidence_score=c_sec_cand.score,
            recommended=False,
        )
        builder.add_option(
            label="Complete and promote all remaining local changes together",
            plain_english_summary="Keep every pending adjustment active for full review.",
            impact_statement="Preserves pending edits and requires manual conflict resolution across overlapping changes.",
            confidence_score=min(c_sec_cand.score, 0.25),
            recommended=False,
        )
        built = builder.build()
        return {"question": built.question, "options": list(built.display_options), "is_multi_select": False}

    q = (
        f"The {label} branch is {behind} updates behind mainline with conflicting changes. How should we proceed?"
        if (has_conflicts and behind >= 15)
        else f"The {label} branch has competing disposition signals between {c_top_cand.disposition} and {c_sec_cand.disposition}. Which path should we take?"
    )
    builder = QuestionBuilder(question=q[:300], metadata={"calibrate": False, "category": "branch_disposition"})
    builder.add_option(
        label=c_top_cand.disposition,
        plain_english_summary=c_top_cand.rationale,
        impact_statement="Enables the top-ranked branch disposition and updates queue state.",
        confidence_score=c_top_cand.score,
        recommended=True,
    )
    builder.add_option(
        label=c_sec_cand.disposition,
        plain_english_summary=c_sec_cand.rationale,
        impact_statement="Allows the alternative branch disposition and preserves queue safety.",
        confidence_score=c_sec_cand.score,
        recommended=False,
    )
    built = builder.build()
    return {"question": built.question, "options": list(built.display_options), "is_multi_select": False}


def _score_partial_absorption(
    abs_cnt: int, unabs_cnt: int, absorbed_files: List[str], unabsorbed_files: List[str], hunk_ratio: float, ahead: int, dirty: bool
) -> Optional[Tuple[List[VerdictCandidateScore], List[str]]]:
    if abs_cnt > 0 and unabs_cnt == 0:
        return [
            VerdictCandidateScore("Decommission (No Value)", 0.98, "HIGH", f"all {abs_cnt} branch file(s) are already absorbed or sealed in main"),
            VerdictCandidateScore("Hold / Blocked", 0.02, "LOW", "retains redundant absorbed branch"),
        ], []
    if abs_cnt > 0 and unabs_cnt > 0:
        amb = [
            f"Partial file absorption: {abs_cnt} file(s) already in main ({', '.join(absorbed_files[:2])}) "
            f"while {unabs_cnt} file(s) contain unabsorbed work ({', '.join(unabsorbed_files[:2])})"
        ]
        return [
            VerdictCandidateScore("Salvage Subset & Archive", 0.56, "MEDIUM", f"preserves {unabs_cnt} unique change set while retiring {abs_cnt} duplicate change set"),
            VerdictCandidateScore("Decommission (No Value)", 0.44, "MEDIUM", f"retires branch because core implementation ({abs_cnt} change set) is already in mainline"),
            VerdictCandidateScore("Complete In-Flight Work", 0.25, "LOW", "commits all local edits and resolves mainline overlap manually"),
        ], amb
    if 0.25 <= hunk_ratio < 0.85 and (ahead > 0 or dirty):
        pct = int(round(hunk_ratio * 100))
        sec_disp = "Decommission (No Value)" if hunk_ratio >= 0.55 else "Complete In-Flight Work"
        sec_rat = "retires mostly superseded work" if hunk_ratio >= 0.55 else "completes and rebases remaining edits"
        return [
            VerdictCandidateScore("Salvage Subset & Archive", 0.58, "MEDIUM", f"extracts remaining {100 - pct}% unique logic and retires duplicate changes"),
            VerdictCandidateScore(sec_disp, 0.45, "MEDIUM", sec_rat),
        ], [f"Partial hunk absorption ({pct}% of added lines already present in main)"]
    return None


def _is_decommission_candidate(
    ahead: int, behind: int, dirty: bool, has_open_waves: bool, review_verdict: str, diff_category: str, is_docs_plan: bool, abs_cnt: int, unabs_cnt: int, tier: str, next_action: str
) -> bool:
    if ahead == 0 and not dirty and not has_open_waves:
        return True
    if diff_category == "SUPERSEDED_CODE" or review_verdict == "SUPERSEDED_TOMBSTONE" or (abs_cnt > 0 and unabs_cnt == 0):
        return True
    if (diff_category == "DOCS_ONLY_UNSTARTED" or is_docs_plan) and behind >= 20 and not dirty:
        return True
    if diff_category == "EMPTY_STUB" and not dirty and not has_open_waves:
        return True
    return tier.startswith(("D", "TOMBSTONE")) or "[PRUNE]" in next_action or "[TOMBSTONE]" in next_action


def _evaluate_candidate_scores(item: Dict[str, Any], hunk_ratio: float, absorbed_files: List[str], unabsorbed_files: List[str], total_hunks: int) -> Tuple[List[VerdictCandidateScore], List[str]]:
    ahead, behind = int(item.get("ahead") or 0), int(item.get("behind") or 0)
    dirty, clean_merge = bool(item.get("dirty", False)), bool(item.get("clean_merge", True))
    open_waves = str(item.get("open_waves", "—"))
    has_open = open_waves not in ("—", "", "None", "?") and bool(open_waves.strip())
    rev = str(item.get("review_verdict", "—"))
    diff_cat, is_docs = str(item.get("diff_category") or ""), bool(item.get("is_docs_only_plan", False))
    tier, next_act = str(item.get("tier", "")), str(item.get("next_action") or "")

    partial = _score_partial_absorption(len(absorbed_files), len(unabsorbed_files), absorbed_files, unabsorbed_files, hunk_ratio, ahead, dirty)
    if partial is not None:
        return partial

    if _is_decommission_candidate(ahead, behind, dirty, has_open, rev, diff_cat, is_docs, len(absorbed_files), len(unabsorbed_files), tier, next_act):
        return [
            VerdictCandidateScore("Decommission (No Value)", 0.96, "HIGH", "all changes are already in mainline or zero unique commits exist"),
            VerdictCandidateScore("Hold / Blocked", 0.04, "LOW", "retains obsolete branch unnecessarily"),
        ], []

    if rev in ("GOVERNANCE_BLOCK", "SPLIT_REQUIRED", "REBASE_RETARGET"):
        return [
            VerdictCandidateScore("Hold / Blocked", 0.91, "HIGH", "halts promotion until governance, split, or stale path blockers are remediated"),
            VerdictCandidateScore("Complete In-Flight Work", 0.09, "LOW", "requires structural remediation before normal wave completion"),
        ], []

    if dirty or has_open or "[COMMIT_FIRST]" in next_act:
        if ahead == 0 and dirty and behind >= 50 and total_hunks == 0:
            return [
                VerdictCandidateScore("Complete In-Flight Work", 0.55, "MEDIUM", "preserves local untracked or binary files for inspection"),
                VerdictCandidateScore("Decommission (No Value)", 0.45, "MEDIUM", "retires stale workspace with zero code hunks"),
            ], [f"Dirty worktree ({behind} commits behind main) has zero extractable code hunks"]
        return [
            VerdictCandidateScore("Complete In-Flight Work", 0.92, "HIGH", "commits active unabsorbed work or finishes open waves before promotion"),
            VerdictCandidateScore("Promote to Main", 0.08, "LOW", "cannot promote while uncommitted edits or open waves remain"),
        ], []

    if ahead > 0:
        rat = "promotes verified unique branch commits into mainline" if clean_merge else "rebases unique commits and resolves conflicts for mainline promotion"
        return [
            VerdictCandidateScore("Promote to Main", 0.93 if clean_merge else 0.86, "HIGH", rat),
            VerdictCandidateScore("Hold / Blocked" if not clean_merge else "Complete In-Flight Work", 0.14 if not clean_merge else 0.07, "LOW", "defers promotion pending conflict resolution"),
        ], []

    return [
        VerdictCandidateScore("Decommission (No Value)", 0.90, "HIGH", "zero unique commits or files remain"),
        VerdictCandidateScore("Hold / Blocked", 0.10, "LOW", "retains empty branch"),
    ], []


def verify_branch_verdict_and_disposition(item: Dict[str, Any], root: Optional[Path] = None) -> VerdictVerificationResult:
    """Verifies a branch queue item's disposition and review verdict against its file-diff evidence."""
    branch = str(item.get("branch", ""))
    code_rev = item.get("code_review") or {}
    signals = code_rev.get("signals") if isinstance(code_rev, dict) else getattr(code_rev, "signals", None)
    sig_dict = signals if isinstance(signals, dict) else (signals.to_dict() if hasattr(signals, "to_dict") else (item.get("signals") or {}))

    total_hunks = len(sig_dict.get("file_hunks") or [])
    hunk_ratio = float(sig_dict.get("hunk_absorption_ratio", 0.0) or 0.0)
    absorbed_files = list(item.get("absorbed_files") or sig_dict.get("absorbed_files") or [])
    unabsorbed_files = list(item.get("salvageable_files") or sig_dict.get("unabsorbed_files") or [])
    in_main_pcts = dict(item.get("in_main_pcts") or sig_dict.get("in_main_pcts") or {})

    # Apply domain-aware container and ephemeral receipt filtering
    if root is not None and unabsorbed_files:
        from tools.pr_governance_core.container_triage import filter_topology_absorbed_files
        all_branch_files = absorbed_files + unabsorbed_files
        absorbed_files, unabsorbed_files, in_main_pcts = filter_topology_absorbed_files(
            all_branch_files, root, absorbed_files, unabsorbed_files, in_main_pcts
        )
        item["absorbed_files"], item["salvageable_files"], item["in_main_pcts"] = absorbed_files, unabsorbed_files, in_main_pcts

    candidates, ambiguity_reasons = _evaluate_candidate_scores(item, hunk_ratio, absorbed_files, unabsorbed_files, total_hunks)
    c_top = round(candidates[0].score, 4)
    c_second = round(candidates[1].score if len(candidates) > 1 else 0.0, 4)
    margin_delta = round(c_top - c_second, 4)
    requires_hitl = needs_operator_decision(c_top, c_second) or bool(ambiguity_reasons)

    verified_disp = candidates[0].disposition
    verified_rev = str(item.get("review_verdict", "—"))
    if verified_disp == "Decommission (No Value)" and (hunk_ratio >= 0.85 or (absorbed_files and not unabsorbed_files)):
        verified_rev = "SUPERSEDED_TOMBSTONE"
        item["tier"], item["recommended_disposition"], item["disposition"], item["review_verdict"] = "D.1", verified_disp, verified_disp, verified_rev
    elif verified_disp == "Salvage Subset & Archive" and verified_rev in ("—", "SUPERSEDED_TOMBSTONE", "MERGE_READY"):
        verified_rev = "REVIEW_READY"

    ahead, behind = int(item.get("ahead") or 0), int(item.get("behind") or 0)
    dirty, clean_merge = bool(item.get("dirty", False)), bool(item.get("clean_merge", True))
    diff_proof = (
        f"ahead={ahead}, behind={behind}, dirty={dirty}, hunks={total_hunks}, "
        f"hunk_absorption={int(round(hunk_ratio * 100))}%, absorbed_files={len(absorbed_files)}, "
        f"unabsorbed_files={len(unabsorbed_files)}, c_top={c_top:.2f}, c_second={c_second:.2f}, delta={margin_delta:.2f}"
    )

    hitl_payload = (
        _build_hitl_question_payload(branch, candidates[0], candidates[1], len(absorbed_files), len(unabsorbed_files), behind, not clean_merge)
        if (requires_hitl and len(candidates) >= 2)
        else None
    )

    res = VerdictVerificationResult(
        branch=branch, verified_disposition=verified_disp, verified_review_verdict=verified_rev,
        c_top=c_top, c_second=c_second, margin_delta=margin_delta, confidence_tier=_score_to_tier(c_top),
        requires_hitl_clarification=requires_hitl, ambiguity_reasons=ambiguity_reasons,
        diff_proof_summary=diff_proof, absorbed_files=absorbed_files, unabsorbed_files=unabsorbed_files,
        in_main_pcts=in_main_pcts, total_hunks=total_hunks, hunk_absorption_ratio=round(hunk_ratio, 4),
        candidates=candidates, hitl_question_payload=hitl_payload,
    )
    item["verdict_verification"] = res.to_dict()
    item["requires_hitl_clarification"] = res.requires_hitl_clarification
    item["hitl_question_payload"] = res.hitl_question_payload
    item["recommended_disposition"] = res.verified_disposition
    item["disposition"] = res.verified_disposition
    return res
