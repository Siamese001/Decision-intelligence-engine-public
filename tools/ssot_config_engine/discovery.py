"""
tools/ssot_config_engine/discovery.py

Canonical file discovery for SSOT and anti-hardcoding AST linters.
Fixes the `"test" not in p.name` substring bug so production files like
`attestation.py`, `latest_release.py`, or `contest_resolver.py` are never
accidentally skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

DEFAULT_GOVERNED_DIRS: Tuple[str, ...] = (
    "config",
    "contracts",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "dual_pipeline",
    "storage",
    "scripts",
    "tools",
)


def is_test_or_excluded_path(rel_path: str) -> bool:
    """Return True only for genuine test files, package `__init__.py`, archives, or evals."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    parts = norm.split("/")
    fname = parts[-1] if parts else norm
    if fname == "__init__.py":
        return True
    if fname.startswith("test_") or fname.endswith("_test.py"):
        return True
    if any(p in ("tests", "archive", "evals", "__pycache__") for p in parts[:-1]):
        return True
    if norm.startswith("tools/qualify_prf_core/"):
        return True
    return False


def discover_governed_python_files(
    repo_root: Path,
    governed_dirs: Optional[Sequence[str]] = None,
) -> List[Path]:
    """Discover all governed production Python files under `repo_root`."""
    dirs = governed_dirs if governed_dirs is not None else DEFAULT_GOVERNED_DIRS
    root = repo_root.resolve()
    discovered: List[Path] = []
    for gdir in dirs:
        dp = root / gdir
        if not dp.is_dir():
            continue
        for p in sorted(dp.rglob("*.py")):
            try:
                rel = str(p.resolve().relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(p).replace("\\", "/")
            if not is_test_or_excluded_path(rel):
                discovered.append(p.resolve())
    return discovered
