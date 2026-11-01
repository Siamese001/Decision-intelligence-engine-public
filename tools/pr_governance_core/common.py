"""
Common constants, regexes, and utility functions for PR governance.
"""
from __future__ import annotations

import functools
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def _find_repo_root() -> Path:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).parent.resolve()
    except Exception:
        pass
    return Path(__file__).resolve().parents[2]


ROOT = _find_repo_root()

CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([a-zA-Z0-9_\-./]+\))?:\s+.+$|"
    r"^Merge\s+.+$"
)

CONVENTIONAL_BRANCH_RE = re.compile(
    r"^(feat|feature|enhance|fix|ingest|audio|transcript|docs|style|refactor|perf|test|build|ci|chore|revert)/[a-zA-Z0-9_\-\.]+$"
)

GENERATED_ARTIFACT_PATTERNS = (
    "reports/pr_review_",
    "reports/pr_merge_",
    "reports/pr_queue_detail.json",
    "reports/readiness/",
    "reports/local_main_readiness_",
    "reports/testing_gaps_audit_report",
    "reports/qa_gates_report",
    "reports/test_timing_db",
    "reports/llm_judge_evaluation",
    "reports/final_release_decision",
    "reports/domain_classification_cache.json",
    "governance/decision_intelligence/REPO_TESTING_GAPS_AUDIT_REPORT",
)

PLAN_DOC_FILES = frozenset({"implementation_plan.md", "walkthrough.md"})


def is_plan_doc_file(path: str | Path) -> bool:
    """Checks whether a path refers to an implementation plan or walkthrough doc."""
    s = str(path).replace("\\", "/")
    if s in PLAN_DOC_FILES:
        return True
    p = Path(s)
    parts = p.parts
    return bool(parts and parts[0] == "plans" and p.suffix == ".md")


def resolve_branch_plan_path(branch: str, repo_root: Path | None = None) -> Path:
    """Resolves the canonical plan path for a branch, preferring plans/<slug>/ with legacy fallback."""
    root = repo_root or ROOT
    slug = slugify_branch(branch)
    branch_plan = root / "plans" / slug / "implementation_plan.md"
    if branch_plan.is_file():
        return branch_plan
    legacy_plan = root / "implementation_plan.md"
    if legacy_plan.is_file():
        return legacy_plan
    legacy_archive = root / "plans" / "_legacy" / "implementation_plan.md"
    if legacy_archive.is_file():
        return legacy_archive
    return branch_plan


REQUIRED_HOOKS = [
    ".githooks/pre-commit",
    ".githooks/commit-msg",
    ".githooks/pre-merge-commit",
    ".githooks/pre-push",
    ".githooks/post-checkout",
    ".githooks/post-merge",
]


_RESOLVED_PR_PYTHON: Optional[str] = None


def resolve_python() -> str:
    global _RESOLVED_PR_PYTHON
    if _RESOLVED_PR_PYTHON is not None:
        return _RESOLVED_PR_PYTHON

    if sys.prefix != sys.base_prefix and Path(sys.executable).is_file():
        _RESOLVED_PR_PYTHON = sys.executable
        return sys.executable

    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists() and os.access(venv_py, os.X_OK):
        _RESOLVED_PR_PYTHON = str(venv_py)
        return _RESOLVED_PR_PYTHON
    win_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if win_py.exists() and os.access(win_py, os.X_OK):
        _RESOLVED_PR_PYTHON = str(win_py)
        return _RESOLVED_PR_PYTHON
    _RESOLVED_PR_PYTHON = sys.executable
    return sys.executable


def run_git(args: List[str], check: bool = True, cwd: Path | None = None, timeout: float = 30.0) -> str:
    res = subprocess.run(
        ["git"] + args,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )
    return res.stdout.rstrip("\r\n")


def slugify_branch(branch: str) -> str:
    """Produces a clean filesystem slug, sanitizing prohibited model naming tokens (AGENTS.md Invariant 7)."""
    tokens = re.split(r"[^a-zA-Z0-9]+", branch)
    prohibited = {"gpt", "astra", "luna", "sol", "terra", "claude", "gemini", "llama", "sonnet", "haiku", "opus", "o1", "o3"}  # noqa: SSOT_EXEMPT
    filtered = [
        t for t in tokens
        if t and t.lower() not in prohibited and not any(t.lower().startswith(p) for p in ("gpt", "claude", "gemini", "llama"))
    ]
    return "_".join(filtered) or "feature"


def check_diff_syntax(diff_names: list[str], target_cwd: Path) -> Tuple[bool, Optional[str]]:
    """Performs early deterministic syntax prechecks on modified Python and JSON files."""
    import ast
    import json

    for name in diff_names:
        file_path = target_cwd / name
        if not file_path.is_file():
            continue
        if name.endswith(".py"):
            try:
                ast.parse(file_path.read_bytes(), filename=str(file_path))
            except SyntaxError as e:
                return False, f"Python syntax error in {name}:{e.lineno}: {e.msg}"
        elif name.endswith(".json"):
            if name.endswith(".transcript_cache.json"):
                continue
            try:
                json.loads(file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                return False, f"JSON syntax error in {name}:{e.lineno}: {e.msg}"
    return True, None


_MERGE_TREE_CACHE: dict[tuple[str, str], Tuple[bool, str]] = {}


def check_merge_conflict(base: str, target: str, cwd: Optional[Path] = None) -> Tuple[bool, str]:
    """Dry-run merge using git merge-tree --write-tree. Returns (is_clean, details)."""
    run_cwd = cwd or ROOT
    try:
        resolved_base = base
        resolved_target = target
        if base == "HEAD":
            proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=run_cwd, capture_output=True, text=True, check=False, timeout=30.0)
            if proc.returncode == 0 and proc.stdout.strip():
                resolved_base = proc.stdout.strip()
        if target == "HEAD":
            proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=run_cwd, capture_output=True, text=True, check=False, timeout=30.0)
            if proc.returncode == 0 and proc.stdout.strip():
                resolved_target = proc.stdout.strip()

        cache_key = (str(run_cwd), resolved_base, resolved_target)
        if cache_key in _MERGE_TREE_CACHE:
            return _MERGE_TREE_CACHE[cache_key]

        res = subprocess.run(
            ["git", "merge-tree", "--write-tree", resolved_base, resolved_target],
            cwd=run_cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
        if res.returncode == 0:
            out = (True, "")
        elif res.returncode == 1 and res.stdout.strip() and "not something we can merge" not in res.stderr:
            out = (False, res.stdout + ("\n" + res.stderr if res.stderr else ""))
        else:
            out = (False, f"UNKNOWN: git merge-tree exited with code {res.returncode}\n{res.stderr.strip()}")

        _MERGE_TREE_CACHE[cache_key] = out
        return out
    except Exception as e:
        return False, f"UNKNOWN: {e}"

def calculate_readiness_and_risk(info: dict, wt: dict, open_waves: str = "?", is_merged: bool = False) -> tuple[str, int]:
    if is_merged:
        return "✅ MERGED", 0
    if not info.get("clean_merge"):
        return "🚫 Blocked by Conflicts", 100
    if info.get("behind", 0) >= 20:
        return "⚠️ Severely Diverged", 80
    if wt and wt.get("dirty"):
        return "⏳ In Flight (Dirty)", 50
    if info.get("behind", 0) > 5:
        return "⏳ Rebase Needed", 30

    ahead = info.get("ahead", 0)
    if ahead <= 0:
        return "⏳ Authoring", 20

    if open_waves == "—":
        if info.get("readiness_receipt_pass"):
            return "🚀 Fast-Lane", 0
        return "⏳ Ready for Qualification", 10
    elif open_waves == "?" or "—" not in open_waves:
        return "⏳ Ready for Qualification", 10

    return "⏳ Ready for Review", 10


def get_immune_pids() -> set[int]:
    """Returns set of PIDs immune from ghost process reaping (current process, parent, process group)."""
    import os
    immune: set[int] = {os.getpid()}
    try:
        immune.add(os.getppid())
    except Exception:
        pass
    try:
        immune.add(os.getpgid(0))
    except Exception:
        pass
    return immune


def kill_ghost_processes(worktree_dir: Path, terminate_ghosts: bool = False) -> list[int]:
    """
    Safely audits and optionally terminates orphan processes holding descriptors in worktree_dir.
    Enforces PID ancestry immunity: current PID, parent PID, and process group leader are immune.
    Never sends signals to immune processes (Invariant I11 & Suicide Bug Prevention).
    """
    import os
    import signal
    import subprocess
    proc = subprocess.run(["lsof", "-t", "+D", str(worktree_dir)], capture_output=True, text=True, check=False, timeout=10.0)
    if not proc.stdout.strip():
        return []

    immune = get_immune_pids()
    target_pids: list[int] = []
    for line in set(proc.stdout.strip().splitlines()):
        try:
            pid = int(line.strip())
            if pid > 0 and pid not in immune:
                target_pids.append(pid)
        except ValueError:
            continue

    if not target_pids:
        return []

    if terminate_ghosts:
        for pid in target_pids:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"⚠️ Terminated orphan worktree process (PID {pid}).")
            except OSError:
                pass
    else:
        pid_strs = [str(p) for p in target_pids]
        print(f"ℹ️ Detected background process(es) holding descriptors in {worktree_dir}: {', '.join(pid_strs)} (not terminated).")

    return target_pids


@functools.lru_cache(maxsize=None)
def logic_digest(*module_files: str) -> str:
    """Short digest of rule/engine source files; part of every verdict cache key so rule changes invalidate cached verdicts."""
    h = hashlib.sha256(b"prq-cache-schema-v2")
    for name in module_files:
        try:
            h.update(Path(name).read_bytes())
        except OSError:
            h.update(name.encode("utf-8"))
    return h.hexdigest()[:10]


def compute_first_parent_sync(
    base_branch: str, eff_branch: str, cwd: Path | None = None
) -> Tuple[int, int]:
    """
    Computes (behind, ahead) commit counts along the mainline first-parent spine.

    Uses `merge-base` to find the fork point, then counts:
    - behind: `rev-list --first-parent --count <base_commit>..<base_branch>` (PR merges on main)
    - ahead:  `rev-list --count <base_commit>..<eff_branch>` (commits unique to branch)

    Returns (-1, -1) on git error.
    """
    root = cwd or ROOT
    try:
        base_res = run_git(["merge-base", base_branch, eff_branch], check=True, cwd=root).strip()
        if not base_res:
            return -1, -1
        behind_s = run_git(
            ["rev-list", "--first-parent", "--count", f"{base_res}..{base_branch}"],
            check=True,
            cwd=root,
        ).strip()
        ahead_s = run_git(
            ["rev-list", "--count", f"{base_res}..{eff_branch}"],
            check=True,
            cwd=root,
        ).strip()
        return int(behind_s), int(ahead_s)
    except Exception:
        return -1, -1

