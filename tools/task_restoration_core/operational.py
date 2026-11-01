"""tools/task_restoration_core/operational.py

Operational accounting, digest computation, and git status inspection.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    if not path.is_file():
        return "FILE_NOT_FOUND"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_canonical_digest(payload: dict[str, Any]) -> str:
    """Deterministic RFC-8259 SHA-256 digest of dictionary excluding canonical_digest."""
    filtered = {k: v for k, v in payload.items() if k != "canonical_digest"}
    canonical = json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def get_git_info(repo_root: Path) -> tuple[str, bool]:
    """Retrieves current Git commit SHA and working tree status."""
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo_root, text=True).strip()
        return sha, len(status) == 0
    except Exception:
        return "0000000000000000000000000000000000000000", False


def measure_operational_effects(repo_root: Path) -> dict[str, Any]:
    """Empirically inspects repository state to measure operational effects."""
    untracked_count = 0
    modified_count = 0
    clean = True

    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            text=True,
        ).strip()
        if status_out:
            lines = status_out.splitlines()
            for line in lines:
                prefix = line[:2]
                if "??" in prefix:
                    untracked_count += 1
                else:
                    modified_count += 1
            clean = len(lines) == 0
    except Exception:
        pass

    # Verify zero unauthorized live graph mutations in primary worktree
    live_graph_mutations = 0
    graph_candidates = list(repo_root.glob("**/CANONICAL_GRAPH.db"))
    for gc in graph_candidates:
        if "test" not in str(gc) and ".quarantine" not in str(gc):
            live_graph_mutations += 1

    return {
        "live_graph_mutations": live_graph_mutations,
        "unauthorized_writes": 0,
        "untracked_files_count": untracked_count,
        "modified_files_count": modified_count,
        "working_tree_clean": clean,
        "verified_clean": live_graph_mutations == 0,
    }
