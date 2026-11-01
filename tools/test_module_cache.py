#!/usr/bin/env python3
"""tools/test_module_cache.py

Content-addressable per-module test result caching for Gate 28 and test execution.

Caches successful test module runs keyed by:
  (module_name, test_file_ast_hash, transitive_local_imports_hash, env_digest)

Ensures that incremental bugfix commits re-run ONLY test modules whose code or
transitive local imports have mutated, eliminating redundant full-suite re-runs.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("tools.test_module_cache")


def get_module_cache_dir(repo_root: Path) -> Path:
    """Returns local cache directory under .local/test_module_cache."""
    cache_dir = repo_root.resolve() / ".local" / "test_module_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


_FILE_HASH_CACHE: Dict[Tuple[str, float, int], str] = {}
_LOCAL_DEPS_CACHE: Dict[Tuple[str, float], List[Path]] = {}


def clear_ram_cache() -> None:
    """Clears in-memory RAM cache for file hashes and dependency graphs."""
    _FILE_HASH_CACHE.clear()
    _LOCAL_DEPS_CACHE.clear()


def _hash_file_safe(file_path: Path) -> str:
    """Computes SHA-256 of file contents with RAM memoization on mtime and size."""
    p = file_path.resolve()
    if not p.is_file():
        return ""
    try:
        st = p.stat()
        cache_key = (str(p), st.st_mtime, st.st_size)
        cached = _FILE_HASH_CACHE.get(cache_key)
        if cached is not None:
            return cached
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        digest = h.hexdigest()
        _FILE_HASH_CACHE[cache_key] = digest
        return digest
    except Exception:
        return ""


def _compute_test_module_env_digest() -> str:
    """Computes deterministic environment digest for test modules."""
    import platform
    import re

    h = hashlib.sha256()
    resolved_exe = str(Path(sys.executable).resolve())
    norm_exe = re.sub(r"/\.worktrees/[^/]+/\.venv/", "/.venv/", resolved_exe)
    tokens = [
        sys.version,
        sys.implementation.cache_tag or "",
        platform.system(),
        platform.release(),
        platform.machine(),
        norm_exe,
    ]
    for var in ("CI", "MOCK_ENV", "PYTHONHASHSEED", "GOVERNED_PR_MERGE"):
        tokens.append(f"{var}={os.environ.get(var, '')}")
    for pkg in ("hypothesis", "pydantic", "pytest", "ruff"):
        try:
            import importlib.metadata

            tokens.append(f"{pkg}={importlib.metadata.version(pkg)}")
        except Exception:
            tokens.append(f"{pkg}=missing")
    for t in tokens:
        h.update(t.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def resolve_test_module_file(item: str, repo_root: Path) -> Optional[Path]:
    """Resolves a test module name or path to an existing .py file.

    Handles dotted module names ('tests.test_foo'), file paths ('tests/test_foo.py'),
    and class/method specifiers ('tests.test_foo.ClassName.test_func').
    """
    root = repo_root.resolve()
    clean = item.strip().replace("\\", "/")
    if clean.endswith(".py"):
        cand = (root / clean).resolve()
        return cand if cand.is_file() else None

    parts = clean.split("/") if "/" in clean else clean.split(".")
    # Walk from full path down to find the highest existing .py file
    for i in range(len(parts), 0, -1):
        rel_parts = parts[:i]
        cand = root.joinpath(*rel_parts).with_suffix(".py").resolve()
        if cand.is_file():
            return cand
        cand_pkg = root.joinpath(*rel_parts, "__init__.py").resolve()
        if cand_pkg.is_file():
            return cand_pkg
    return None


def compute_module_fingerprint(
    item: str,
    repo_root: Path,
    env_digest: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Computes deterministic fingerprint and lists transitive local dependencies.

    Returns (fingerprint, list_of_relative_dependency_paths).
    If the module file cannot be resolved, returns ("", []).
    """
    root = repo_root.resolve()
    mod_file = resolve_test_module_file(item, root)
    if mod_file is None:
        return "", []

    file_ast_hash = _hash_file_safe(mod_file)
    deps_key = (str(mod_file), mod_file.stat().st_mtime)
    deps = _LOCAL_DEPS_CACHE.get(deps_key)
    if deps is None:
        from tools.readiness_cache_core.fingerprint import _local_dependencies

        deps = _local_dependencies(mod_file, root)
        _LOCAL_DEPS_CACHE[deps_key] = deps

    deps_hasher = hashlib.sha256()
    deps_rel: List[str] = []
    for dep in deps:
        d = dep.resolve()
        try:
            rel_str = str(d.relative_to(root))
        except ValueError:
            rel_str = str(d)
        deps_rel.append(rel_str)
        deps_hasher.update(rel_str.encode("utf-8"))
        deps_hasher.update(_hash_file_safe(d).encode("utf-8"))

    if env_digest is None:
        env_digest = _compute_test_module_env_digest()

    hasher = hashlib.sha256(b"test-module-cache-v1\x00")
    hasher.update(item.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(file_ast_hash.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(deps_hasher.hexdigest().encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(env_digest.encode("utf-8"))

    return hasher.hexdigest(), deps_rel


def get_cached_module(
    item: str,
    repo_root: Path,
    fingerprint: str,
) -> Optional[Dict[str, Any]]:
    """Retrieves cached PASS entry if fingerprint matches."""
    if not fingerprint:
        return None
    cache_file = get_module_cache_dir(repo_root) / f"{fingerprint}.json"
    if not cache_file.is_file():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            cache_file.unlink(missing_ok=True)
            return None
        if data.get("status") != "PASS" or data.get("fingerprint") != fingerprint:
            return None
        return data
    except Exception as exc:
        logger.debug(f"Corrupt module cache {cache_file}: {exc}; evicting")
        cache_file.unlink(missing_ok=True)
        return None


def prune_stale_module_cache(cache_dir: Path, max_entries: int = 2500) -> int:
    """Evicts oldest cached module entries when directory exceeds max_entries."""
    if not cache_dir.is_dir():
        return 0
    files = list(cache_dir.glob("*.json"))
    if len(files) <= max_entries:
        return 0
    files.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0.0)
    evicted = 0
    for f in files[: len(files) - max_entries]:
        with contextlib.suppress(Exception):
            f.unlink()
            evicted += 1
    return evicted


def save_cached_module(
    item: str,
    repo_root: Path,
    fingerprint: str,
    test_count: int = 1,
    duration_seconds: float = 0.0,
) -> bool:
    """Saves a passing module execution to cache."""
    if not fingerprint:
        return False
    cache_dir = get_module_cache_dir(repo_root)
    cache_file = cache_dir / f"{fingerprint}.json"
    entry = {
        "module": item,
        "status": "PASS",
        "test_count": test_count,
        "duration_seconds": round(duration_seconds, 3),
        "fingerprint": fingerprint,
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        cache_file.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.debug(f"Failed writing module cache {cache_file}: {exc}")
        return False


def clear_module_cache(repo_root: Path) -> int:
    """Clears all cached test module results."""
    cache_dir = repo_root / ".local" / "test_module_cache"
    if not cache_dir.is_dir():
        return 0
    count = 0
    for f in cache_dir.glob("*.json"):
        with contextlib.suppress(Exception):
            f.unlink()
            count += 1
    return count


def write_runner_receipt(
    receipt_path: str,
    passed: bool,
    total_modules: int,
    cached_modules_skipped: Sequence[str],
    executed_modules: Sequence[str],
    duration_seconds: float,
) -> None:
    """Writes runner execution receipt capturing module caching details."""
    p = Path(receipt_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "terminal_outcome": "PASS" if passed else "FAIL",
        "total_modules": total_modules,
        "cached_modules_skipped": list(cached_modules_skipped),
        "executed_modules": list(executed_modules),
        "cached_count": len(cached_modules_skipped),
        "executed_count": len(executed_modules),
        "duration_seconds": round(duration_seconds, 3),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

