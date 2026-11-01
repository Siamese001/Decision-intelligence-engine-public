"""tools/pr_governance_core/diff_semantics.py

Semantic Diff Classifier for Local PR Queue Governance.
Inspects branch diff composition (changed files, plan completion state, code vs doc ratio)
to prevent treating unstarted planning stubs or empty stubs as active code branches.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DOC_EXTENSIONS = {".md", ".rst", ".txt"}
CODE_EXTENSIONS = {".py", ".sh", ".sql", ".js", ".ts", ".html", ".css", ".toml", ".yaml", ".yml"}
UNSTARTED_PLAN_PATTERNS = (
    re.compile(r"status:\s*plan\s*only", re.IGNORECASE),
    re.compile(r"all\s+(?:ten|\d+)\s+waves\s+are\s+not_started", re.IGNORECASE),
    re.compile(r"waves?\s*are\s*not_started", re.IGNORECASE),
)


@dataclass
class DiffSemantics:
    category: str  # "EMPTY_STUB", "DOCS_ONLY_UNSTARTED", "DOCS_ONLY_ACTIVE", "CODE_ACTIVE", "SUPERSEDED_CODE", "DELIVERABLE_PACKAGE_READY", "DELIVERABLE_INCOMPLETE"
    is_docs_only: bool
    is_unstarted_plan: bool
    code_files_count: int
    doc_files_count: int
    code_files: List[str] = field(default_factory=list)
    doc_files: List[str] = field(default_factory=list)
    is_superseded: bool = False
    superseded_reason: Optional[str] = None
    deliverable_files_count: int = 0
    deliverable_dirs: List[str] = field(default_factory=list)
    has_deliverable_triad: bool = False
    has_publication_receipt: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileDiffHunkSpec:
    file_path: str
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    enclosing_symbol: str = ""
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    hunk_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def is_deliverable_file(file_path: str) -> bool:
    """Return True if the file path represents a production or intake deliverable under output/ or inputs/."""
    from .worktree import is_ephemeral_worktree_path

    if is_ephemeral_worktree_path(file_path):
        return False
    parts = Path(file_path).parts
    return bool(parts and parts[0] in ("output", "inputs", "input"))


def is_doc_file(file_path: str) -> bool:
    """Return True if the file path represents documentation or planning (excluding output/ deliverables)."""
    if is_deliverable_file(file_path):
        return False
    p = Path(file_path)
    if p.suffix.lower() in DOC_EXTENSIONS:
        return True
    parts = p.parts
    return bool(parts and parts[0] in ("docs", "documentation", "plans"))


def is_code_file(file_path: str) -> bool:
    """Return True if the file path represents executable code, tests, or system contracts."""
    if is_deliverable_file(file_path):
        return False
    p = Path(file_path)
    if p.suffix.lower() in CODE_EXTENSIONS:
        return True
    parts = p.parts
    if not parts:
        return False
    return parts[0] in (
        "dual_pipeline", "storage", "tools", "tests", "contracts",
        "config", "scripts", "agentic_core",
    )


def _check_unstarted_in_text(text: str) -> bool:
    return any(pat.search(text) for pat in UNSTARTED_PLAN_PATTERNS)


def _normalize_branch_tokens(branch_name: str) -> set[str]:
    tokens = re.split(r"[/_\-\s'\"`]+", branch_name.lower())
    ignore = {"feat", "fix", "docs", "chore", "ingest", "test", "archive", "main", "branch"}
    return {t for t in tokens if t and t not in ignore and len(t) > 1}


def _run_git_cmd(cmd: list[str], root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, timeout=5, cwd=str(root), capture_output=True, text=True, check=False)


def _check_cherry_supersession(
    branch: str,
    root: Path,
    git_runner: Optional[Callable[[list[str], Path], Any]] = None,
) -> Tuple[bool, Optional[str]]:
    runner = git_runner or _run_git_cmd
    try:
        cherry_res = runner(["git", "cherry", "main", branch], root)
        if cherry_res.returncode == 0:
            lines = [line.strip() for line in cherry_res.stdout.splitlines() if line.strip()]
            if lines and all(line.startswith("-") for line in lines):
                return True, "All commits are already cherry-picked or merged into main"
    except Exception:
        pass
    return False, None


def _calc_added_line_absorption(runner: Any, diff_args: list[str], fpath: str, root: Path) -> Tuple[int, int]:
    diff_p = runner(["git", "diff", "-U0", *diff_args, "--", fpath], root)
    if diff_p.returncode != 0 or not diff_p.stdout:
        return 0, 0
    added = [ln[1:].strip() for ln in diff_p.stdout.splitlines() if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()]
    if not added:
        return 0, 0
    show_p = runner(["git", "show", f"main:{fpath}"], root)
    main_lines = {ln.strip() for ln in show_p.stdout.splitlines() if ln.strip()} if show_p.returncode == 0 else set()
    return sum(1 for ln in added if ln in main_lines), len(added)


def _check_recent_merge_overlap(
    branch: str,
    code_files: set[str],
    root: Path,
    git_runner: Optional[Callable[[list[str], Path], Any]] = None,
) -> Tuple[bool, Optional[str]]:
    runner = git_runner or _run_git_cmd
    try:
        mb_res = runner(["git", "merge-base", "main", branch], root)
        merge_base = mb_res.stdout.strip() if mb_res.returncode == 0 else ""

        log_cmd = ["git", "log", "--merges", "--pretty=format:%H\t%s"]
        log_cmd.append(f"{merge_base}..main") if merge_base else log_cmd.extend(["-n", "30", "main"])
        merges_res = runner(log_cmd, root)
        if merges_res.returncode != 0 or not merges_res.stdout.strip():
            merges_res = runner(["git", "log", "-n", "30", "--merges", "--pretty=format:%H\t%s", "main"], root)

        if merges_res.returncode == 0 and merges_res.stdout.strip():
            branch_tokens = _normalize_branch_tokens(branch)
            for line in merges_res.stdout.splitlines():
                if not line.strip() or "\t" not in line:
                    continue
                m_hash, m_subject = line.strip().split("\t", 1)
                merge_tokens = _normalize_branch_tokens(m_subject)
                files_res = runner(["git", "diff", "--name-only", f"{m_hash}^1..{m_hash}"], root)
                if files_res.returncode != 0:
                    continue
                overlap_files = code_files.intersection({f.strip() for f in files_res.stdout.splitlines() if f.strip()})
                if len(overlap_files) >= 5 or (len(overlap_files) >= 2 and len(branch_tokens & merge_tokens) >= 2):
                    absorption_ratios = []
                    for fpath in overlap_files:
                        matched, total = _calc_added_line_absorption(runner, [f"{m_hash}^1", branch], fpath, root)
                        if total > 0:
                            absorption_ratios.append(matched / total)
                    avg_abs = sum(absorption_ratios) / max(len(absorption_ratios), 1) if absorption_ratios else 0.0
                    if avg_abs >= 0.85:
                        return (
                            True,
                            f"Superseded by merge {m_hash[:9]} ('{m_subject[:50]}...') with {len(overlap_files)} overlapping code files (hunk absorption: {avg_abs:.1%})",
                        )

        # Direct hunk survival check across code_files against main:<path>
        diff_range = f"{merge_base}...{branch}" if merge_base else branch
        tot_added, tot_abs = 0, 0
        for fpath in code_files:
            matched, total = _calc_added_line_absorption(runner, [diff_range], fpath, root)
            tot_added += total
            tot_abs += matched

        if tot_added > 0 and (tot_abs / tot_added) >= 0.85:
            return (
                True,
                f"Code changes superseded: {len(code_files)} file(s) with {tot_added} added line(s) (hunk absorption: {tot_abs / tot_added:.1%})",
            )
    except Exception:
        pass
    return False, None


def extract_file_hunks(
    merge_base: str,
    branch: str,
    root: Path,
    worktree_path: Optional[Path] = None,
    git_runner: Optional[Callable[[list[str], Path], Any]] = None,
) -> List[FileDiffHunkSpec]:
    """Extracts unified diff hunks with line numbers, enclosing AST symbols, and line deltas."""
    import hashlib

    runner = git_runner or _run_git_cmd
    hunks: List[FileDiffHunkSpec] = []
    try:
        diff_res = runner(["git", "diff", "--unified=3", "--no-color", f"{merge_base}...{branch}"], root)
        diff_text = diff_res.stdout if diff_res.returncode == 0 else ""
        if worktree_path and worktree_path.exists():
            wt_diff = runner(["git", "diff", "--unified=3", "--no-color", "HEAD"], worktree_path)
            if wt_diff.returncode == 0 and wt_diff.stdout:
                diff_text += "\n" + wt_diff.stdout

        current_file = ""
        current_hunk: Optional[FileDiffHunkSpec] = None
        hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(?:[ \t]*(.*))?$")

        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                if current_hunk:
                    hunks.append(current_hunk)
                    current_hunk = None
                parts = line.split(" ")
                if len(parts) >= 4 and parts[3].startswith("b/"):
                    current_file = parts[3][2:]
                continue
            if line.startswith("+++ b/"):
                current_file = line[6:].strip()
                continue
            if line.startswith("@@"):
                if current_hunk:
                    hunks.append(current_hunk)
                    current_hunk = None
                m = hunk_header_re.match(line)
                if m and current_file:
                    current_hunk = FileDiffHunkSpec(
                        file_path=current_file,
                        old_start=int(m.group(1)),
                        old_len=int(m.group(2)) if m.group(2) is not None else 1,
                        new_start=int(m.group(3)),
                        new_len=int(m.group(4)) if m.group(4) is not None else 1,
                        enclosing_symbol=(m.group(5) or "").strip(),
                    )
                continue
            if current_hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    current_hunk.added_lines.append(line[1:])
                elif line.startswith("-") and not line.startswith("---"):
                    current_hunk.removed_lines.append(line[1:])

        if current_hunk:
            hunks.append(current_hunk)

        if worktree_path and worktree_path.exists() and git_runner is None:
            from .verdict_verifier import extract_untracked_worktree_hunks
            existing_paths = {h.file_path for h in hunks}
            for uh in extract_untracked_worktree_hunks(worktree_path):
                if uh.file_path not in existing_paths:
                    hunks.append(uh)

        for h in hunks:
            h.hunk_digest = hashlib.sha256("\n".join(h.added_lines).encode("utf-8")).hexdigest()[:12]

    except Exception:
        pass
    return hunks


def detect_superseded_code(
    changed_files: Sequence[str],
    branch: Optional[str] = None,
    repo_root: Optional[Path] = None,
    clean_merge: Optional[bool] = None,
    behind: Optional[int] = None,
    git_runner: Optional[Callable[[list[str], Path], Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """Detect if branch changes or objectives are superseded by recent merges to main."""
    if not branch or branch in ("main", "master") or not changed_files:
        return False, None

    root = repo_root or Path.cwd()
    code_files = {f.strip() for f in changed_files if is_code_file(f)}
    if not code_files:
        return False, None

    is_cherry, reason = _check_cherry_supersession(branch, root, git_runner=git_runner)
    if is_cherry:
        return True, reason

    if clean_merge is False or (behind is not None and behind > 0):
        return _check_recent_merge_overlap(branch, code_files, root, git_runner=git_runner)

    return False, None


def _inspect_deliverable_files(deliverable_files: Sequence[str]) -> Tuple[List[str], bool, bool]:
    """Extract canonical deliverable directories, triad completeness (01, 02, 03), and publication receipts."""
    dirs: set[str] = set()
    has_01 = has_02 = has_03 = has_pub = False
    for f in deliverable_files:
        p = Path(f)
        parts = p.parts
        if parts and parts[0] == "output" and len(parts) >= 3:
            dirs.add("/".join(parts[: 4 if len(parts) >= 4 else 3]))
        if "attempts" not in parts:
            fn = p.name.lower()
            if fn.startswith("01_") and fn.endswith(".md") and not fn.endswith("_live_transcript.md"):
                has_01 = True
            elif fn.startswith("02_") and fn.endswith(".md"):
                has_02 = True
            elif fn.startswith("03_") and fn.endswith(".json"):
                has_03 = True
            elif fn in ("publication_coverage_receipt.json", "final_release_decision.json", "intake_package.json"):
                has_pub = True
    return sorted(dirs), (has_01 and has_02 and has_03), has_pub


def classify_branch_diff(
    changed_files: List[str],
    branch: Optional[str] = None,
    open_waves: Optional[str] = None,
    completed_waves: Optional[str] = None,
    file_content_getter: Optional[Callable[[str], str]] = None,
    clean_merge: Optional[bool] = None,
    behind: Optional[int] = None,
    repo_root: Optional[Path] = None,
    git_runner: Optional[Callable[[list[str], Path], Any]] = None,
) -> DiffSemantics:
    """Classifies a branch's diff into structured semantic categories."""
    if not isinstance(changed_files, (list, tuple, set)):
        changed_files = []
    cleaned = [f.strip() for f in changed_files if isinstance(f, str) and f.strip()]
    if not cleaned:
        return DiffSemantics(
            category="EMPTY_STUB", is_docs_only=False, is_unstarted_plan=False,
            code_files_count=0, doc_files_count=0, code_files=[], doc_files=[],
        )

    code_files = [f for f in cleaned if is_code_file(f)]
    doc_files = [f for f in cleaned if is_doc_file(f)]
    deliverable_files = [f for f in cleaned if is_deliverable_file(f)]
    deliv_dirs, has_triad, has_pub_receipt = _inspect_deliverable_files(deliverable_files)

    is_docs_only = len(code_files) == 0 and len(deliverable_files) == 0 and len(doc_files) > 0
    is_unstarted_plan = False

    if is_docs_only:
        # Check plan text if content getter is supplied
        if file_content_getter:
            for df in doc_files:
                try:
                    content = file_content_getter(df)
                    if _check_unstarted_in_text(content):
                        is_unstarted_plan = True
                        break
                except Exception:
                    pass

        # Check wave status indicators if content check was not conclusive
        if not is_unstarted_plan and open_waves:
            op = str(open_waves).strip()
            comp = str(completed_waves or "").strip()
            has_open = op not in ("—", "?", "None", "")
            has_no_done = comp in ("—", "?", "None", "", "0")
            if has_open and has_no_done:
                is_unstarted_plan = True

    is_superseded, superseded_reason = (False, None)
    if code_files:
        is_superseded, superseded_reason = detect_superseded_code(
            code_files, branch=branch, repo_root=repo_root, clean_merge=clean_merge, behind=behind, git_runner=git_runner,
        )

    if is_superseded:
        cat = "SUPERSEDED_CODE"
    elif code_files:
        cat = "CODE_ACTIVE"
    elif deliverable_files:
        cat = "DELIVERABLE_PACKAGE_READY" if (has_triad or has_pub_receipt) else "DELIVERABLE_INCOMPLETE"
    elif is_docs_only and is_unstarted_plan:
        cat = "DOCS_ONLY_UNSTARTED"
    elif is_docs_only:
        cat = "DOCS_ONLY_ACTIVE"
    else:
        cat = "CODE_ACTIVE"

    return DiffSemantics(
        category=cat, is_docs_only=is_docs_only, is_unstarted_plan=is_unstarted_plan,
        code_files_count=len(code_files), doc_files_count=len(doc_files),
        code_files=code_files, doc_files=doc_files,
        is_superseded=is_superseded, superseded_reason=superseded_reason,
        deliverable_files_count=len(deliverable_files), deliverable_dirs=deliv_dirs,
        has_deliverable_triad=has_triad, has_publication_receipt=has_pub_receipt,
    )

