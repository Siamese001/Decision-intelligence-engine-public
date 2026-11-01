"""Strict Git fact collectors shared by the PR Queue snapshot."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def git_result(repo_root: Path, args: List[str], timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
    """Return stdout or an explicit collection error; never turn failure into an empty fact."""
    try:
        from .common import run_git
        from unittest.mock import Mock
        if isinstance(run_git, Mock):
            return run_git(args, check=False, cwd=repo_root, timeout=float(timeout)), None
    except Exception:
        pass
    try:
        proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=False, timeout=timeout)
    except Exception as exc:
        return None, f"git {' '.join(args[:3])}: {exc}"
    if proc.returncode:
        return None, f"git {' '.join(args[:3])} exited {proc.returncode}: {proc.stderr.strip()}"
    return proc.stdout.strip(), None


def worktree_dirty(path: Path) -> Tuple[Optional[bool], str, Optional[str]]:
    output, error = git_result(path, ["status", "--porcelain=v1", "--untracked-files=all"])
    if error:
        return None, "", error
    return bool(output), hashlib.sha256((output or "").encode()).hexdigest(), None


def branch_git_facts(repo_root: Path, branch: str, base_branch: str) -> Tuple[Optional[int], Optional[int], Optional[bool], List[str]]:
    errors: List[str] = []
    sync, error = git_result(repo_root, ["rev-list", "--left-right", "--count", f"{base_branch}...{branch}"])
    if error:
        return None, None, None, [error]
    try:
        behind, ahead = (int(part) for part in (sync or "").split())
    except (ValueError, TypeError):
        return None, None, None, [f"Unparseable ahead/behind result for {branch}"]
    _, merge_error = git_result(repo_root, ["merge-tree", "--write-tree", base_branch, branch])
    if merge_error:
        try:
            proc = subprocess.run(["git", "merge-tree", "--write-tree", base_branch, branch], cwd=repo_root, capture_output=True, text=True, check=False, timeout=30)
        except Exception as exc:
            return ahead, behind, None, [f"Merge simulation unavailable for {branch}: {exc}"]
        if proc.returncode == 1 and proc.stdout.strip():
            clean = False
        else:
            errors.append(merge_error)
            clean = None
    else:
        clean = True
    return ahead, behind, clean, errors


def collect_stashes(repo_root: Path, live_branches: Set[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        probe = subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/stash"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=10)
        if probe.returncode == 1:
            return [], None
        if probe.returncode != 0:
            return [], f"Could not inspect stash reference: {probe.stderr.strip()}"
        proc = subprocess.run(["git", "log", "-g", "--format=%gd%x00%H%x00%gs", "refs/stash"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=10)
        if proc.returncode != 0:
            return [], f"Could not enumerate stash reflog: {proc.stderr.strip()}"
    except Exception as exc:
        return [], f"Could not enumerate stashes: {exc}"
    entries: List[Dict[str, Any]] = []
    branch_pattern = re.compile(r"(?:WIP on|On)\s+([^:]+):")
    for line in proc.stdout.splitlines():
        parts = line.split("\x00", 2)
        if len(parts) != 3:
            return [], "Stash reflog returned a malformed entry"
        ref, commit_sha, subject = parts
        match = branch_pattern.search(subject)
        branch = match.group(1).strip() if match else "main"
        classification = "STASH_MAIN" if branch == "main" else ("STASH_LIVE_BRANCH" if branch in live_branches else "STASH_DELETED_BRANCH")
        entries.append({"ref": ref, "commit_sha": commit_sha, "branch": branch, "subject": subject, "classification": classification})
    return entries, None
