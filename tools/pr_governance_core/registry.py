"""
Worktree and output hierarchy registry governance for PR governance.
Enforces 5 Non-Negotiable Invariants on worktrees and universal subfolder structure.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List

from .common import CONVENTIONAL_BRANCH_RE, slugify_branch


def _git_env() -> Dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") or k in ("GIT_EXEC_PATH", "GIT_TEMPLATE_DIR")
    }


def verify_worktree_registry(repo_root: Path, worktrees: List[Dict[str, str]]) -> List[str]:
    """Audit worktree registry against 5 Non-Negotiable Invariants and core.bare freeze."""
    violations: List[str] = []

    # Barrier 2: core.bare freeze
    try:
        bare_res = subprocess.run(
            ["git", "config", "--bool", "core.bare"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
        )
        if bare_res.returncode == 0 and bare_res.stdout.strip() == "true":
            violations.append(
                "[CORE_BARE_VIOLATION] Repository has core.bare = true. "
                "Primary working repository must never be bare. Run: git config core.bare false"
            )
    except Exception as e:
        violations.append(f"Failed to check core.bare: {e}")

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            root_resolved = Path(proc.stdout.strip()).resolve().parent
        else:
            root_resolved = repo_root.resolve()
    except Exception:
        root_resolved = repo_root.resolve()

    canonical_worktrees_dir = (root_resolved / ".worktrees").resolve()

    for wt in worktrees:
        wt_path_str = wt.get("path")
        if not wt_path_str:
            continue
        wt_path = Path(wt_path_str).resolve()
        is_root = (wt_path == root_resolved)
        branch_ref = wt.get("branch", "")
        branch = branch_ref.replace("refs/heads/", "") if branch_ref else ""

        if is_root:
            continue

        # INV-WT-01: ROOT_ISOLATION - Secondary worktree cannot check out main or be named main_merge
        if branch in ("main", "master") or wt_path.name in ("main", "main_merge") or wt_path.name.endswith("_main_merge"):
            violations.append(
                f"[INV-WT-01 Violation] Branch '{branch or wt_path.name}' is checked out in secondary worktree at '{wt_path}'. "
                "Branch 'main' is strictly reserved for repository root."
            )

        # INV-WT-02: CANONICAL_LOCATION - Must reside directly under .worktrees/
        try:
            if wt_path.parent.resolve() != canonical_worktrees_dir:
                violations.append(
                    f"[INV-WT-02 Violation] Worktree at '{wt_path}' is not located directly in canonical directory '{canonical_worktrees_dir}'."
                )
        except Exception:
            pass

        # INV-WT-05: NO_DETACHED_OR_BARE
        if not branch:
            violations.append(
                f"[INV-WT-05 Violation] Worktree at '{wt_path}' is detached or has no named branch ref."
            )
        else:
            # INV-WT-04: CONVENTIONAL_BRANCH
            if not CONVENTIONAL_BRANCH_RE.match(branch):
                violations.append(
                    f"[INV-WT-04 Violation] Worktree branch '{branch}' at '{wt_path}' violates conventional branch format (e.g. feat/*, fix/*, ingest/*)."
                )

            # INV-WT-03: DETERMINISTIC_SLUG
            slug_with_prefix = slugify_branch(branch)
            slug_without_prefix = slugify_branch(branch.split("/", 1)[-1]) if "/" in branch else slug_with_prefix
            allowed_slugs = {
                slug_with_prefix,
                slug_without_prefix,
                slug_with_prefix.replace("_", "-"),
                slug_without_prefix.replace("_", "-"),
            }
            # Allow common descriptive suffixes (e.g. _pipeline) to be omitted in worktree directories
            for s in list(allowed_slugs):
                for suffix in ("_pipeline", "-pipeline", "_engine", "-engine"):
                    if s.endswith(suffix):
                        trimmed = s[:-len(suffix)]
                        allowed_slugs.add(trimmed)
                        allowed_slugs.add(trimmed.replace("_", "-"))
            if wt_path.name not in allowed_slugs:
                violations.append(
                    f"[INV-WT-03 Violation] Worktree directory '{wt_path.name}' does not match branch slug '{slug_with_prefix}'."
                )

    return violations


def verify_output_hierarchy(repo_root: Path) -> List[str]:
    """Verify output/ directory adheres to output/<project_name>/[<category>/]<MM-DD-YYYY>/ structure."""
    violations: List[str] = []
    output_dir = repo_root / "output"
    if not output_dir.is_dir():
        return violations

    date_regex = re.compile(r"^\d{2}-\d{2}-\d{4}$")
    for project_dir in output_dir.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        if project_dir.name == "transcript_cache":
            continue
        for child in project_dir.iterdir():
            if child.is_file() and not child.name.startswith("."):
                violations.append(f"Root file in project directory: {child.relative_to(repo_root)}")
            elif child.is_dir() and not child.name.startswith("."):
                if date_regex.match(child.name):
                    continue
                sub_dates = [sd for sd in child.iterdir() if sd.is_dir() and date_regex.match(sd.name)]
                if not sub_dates:
                    msg = "Invalid date directory" if not any(child.iterdir()) else "Directory contains no valid MM-DD-YYYY folders"
                    violations.append(f"{msg}: {child.name} at {child.relative_to(repo_root)}")
    return violations
