"""Authoritative singleton runtime root and active-pair resolution.

Enforces:
1. Exactly one authoritative runtime root across main repo and worktrees.
2. active_pair.json resolution via config and read-only pointers.
3. Clean worktree traversal without duplicate or missing generation issues.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from dual_pipeline.credentials import CredentialManager


class ActiveGraphMissingError(FileNotFoundError):
    """Raised when the authoritative active graph file is missing or unresolvable."""
    pass


class ActiveGraphIntegrityMismatchError(ValueError):
    """Raised when the active graph SHA-256 does not match recorded graph_sha256."""
    pass


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    if not path.is_file():
        raise ActiveGraphMissingError(f"ACTIVE_GRAPH_MISSING: File not found for hash calculation: {path}")
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_worktree_gitdir(git_file: Path) -> Path | None:
    """Extract main repository root from a worktree .git file if applicable."""
    try:
        content = git_file.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir:"):
            return None
        gitdir_raw = content.split(":", 1)[1].strip()
        gitdir = Path(gitdir_raw)
        if not gitdir.is_absolute():
            gitdir = (git_file.parent / gitdir).resolve()
        if "worktrees" in gitdir.parts:
            return gitdir.parents[1].parent.resolve()
    except Exception:
        return None
    return None


def resolve_main_repository_root(start_path: Path | str | None = None) -> Path:
    """Find the main repository root, resolving git worktrees to their common root."""
    candidate = Path(start_path or Path.cwd()).resolve()
    for parent in [candidate, *candidate.parents]:
        git_entry = parent / ".git"
        if git_entry.is_dir():
            return parent
        if git_entry.is_file():
            main_repo = _resolve_worktree_gitdir(git_entry)
            return main_repo if main_repo is not None else parent
    for parent in [candidate, *candidate.parents]:
        if parent.name == ".worktrees":
            return parent.parent
    return candidate


def resolve_authoritative_runtime_root(repo_root: Path | str | None = None) -> Path:
    """Resolve the single authoritative runtime root for the repository."""
    # 1. Environment variable override
    env_root = CredentialManager.get_credential("NEE_RUNTIME_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()

    main_repo = resolve_main_repository_root(repo_root)

    # 2. Check config/runtime/parameters.json if available
    config_file = main_repo / "config" / "runtime" / "parameters.json"
    if config_file.is_file():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            custom_root = (
                data.get("parameters", {}).get("runtime", {}).get("authoritative_runtime_root")
            )
            if custom_root:
                p = Path(custom_root)
                if not p.is_absolute():
                    p = (main_repo / p).resolve()
                return p
        except Exception:
            pass

    # 3. Default to .local/dual_pipeline under main repository
    return (main_repo / ".local" / "dual_pipeline").resolve()


def _read_pair_json(pair_path: Path) -> dict[str, Any]:
    if not pair_path.is_file():
        raise ActiveGraphMissingError(f"ACTIVE_GRAPH_MISSING: Authoritative active_pair.json not found at {pair_path}")
    try:
        with open(pair_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise ActiveGraphMissingError(f"ACTIVE_GRAPH_MISSING: Malformed active_pair.json at {pair_path}: {exc}") from exc


def _resolve_pointer(data: dict[str, Any], r_root: Path, pair_path: Path) -> tuple[dict[str, Any], Path]:
    if not (data.get("$schema") == "runtime-pointer:1.0.0" or data.get("pointer")):
        return data, pair_path
    target_raw = data.get("target") or data.get("pointer")
    target_path = Path(target_raw)
    if not target_path.is_absolute():
        cand = (pair_path.parent / target_path).resolve()
        if cand.is_file():
            target_path = cand
        else:
            main_repo = resolve_main_repository_root(r_root)
            target_path = (main_repo / target_path).resolve()
    else:
        target_path = target_path.resolve()
    target_data = _read_pair_json(target_path)
    if target_data.get("$schema") == "runtime-pointer:1.0.0" or target_data.get("pointer"):
        raise ValueError(f"ACTIVE_GRAPH_MISSING: Multi-hop pointer chains are forbidden: {target_path}")
    return target_data, target_path


def _validate_pair_graph(data: dict[str, Any], actual_pair_path: Path, enforce_integrity: bool) -> None:
    raw_graph = data.get("graph_path")
    if not raw_graph:
        raise ActiveGraphMissingError(f"ACTIVE_GRAPH_MISSING: active_pair.json missing graph_path at {actual_pair_path}")
    graph_file = Path(raw_graph)
    if not graph_file.is_absolute():
        graph_file = (actual_pair_path.parent / graph_file).resolve()
    if not graph_file.is_file():
        raise ActiveGraphMissingError(f"ACTIVE_GRAPH_MISSING: Resolved graph database file not found at {graph_file}")
    if not enforce_integrity:
        return
    recorded_sha256 = data.get("graph_sha256")
    if not recorded_sha256:
        raise ActiveGraphMissingError(f"ACTIVE_GRAPH_MISSING: active_pair.json missing graph_sha256 at {actual_pair_path}")
    actual_sha = _compute_sha256(graph_file)
    if actual_sha != recorded_sha256:
        raise ActiveGraphIntegrityMismatchError(
            f"ACTIVE_GRAPH_MISSING: Graph integrity mismatch: actual {actual_sha} != recorded {recorded_sha256} at {graph_file}"
        )


def resolve_active_pair(
    runtime_root: Path | str | None = None,
    *,
    enforce_integrity: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Load the authoritative active_pair.json, following read-only pointers if present.

    Fails closed with ActiveGraphMissingError if active_pair.json or the referenced graph
    is missing, or ActiveGraphIntegrityMismatchError if the graph hash differs.
    """
    r_root = resolve_authoritative_runtime_root() if runtime_root is None else Path(runtime_root).resolve()
    pair_path = r_root / "active_pair.json"
    data = _read_pair_json(pair_path)
    data, actual_pair_path = _resolve_pointer(data, r_root, pair_path)
    _validate_pair_graph(data, actual_pair_path, enforce_integrity)
    return data, actual_pair_path


def resolve_active_graph_path(
    runtime_root: Path | str | None = None,
    *,
    enforce_integrity: bool = True,
) -> Path:
    """Resolve and verify the authoritative active graph SQLite file."""
    data, actual_pair_path = resolve_active_pair(runtime_root, enforce_integrity=enforce_integrity)
    graph_file = Path(data["graph_path"])
    if not graph_file.is_absolute():
        graph_file = (actual_pair_path.parent / graph_file).resolve()
    return graph_file
