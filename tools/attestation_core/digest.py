"""tools/attestation_core/digest.py

Deterministic Git tree digest calculation excluding volatile paths (reports/, .local/, etc.).
Produces an immutable SHA-256 tree digest representing code, tests, and governance.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Sequence

DEFAULT_EXCLUDE_PREFIXES: tuple[str, ...] = ("reports/", ".local/", ".git/")


def compute_tree_digest(
    repo_root: Path,
    ref: str = "HEAD",
    exclude_prefixes: Sequence[str] = DEFAULT_EXCLUDE_PREFIXES,
) -> str:
    """Computes a deterministic SHA-256 digest of git tree entries excluding specified prefixes.

    Runs `git ls-tree -r <ref>` to get all tracked blobs with their object hashes.
    Filters out any paths matching exclude_prefixes (e.g. reports/**).
    Sorts lexicographically and hashes the resulting canonical index representation.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-tree", "-r", ref],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=15.0,
        )
    except subprocess.CalledProcessError:
        # Fallback for fresh worktree or detached state without ref
        proc = subprocess.run(
            ["git", "ls-files", "-s"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=15.0,
        )

    canonical_lines: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format of git ls-tree: <mode> <type> <sha>\t<path>
        # Format of git ls-files -s: <mode> <sha> <stage>\t<path>
        if "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        path = path.strip()

        # Exclude volatile report/local paths
        if any(path.startswith(prefix) for prefix in exclude_prefixes):
            continue

        canonical_lines.append(f"{meta}\t{path}")

    canonical_lines.sort()
    hasher = hashlib.sha256()
    for item in canonical_lines:
        hasher.update(item.encode("utf-8"))
        hasher.update(b"\n")

    return hasher.hexdigest()


def compute_staged_tree_digest(
    repo_root: Path,
    exclude_prefixes: Sequence[str] = DEFAULT_EXCLUDE_PREFIXES,
) -> str:
    """Computes tree digest incorporating staged git index modifications."""
    proc = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        timeout=15.0,
    )
    canonical_lines: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        path = path.strip()
        if any(path.startswith(prefix) for prefix in exclude_prefixes):
            continue
        canonical_lines.append(f"{meta}\t{path}")

    canonical_lines.sort()
    hasher = hashlib.sha256()
    for item in canonical_lines:
        hasher.update(item.encode("utf-8"))
        hasher.update(b"\n")

    return hasher.hexdigest()


def compute_git_tree_oid(repo_root: Path, ref: str = "HEAD") -> str:
    """Returns the native Git tree OID (`<ref>^{tree}`) for `ref`, or empty string on failure."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", f"{ref}^{{tree}}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception as exc:
        _ = exc
    return ""


def compute_branch_delta_digest(
    repo_root: Path,
    ref: str = "HEAD",
    base_ref: str = "main",
    exclude_prefixes: Sequence[str] = DEFAULT_EXCLUDE_PREFIXES,
) -> str:
    """Computes a deterministic SHA-256 digest of a branch's changed blobs relative to its merge-base with `base_ref`.

    Because a pure disjoint rebase onto an updated `main` preserves the exact mode, blob OID,
    and path of every file modified by the branch relative to its merge-base, this digest
    remains invariant across disjoint rebases while changing immediately if any branch file is edited.
    """
    try:
        mb_proc = subprocess.run(
            ["git", "merge-base", base_ref, ref],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        merge_base = mb_proc.stdout.strip() if mb_proc.returncode == 0 else ""
        if not merge_base:
            return ""
        diff_proc = subprocess.run(
            ["git", "diff-tree", "-r", "--no-commit-id", merge_base, ref],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15.0,
        )
        if diff_proc.returncode != 0:
            return ""
        canonical_lines: list[str] = []
        for line in diff_proc.stdout.splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            meta, path = line.split("\t", 1)
            path = path.strip()
            if any(path.startswith(prefix) for prefix in exclude_prefixes):
                continue
            # Format of diff-tree: :<old_mode> <new_mode> <old_blob> <new_blob> <status>\t<path>
            parts = meta.split()
            if len(parts) >= 5:
                new_mode = parts[1]
                new_blob = parts[3]
                status = parts[4][:1]
                canonical_lines.append(f"{status} {new_mode} {new_blob}\t{path}")
            else:
                canonical_lines.append(f"{meta}\t{path}")
        if not canonical_lines:
            return ""
        canonical_lines.sort()
        hasher = hashlib.sha256()
        for item in canonical_lines:
            hasher.update(item.encode("utf-8"))
            hasher.update(b"\n")
        return hasher.hexdigest()
    except Exception as exc:
        _ = exc
        return ""

