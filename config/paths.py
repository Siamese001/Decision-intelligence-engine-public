#!/usr/bin/env python3
"""
config/paths.py

Canonical Layer-0 path registry for repository roots, SQLite ledgers,
active release pair manifests, voiceprint stores, and speaker memory.
Centralizes worktree-aware primary repository resolution and environment
overrides without raw os.environ access in downstream modules.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from config.credentials import CredentialManager

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
_PYTEST_ISOLATED_PATHS: Dict[str, Path] = {}


def get_repo_root() -> Path:
    """Return the root directory of the current checkout or worktree."""
    return REPO_ROOT


def _resolve_gitdir_pointer(dot_git: Path) -> Optional[Path]:
    """Parse a `.git` worktree pointer file and return the primary repository root if valid."""
    try:
        raw = dot_git.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw.startswith("gitdir:"):
            return None
        gd = Path(raw.split("gitdir:", 1)[1].strip())
        resolved_gd = gd.resolve() if gd.is_absolute() else (dot_git.parent / gd).resolve()
        if resolved_gd.parent.name == "worktrees" and resolved_gd.parent.parent.name == ".git":
            return resolved_gd.parent.parent.parent.resolve()
    except Exception:
        pass
    return None


def _walk_parent_git_root(base_dir: Path) -> Optional[Path]:
    """Walk upward from `base_dir` to locate a primary `.git` directory or `.worktrees` parent."""
    cur = base_dir
    while cur != cur.parent:
        dot_git = cur / ".git"
        if dot_git.is_dir():
            return cur
        if dot_git.is_file():
            ext_root = _resolve_gitdir_pointer(dot_git)
            if ext_root is not None:
                return ext_root
        if cur.parent.name == ".worktrees" and (cur.parent.parent / ".git").is_dir():
            return cur.parent.parent.resolve()
        cur = cur.parent
    return None


def _resolve_git_common_dir(base_dir: Path) -> Optional[Path]:
    """Run `git rev-parse --git-common-dir` to resolve the primary repository root."""
    try:
        res = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=str(base_dir), capture_output=True, text=True, timeout=2.0, check=False)
        if res.returncode != 0 or not res.stdout.strip():
            return None
        common = Path(res.stdout.strip())
        c_dir = common.resolve() if common.is_absolute() else (base_dir / common).resolve()
        if c_dir.name == ".git":
            return c_dir.parent.resolve()
        if c_dir.parent.name == "worktrees" and c_dir.parent.parent.name == ".git":
            return c_dir.parent.parent.parent.resolve()
    except Exception:
        pass
    return None


def get_primary_repo_root(anchor: Optional[Path] = None) -> Path:
    """
    Resolve the primary repository root even when running inside a git worktree.
    Checks `.git` hierarchy, `.worktrees` path segments, then `git rev-parse --git-common-dir`.
    """
    base = (anchor or REPO_ROOT).resolve()
    base_dir = base if base.is_dir() else base.parent
    walked = _walk_parent_git_root(base_dir)
    if walked is not None:
        return walked
    if ".worktrees" in base_dir.parts:
        return Path(*base_dir.parts[: base_dir.parts.index(".worktrees")]).resolve()
    return _resolve_git_common_dir(base_dir) or REPO_ROOT


def _get_pytest_temp_path(key: str, suffix: str) -> Path:
    if key not in _PYTEST_ISOLATED_PATHS:
        fd_path = Path(tempfile.mkdtemp(prefix=f"nee_pytest_{key}_")) / f"{key}{suffix}"
        _PYTEST_ISOLATED_PATHS[key] = fd_path
    return _PYTEST_ISOLATED_PATHS[key]


def get_active_pair_path(repo_root: Optional[Path] = None) -> Path:
    """Resolve `graph/releases/active_pair.json` honoring `NEE_ACTIVE_PAIR_PATH` and primary repo fallback."""
    env_override = CredentialManager.get_credential("NEE_ACTIVE_PAIR_PATH")
    if env_override:
        return Path(env_override).expanduser().resolve()
    root = (repo_root or REPO_ROOT).resolve()
    local_pair = root / "graph" / "releases" / "active_pair.json"
    if local_pair.exists():
        return local_pair
    primary_pair = get_primary_repo_root(root) / "graph" / "releases" / "active_pair.json"
    if primary_pair.exists():
        return primary_pair
    return local_pair


def get_graph_sqlite_path(repo_root: Optional[Path] = None) -> Path:
    """Resolve canonical SQLite graph path via `NEE_GRAPH_DB_PATH`, `active_pair.json`, or `graph/nee_graph.sqlite`."""
    env_override = (
        CredentialManager.get_credential("NEE_GRAPH_DB_PATH")
        or CredentialManager.get_credential("GRAPH_DB_PATH")
    )
    if env_override:
        return Path(env_override).expanduser().resolve()
    root = (repo_root or REPO_ROOT).resolve()
    active_pair = get_active_pair_path(root)
    if active_pair.is_file():
        try:
            data = json.loads(active_pair.read_text(encoding="utf-8"))
            rel_sqlite = data.get("active_graph_sqlite") or data.get("graph_sqlite_path")
            if rel_sqlite:
                cand = (active_pair.parent.parent.parent / str(rel_sqlite)).resolve()
                if cand.exists():
                    return cand
        except Exception:
            pass
    local_db = root / "graph" / "nee_graph.sqlite"
    if local_db.exists():
        return local_db
    primary_db = get_primary_repo_root(root) / "graph" / "nee_graph.sqlite"
    if primary_db.exists():
        return primary_db
    return local_db


def get_learning_sqlite_path(runtime_root: Optional[Path] = None) -> Path:
    """Resolve `learning.sqlite` path with pytest isolation and environment override support."""
    if runtime_root is not None:
        return Path(runtime_root).resolve() / ".local" / "ledger" / "learning.sqlite"
    for env_key in ("NEE_TEST_LEARNING_SQLITE", "NEE_HEALING_SQLITE_PATH", "NEE_LEARNING_SQLITE_PATH"):
        val = CredentialManager.get_credential(env_key)
        if val:
            return Path(val).expanduser().resolve()
    if "pytest" in sys.modules or CredentialManager.get_credential("PYTEST_CURRENT_TEST"):
        return _get_pytest_temp_path("learning", ".sqlite")
    rt_env = CredentialManager.get_credential("NEE_RUNTIME_ROOT")
    base = Path(rt_env).expanduser().resolve() if rt_env else REPO_ROOT
    return base / ".local" / "ledger" / "learning.sqlite"


def get_hitl_ledger_paths(repo_root: Optional[Path] = None) -> Tuple[Path, Path]:
    """Return `(sqlite_path, json_path)` for the HITL decision ledger."""
    sql_override = (
        CredentialManager.get_credential("NEE_TEST_HITL_SQLITE")
        or CredentialManager.get_credential("NEE_HITL_LEDGER_SQLITE_PATH")
    )
    json_override = (
        CredentialManager.get_credential("NEE_TEST_HITL_JSON")
        or CredentialManager.get_credential("NEE_HITL_LEDGER_JSON_PATH")
    )
    if sql_override or json_override:
        sp = Path(sql_override).resolve() if sql_override else _get_pytest_temp_path("hitl_ledger", ".sqlite")
        jp = Path(json_override).resolve() if json_override else sp.with_suffix(".json")
        return sp, jp
    if repo_root is None and ("pytest" in sys.modules or CredentialManager.get_credential("PYTEST_CURRENT_TEST")):
        sp = _get_pytest_temp_path("hitl_ledger", ".sqlite")
        return sp, sp.with_suffix(".json")
    root = (repo_root or get_primary_repo_root()).resolve()
    ledger_dir = root / "graph" / "ledger"
    return ledger_dir / "hitl_decision_ledger.sqlite", ledger_dir / "hitl_decision_ledger.json"


def get_voiceprint_sqlite_path(base_file: Optional[Path] = None) -> Path:
    """Resolve `voiceprint_memory.sqlite` with worktree/pytest isolation."""
    env_override = CredentialManager.get_credential("NEE_VOICEPRINT_DB_PATH")
    if env_override:
        return Path(env_override).expanduser().resolve()
    if base_file is None and ("pytest" in sys.modules or CredentialManager.get_credential("PYTEST_CURRENT_TEST")):
        return Path(tempfile.gettempdir()) / "nee_pytest_voiceprint_memory.sqlite"
    anchor = Path(base_file).resolve() if base_file is not None else REPO_ROOT
    return get_primary_repo_root(anchor) / ".local" / "voiceprint_memory.sqlite"


def get_speaker_memory_path(base_file: Optional[Path] = None) -> Path:
    """Resolve `config/runtime/speaker_memory.json` across worktrees via primary repo root."""
    env_override = CredentialManager.get_credential("NEE_SPEAKER_MEMORY_PATH")
    if env_override and env_override.strip():
        return Path(env_override.strip()).expanduser().resolve()
    primary_root = get_primary_repo_root(base_file)
    primary_candidate = primary_root / "config" / "runtime" / "speaker_memory.json"
    if primary_candidate.exists() or primary_candidate.parent.exists():
        return primary_candidate
    return REPO_ROOT / "config" / "runtime" / "speaker_memory.json"


__all__ = [
    "REPO_ROOT",
    "get_active_pair_path",
    "get_graph_sqlite_path",
    "get_hitl_ledger_paths",
    "get_learning_sqlite_path",
    "get_primary_repo_root",
    "get_repo_root",
    "get_speaker_memory_path",
    "get_voiceprint_sqlite_path",
]
