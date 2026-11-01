"""
tools/ssot_config_engine/discovery.py

Canonical file discovery for SSOT and anti-hardcoding AST linters.
Fixes the `"test" not in p.name` substring bug so production files like
`attestation.py`, `latest_release.py`, or `contest_resolver.py` are never
accidentally skipped.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

DEFAULT_GOVERNED_DIRS: Tuple[str, ...] = (
    ".",
    "automations",
    "config",
    "contracts",
    "governance",
    "migrations",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "dual_pipeline",
    "storage",
    "scripts",
    "tools",
    ".agents",
)


def is_test_or_excluded_path(rel_path: str) -> bool:
    """Return True only for genuine test files, package `__init__.py`, archives, evals, or worktrees."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    parts = norm.split("/")
    fname = parts[-1] if parts else norm
    if fname == "__init__.py":
        return True
    if fname.startswith("test_") or fname.endswith("_test.py"):
        return True
    if any(p in ("tests", "archive", "evals", "reports", "__pycache__", ".worktrees", ".git", ".venv") for p in parts[:-1]):
        return True
    if norm.startswith("tools/qualify_prf_core/"):
        return True
    return False


def discover_all_tracked_repo_files(repo_root: Path) -> List[Path]:
    """Discover every tracked file across the entire repository (`git ls-files` with filesystem fallback)."""
    root = repo_root.resolve()
    discovered: List[Path] = []
    seen: Set[Path] = set()
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        for line in res.stdout.splitlines():
            rel = line.strip()
            if not rel or rel.startswith(".worktrees/"):
                continue
            p = (root / rel).resolve()
            if p.is_file() and p not in seen:
                seen.add(p)
                discovered.append(p)
        if discovered:
            return sorted(discovered)
    except Exception:
        pass

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = str(p.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        if any(seg in rel.split("/") for seg in (".git", ".worktrees", ".venv", "__pycache__", ".pytest_cache")):
            continue
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            discovered.append(resolved)
    return discovered


def discover_governed_python_files(
    repo_root: Path,
    governed_dirs: Optional[Sequence[str]] = None,
) -> List[Path]:
    """Discover all governed production Python files across the entire `repo_root`."""
    root = repo_root.resolve()
    seen: Set[Path] = set()
    discovered: List[Path] = []

    use_full_repo = governed_dirs is None or tuple(governed_dirs) == DEFAULT_GOVERNED_DIRS or "." in governed_dirs
    if use_full_repo:
        tracked = [p for p in discover_all_tracked_repo_files(root) if p.suffix == ".py"]
        for p in tracked:
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(p).replace("\\", "/")
            if p not in seen and not is_test_or_excluded_path(rel):
                seen.add(p)
                discovered.append(p)
        for p in sorted(root.rglob("*.py")):
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(p).replace("\\", "/")
            if is_test_or_excluded_path(rel):
                continue
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                discovered.append(resolved)
        return sorted(discovered)

    for gdir in governed_dirs:
        dp = root / gdir
        if not dp.is_dir():
            continue
        for p in sorted(dp.rglob("*.py")):
            try:
                rel = str(p.resolve().relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(p).replace("\\", "/")
            resolved = p.resolve()
            if resolved not in seen and not is_test_or_excluded_path(rel):
                seen.add(resolved)
                discovered.append(resolved)
    return discovered
