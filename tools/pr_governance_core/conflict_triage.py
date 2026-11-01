"""
Conflict triage and rebase-effort estimation engine.

Classifies merge conflicts into 7 distinct classes and computes an evidence-based
rebase effort score and human-readable action strings.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from tools.pr_governance_core.common import (
    GENERATED_ARTIFACT_PATTERNS,
    PLAN_DOC_FILES,
    is_plan_doc_file,
    logic_digest,
    run_git,
)

IMPORT_RE = re.compile(
    r"^\s*(from\s+\S+\s+import\b|import\s+\S+|\)|[A-Za-z_][\w]*,?\s*(#.*)?$|pass$|"
    r"from contextlib import.*|with (contextlib\.)?suppress\(.*|with suppress\(.*|$)"
)


class ConflictClass(str, Enum):
    GENERATED, PLAN_DOC, STRUCTURAL = "GENERATED", "PLAN_DOC", "STRUCTURAL"
    SUPERSEDED, REGISTRY_APPEND = "SUPERSEDED", "REGISTRY_APPEND"
    LINT_IMPORT, LOGIC = "LINT_IMPORT", "LOGIC"


class EffortBand(str, Enum):
    TRIVIAL, LOW, MEDIUM, HIGH = "TRIVIAL", "LOW", "MEDIUM", "HIGH"


@dataclass(frozen=True)
class FileConflict:
    path: str
    conflict_class: ConflictClass
    hunks: int
    conflict_lines: int
    weight: float
    is_rename: bool = False
    is_modify_delete: bool = False
    in_main_pct: float = 0.0
    action_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "conflict_class": self.conflict_class.value}


@dataclass(frozen=True)
class ConflictTriageResult:
    branch: str
    base: str
    conflicted_files: List[FileConflict]
    total_files: int
    manual_files_count: int
    effort_score: float
    effort_band: EffortBand
    action_summary: str
    class_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "effort_band": self.effort_band.value, "conflicted_files": [f.to_dict() for f in self.conflicted_files]}


@dataclass(frozen=True)
class BranchDiffTopology:
    branch: str
    base: str
    branch_files: List[str]
    main_files: List[str]
    overlap_files: List[str]
    in_main_pcts: Dict[str, float]
    absorbed_files: List[str]
    salvageable_files: List[str]
    file_numstat: Dict[str, tuple[int, int]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def is_generated(path: str) -> bool:
    return any(p in path or path.startswith(p) for p in GENERATED_ARTIFACT_PATTERNS)


def is_plan_doc(path: str) -> bool:
    return is_plan_doc_file(path)


def _get_cache_dir(root: Path) -> Path:
    try:
        proc = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root, capture_output=True, text=True, check=False, timeout=30.0)
        d = Path(proc.stdout.strip()) / "pr_conflict_cache" if proc.returncode == 0 and proc.stdout.strip() else root / ".git" / "pr_conflict_cache"
    except Exception:
        d = root / ".git" / "pr_conflict_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _compute_in_main_pct(path: str, mb: str, branch: str, root: Path) -> float:
    try:
        d = run_git(["diff", "-U0", mb, branch, "--", path], cwd=root, check=False)
        added = [ln[1:].strip() for ln in d.splitlines() if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()]
        if not added:
            return 0.0
        main_lines = {ln.strip() for ln in run_git(["show", f"main:{path}"], cwd=root, check=False).splitlines() if ln.strip()}
        return sum(1 for ln in added if ln in main_lines) / len(added)
    except Exception:
        return 0.0


def _compute_main_churn(path: str, mb: str, branch: str, root: Path) -> bool:
    try:
        main_c = run_git(["log", "--oneline", f"{mb}..main", "--", path], cwd=root, check=False).count("\n")
        br_c = run_git(["log", "--oneline", f"{mb}..{branch}", "--", path], cwd=root, check=False).count("\n")
        return main_c >= 3 * max(1, br_c)
    except Exception:
        return False


def _inspect_file(path: str, tree_oid: str, mb: str, branch: str, mod_del_files: Set[str], root: Path) -> FileConflict:
    blob = run_git(["cat-file", "-p", f"{tree_oid}:{path}"], cwd=root, check=False)
    hunks, clines, in_hunk = 0, 0, False
    marker_paths: Set[str] = set()
    hunk_lines: List[str] = []

    for bline in blob.splitlines():
        if bline.startswith("<<<<<<< "):
            hunks += 1
            in_hunk = True
            m = bline[8:].strip()
            if ":" in m:
                marker_paths.add(m.split(":", 1)[1])
        elif bline.startswith(">>>>>>> "):
            in_hunk = False
            m = bline[8:].strip()
            if ":" in m:
                marker_paths.add(m.split(":", 1)[1])
        elif in_hunk and not bline.startswith("======="):
            clines += 1
            hunk_lines.append(bline)

    is_rename = len(marker_paths) > 1
    is_mod_del = path in mod_del_files or (hunks == 0 and clines == 0)
    is_import_only = bool(path.endswith(".py") and hunk_lines and all(IMPORT_RE.match(item) for item in hunk_lines))
    in_main_pct = _compute_in_main_pct(path, mb, branch, root)

    if is_generated(path):
        cls, weight, hint = ConflictClass.GENERATED, 0.0, f"[AUTO] take main's copy (git restore --source=main -- {path}), drop from branch"
    elif is_plan_doc(path):
        cls, weight, hint = ConflictClass.PLAN_DOC, 1.0, f"[DROP] plan docs are not merge-relevant; take main or remove {path} from branch"
    elif is_rename or is_mod_del:
        cls, weight, hint = ConflictClass.STRUCTURAL, (1.0 if is_import_only else 3.0), f"[DECIDE] structural conflict on {path}: confirm delete vs re-apply at renamed path"
    elif in_main_pct >= 0.80:
        cls, weight, hint = ConflictClass.SUPERSEDED, 0.5, f"[TAKE_MAIN] {path} changes already upstream ({in_main_pct * 100:.0f}% lines present)"
    elif path == "tools/run_ci_gates.py" or (path.endswith(".json") and "manifest" in path):
        cls, weight, hint = ConflictClass.REGISTRY_APPEND, 0.5, f"[UNION] keep both sides' entries for {path}, dedupe, preserve order"
    elif is_import_only:
        cls, weight, hint = ConflictClass.LINT_IMPORT, 0.5, f"[MECHANICAL] take main's side, then ruff check --fix && ruff format {path}"
    else:
        churn = 2.0 if _compute_main_churn(path, mb, branch, root) else 0.0
        cls, weight, hint = ConflictClass.LOGIC, 2.0 + min(clines / 10.0, 6.0) + churn, f"[MANUAL] semantic merge required for {path} ({hunks} hunks / {clines} lines)"

    return FileConflict(
        path=path, conflict_class=cls, hunks=hunks, conflict_lines=clines, weight=weight,
        is_rename=is_rename, is_modify_delete=is_mod_del, in_main_pct=round(in_main_pct, 4), action_hint=hint,
    )


def format_conflict_summary(
    total_files: int, band: EffortBand, manual_files: int, class_counts: Dict[str, int], behind: int, key_detail: str,
) -> str:
    if total_files == 0:
        return "[CLEAN] no merge conflicts"
    if band == EffortBand.TRIVIAL:
        return f"[RESOLVE_CONFLICTS:TRIVIAL] {total_files} generated file(s) — auto-resolvable"

    parts = [f"{cnt} {c.lower().replace('_', ' ')}" for c, cnt in sorted(class_counts.items(), key=lambda x: -x[1])]
    b_str = ", ".join(parts)
    if band in (EffortBand.LOW, EffortBand.MEDIUM):
        return f"[RESOLVE_CONFLICTS:{band.value}] {total_files} file(s): {b_str} — {manual_files} manual"
    if key_detail:
        return f"[RESOLVE_CONFLICTS:HIGH] {manual_files} manual file(s) ({key_detail}), {behind} commits behind — rebase early"
    return f"[RESOLVE_CONFLICTS:HIGH] {manual_files} manual file(s), {total_files} total ({b_str}) — rebase required"


def _load_cached(cache_file: Optional[Path]) -> Optional[ConflictTriageResult]:
    if not cache_file or not cache_file.exists():
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        files = [
            FileConflict(
                path=it["path"], conflict_class=ConflictClass(it["conflict_class"]),
                hunks=it["hunks"], conflict_lines=it["conflict_lines"], weight=it["weight"],
                is_rename=it.get("is_rename", False), is_modify_delete=it.get("is_modify_delete", False),
                in_main_pct=it.get("in_main_pct", 0.0), action_hint=it.get("action_hint", ""),
            )
            for it in data.get("conflicted_files", [])
        ]
        return ConflictTriageResult(
            branch=data["branch"], base=data["base"], conflicted_files=files, total_files=data["total_files"],
            manual_files_count=data["manual_files_count"], effort_score=data["effort_score"],
            effort_band=EffortBand(data["effort_band"]), action_summary=data["action_summary"],
            class_counts=data["class_counts"],
        )
    except Exception:
        return None


def _parse_merge_tree_output(lines: List[str]) -> tuple[Set[str], Set[str]]:
    stage_files: Set[str] = set()
    mod_del_files: Set[str] = set()
    in_messages = False

    for line in lines[1:]:
        if not line.strip():
            in_messages = True
            continue
        if not in_messages:
            parts = line.split("\t", 1)
            if len(parts) == 2:
                stage_files.add(parts[1].strip())
        elif "CONFLICT (modify/delete):" in line:
            m = re.search(r"CONFLICT \(modify/delete\):\s*(\S+)", line)
            if m:
                mod_del_files.add(m.group(1))

    return stage_files, mod_del_files


def _score_conflicts(
    stage_files: Set[str], tree_oid: str, mb: str, branch: str, base: str, mod_del_files: Set[str], eff_behind: int, root: Path
) -> ConflictTriageResult:
    conflicted_list, file_weights, lint_weights, manual_files = [], [], 0.0, 0
    class_counts: Dict[str, int] = {}
    key_details: List[str] = []

    for f_path in sorted(stage_files):
        fc = _inspect_file(f_path, tree_oid, mb, branch, mod_del_files, root)
        conflicted_list.append(fc)
        c_name = fc.conflict_class.value
        class_counts[c_name] = class_counts.get(c_name, 0) + 1
        if fc.conflict_class == ConflictClass.LINT_IMPORT:
            lint_weights += fc.weight
        else:
            file_weights.append(fc.weight)

        is_man = (fc.conflict_class == ConflictClass.LOGIC) or (
            fc.conflict_class == ConflictClass.STRUCTURAL and not fc.action_hint.startswith("[MECHANICAL]") and fc.weight >= 2.0
        )
        if is_man:
            manual_files += 1
            key_details.append(f"{Path(fc.path).name} {fc.conflict_lines} lines vs heavy main churn" if fc.conflict_lines >= 50 else Path(fc.path).name)

    total_score = sum(file_weights) + min(lint_weights, 3.0) + min(eff_behind / 100.0, 3.0)
    band = EffortBand.TRIVIAL if (total_score < 1.0 and manual_files == 0) else (
        EffortBand.LOW if total_score < 4.0 else (EffortBand.MEDIUM if total_score < 12.0 else EffortBand.HIGH)
    )

    action_summary = format_conflict_summary(
        total_files=len(conflicted_list), band=band, manual_files=manual_files, class_counts=class_counts,
        behind=eff_behind, key_detail=", ".join(key_details[:2]),
    )
    return ConflictTriageResult(
        branch=branch, base=base, conflicted_files=conflicted_list, total_files=len(conflicted_list),
        manual_files_count=manual_files, effort_score=round(total_score, 2), effort_band=band,
        action_summary=action_summary, class_counts=class_counts,
    )


def _resolve_eff_behind(behind: Optional[int], base: str, branch: str, mb: str, root: Path) -> int:
    if behind is not None:
        return behind
    try:
        ref = mb or branch
        b_cnt = run_git(["rev-list", "--first-parent", "--count", f"{ref}..{base}"], cwd=root, check=False)
        return int(b_cnt) if b_cnt.isdigit() else 0
    except Exception:
        return 0


def _investigate_result(branch: str, base: str, msg: str) -> ConflictTriageResult:
    return ConflictTriageResult(
        branch=branch, base=base, conflicted_files=[], total_files=1, manual_files_count=1,
        effort_score=15.0, effort_band=EffortBand.HIGH, action_summary=msg, class_counts={"UNKNOWN": 1},
    )


def triage_branch_conflicts(
    branch: str, root: Path, base: str = "main", behind: Optional[int] = None, use_cache: bool = True
) -> ConflictTriageResult:
    """Simulate merge, classify conflicts, and evaluate rebase effort."""
    try:
        base_sha, branch_sha = run_git(["rev-parse", base], cwd=root, check=False), run_git(["rev-parse", branch], cwd=root, check=False)
    except Exception:
        base_sha, branch_sha = "", ""

    cache_file = _get_cache_dir(root) / f"{base_sha[:12]}_{branch_sha[:12]}_{logic_digest(__file__)}.json" if (base_sha and branch_sha) else None
    if use_cache:
        cached = _load_cached(cache_file)
        if cached:
            return cached

    try:
        res = subprocess.run(["git", "merge-tree", "--write-tree", base, branch], cwd=root, capture_output=True, text=True, check=False, timeout=30.0)
    except Exception as exc:
        return _investigate_result(branch, base, f"[INVESTIGATE] merge simulation error ({exc}); inspect branch ref")

    if res.returncode == 0:
        clean_res = ConflictTriageResult(
            branch=branch, base=base, conflicted_files=[], total_files=0, manual_files_count=0,
            effort_score=0.0, effort_band=EffortBand.TRIVIAL, action_summary="[CLEAN] no merge conflicts", class_counts={},
        )
        if cache_file:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(clean_res.to_dict(), f, indent=2)
        return clean_res

    if res.returncode > 1 or not res.stdout.strip() or "not something we can merge" in res.stderr:
        return _investigate_result(branch, base, "[INVESTIGATE] merge simulation error (git merge-tree failed with error); inspect branch ref")

    lines = res.stdout.splitlines()
    tree_oid = lines[0] if lines else ""
    stage_files, mod_del_files = _parse_merge_tree_output(lines)
    mb = run_git(["merge-base", base, branch], cwd=root, check=False)
    eff_behind = _resolve_eff_behind(behind, base, branch, mb, root)
    triage_res = _score_conflicts(stage_files, tree_oid, mb, branch, base, mod_del_files, eff_behind, root)
    if cache_file:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(triage_res.to_dict(), f, indent=2)
        except Exception:
            pass
    return triage_res


def analyze_branch_file_diffs(
    branch: str, root: Path, base: str = "main", mb: Optional[str] = None, worktree_path: Optional[Path] = None,
) -> BranchDiffTopology:
    """Analyze file diffs, overlap with base, and absorption percentages across committed and dirty worktree files."""
    try:
        mb = mb or run_git(["merge-base", base, branch], cwd=root, check=False).strip()
    except Exception:
        mb = ""
    if not mb:
        return BranchDiffTopology(branch, base, [], [], [], {}, [], [], {})

    file_numstat: Dict[str, tuple[int, int]] = {}
    with subprocess.Popen(["git", "diff", "--numstat", f"{mb}..{branch}"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as p:
        try:
            comm_out, _ = p.communicate(timeout=30.0)
        except subprocess.TimeoutExpired:
            p.kill()
            comm_out, _ = p.communicate()
        for line in (comm_out or "").splitlines():
            parts = line.strip().split("\t", 2)
            if len(parts) == 3:
                file_numstat[parts[2]] = (int(parts[0]) if parts[0].isdigit() else 0, int(parts[1]) if parts[1].isdigit() else 0)
    branch_files = sorted(file_numstat.keys())
    if not branch_files:
        names = run_git(["diff", "--name-only", f"{mb}..{branch}"], cwd=root, check=False) or ""
        branch_files = sorted(f.strip() for f in names.splitlines() if f.strip())
        file_numstat = {f: (0, 0) for f in branch_files}

    from tools.pr_governance_core.verdict_verifier import collect_dirty_worktree_files, compute_dirty_file_in_main_pct
    wt_dir = Path(worktree_path) if worktree_path else None
    dirty_only = collect_dirty_worktree_files(wt_dir, set(file_numstat.keys()))
    if dirty_only:
        branch_files = sorted(set(branch_files) | dirty_only)

    m_diff = run_git(["diff", "--name-only", f"{mb}..{base}"], cwd=root, check=False) or ""
    main_files = sorted(f.strip() for f in m_diff.splitlines() if f.strip())
    main_set = set(main_files)
    del_diff = run_git(["diff", "--diff-filter=D", "--name-only", f"{mb}..{base}"], cwd=root, check=False) or ""
    deleted_on_main = set(f.strip() for f in del_diff.splitlines() if f.strip())

    overlap_files = [f for f in branch_files if f in main_set]
    in_main_pcts, absorbed, salvageable = {}, [], []
    for f in branch_files:
        if f in deleted_on_main:
            in_main_pcts[f] = 1.0
            absorbed.append(f)
            continue
        if f in dirty_only and wt_dir:
            pct, file_numstat[f] = compute_dirty_file_in_main_pct(f, wt_dir, base, root)
        else:
            pct = round(_compute_in_main_pct(f, mb, branch, root), 4) if f in main_set else 0.0
        in_main_pcts[f] = pct
        (absorbed if pct >= 0.85 else salvageable).append(f)

    return BranchDiffTopology(branch, base, branch_files, main_files, overlap_files, in_main_pcts, absorbed, salvageable, file_numstat)
