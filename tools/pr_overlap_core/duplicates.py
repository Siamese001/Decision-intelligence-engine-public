"""
Detection of duplicate commits, duplicate patch-ids, and identical blob objects.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .models import CommitPatchEvidence, FileHunkDiff


def compute_commit_patch_id(repo_root: Path, commit_sha: str) -> Optional[str]:
    """
    Computes git patch-id for a given commit.
    Uses `git diff-tree -p <commit> | git patch-id --stable`.
    Returns the stable 40-character patch-id digest, or None if empty/merge.
    """
    try:
        diff_proc = subprocess.run(
            ["git", "diff-tree", "-p", commit_sha],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_proc.returncode != 0 or not diff_proc.stdout.strip():
            return None

        pid_proc = subprocess.run(
            ["git", "patch-id", "--stable"],
            input=diff_proc.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        if pid_proc.returncode == 0 and pid_proc.stdout.strip():
            # Format: <patch-id> <commit-sha>
            parts = pid_proc.stdout.strip().split()
            if parts:
                return parts[0]
    except Exception:
        pass
    return None


def compute_blob_sha(repo_root: Path, ref: str, path: str) -> Optional[str]:
    """
    Resolves git blob SHA for a specific file at a given ref or commit.
    Uses `git rev-parse <ref>:<path>`.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", f"{ref}:{path}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def detect_duplicate_commits(
    commits_a: List[CommitPatchEvidence],
    commits_b: List[CommitPatchEvidence],
) -> List[Dict[str, str]]:
    """
    Detects commits across two branches that share identical patch-ids.
    Indicates duplicate cherry-picks or independently applied identical patches.
    """
    duplicates: List[Dict[str, str]] = []
    pid_map_a: Dict[str, CommitPatchEvidence] = {
        c.patch_id: c for c in commits_a if c.patch_id
    }

    for cb in commits_b:
        if not cb.patch_id:
            continue
        if cb.patch_id in pid_map_a:
            ca = pid_map_a[cb.patch_id]
            duplicates.append({
                "commit_a": ca.commit_sha,
                "commit_b": cb.commit_sha,
                "patch_id": cb.patch_id,
                "subject_a": ca.subject,
                "subject_b": cb.subject,
            })

    return duplicates


def detect_duplicate_blobs(
    files_a: Dict[str, FileHunkDiff],
    files_b: Dict[str, FileHunkDiff],
) -> List[Dict[str, str]]:
    """
    Detects files present in both branches that resulted in the exact same blob SHA.
    Indicates identical new files or identical final versions of files.
    """
    duplicates: List[Dict[str, str]] = []
    shared_paths = set(files_a.keys()) & set(files_b.keys())

    for path in sorted(shared_paths):
        fa = files_a[path]
        fb = files_b[path]
        if fa.status == "D" or fb.status == "D":
            continue
        if fa.blob_sha and fb.blob_sha and fa.blob_sha == fb.blob_sha:
            duplicates.append({
                "path": path,
                "blob_sha": fa.blob_sha,
                "status_a": fa.status,
                "status_b": fb.status,
            })

    return duplicates


def check_identical_file_changes(
    files_a: Dict[str, FileHunkDiff],
    files_b: Dict[str, FileHunkDiff],
) -> bool:
    """
    Returns True if both branches modify the exact same files with the exact same
    normalized hunk contents and statuses.
    """
    if set(files_a.keys()) != set(files_b.keys()):
        return False
    if not files_a:
        return False

    for path, fa in files_a.items():
        fb = files_b[path]
        if fa.status != fb.status:
            return False
        if fa.hunk_hashes != fb.hunk_hashes:
            return False

    return True
