"""Inventory caching and active-pair resolution helpers for runtime evidence.

Extracted to preserve modularity, file line budgets, and strict cognitive complexity limits.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


def resolve_pair_and_root(path: Path) -> tuple[dict[str, Any], Path]:
    """Load active_pair.json data and determine the authoritative runtime root."""
    resolved_path = path.resolve()
    runtime_root = resolved_path.parent
    with open(resolved_path, "r", encoding="utf-8") as f:
        pair = json.load(f)

    if pair.get("$schema") == "runtime-pointer:1.0.0" or pair.get("pointer"):
        target_raw = pair.get("target") or pair.get("pointer")
        if target_raw:
            target_path = Path(target_raw)
            if not target_path.is_absolute():
                target_path = (resolved_path.parent / target_path).resolve()
            if target_path.is_file():
                with open(target_path, "r", encoding="utf-8") as tf:
                    pair = json.load(tf)
                runtime_root = target_path.parent.resolve()
    elif pair.get("runtime_root"):
        target_root = Path(pair["runtime_root"])
        if not target_root.is_absolute():
            target_root = (resolved_path.parent / target_root).resolve()
        runtime_root = target_root.resolve()

    return pair, runtime_root


def compute_inventory_cache_key(roots: Iterable[Path]) -> str:
    """Compute deterministic SHA-256 cache key based on active_pair timestamps and sizes."""
    parts: list[str] = []
    for r in sorted(roots, key=lambda v: str(v).casefold()):
        p = r / "active_pair.json"
        if p.is_file():
            try:
                stat = p.stat()
                parts.append(f"{p}:{stat.st_mtime_ns}:{stat.st_size}")
            except OSError:
                pass
    return hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()


def read_cached_inventory(cache_file: Path, cache_key: str) -> dict[str, Any] | None:
    """Read cached inventory if cache key matches."""
    if not cache_file.is_file():
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cached_data = json.load(f)
        if cached_data.get("cache_key") == cache_key and "inventory" in cached_data:
            return cached_data["inventory"]
    except Exception:
        pass
    return None


def write_cached_inventory(cache_file: Path, cache_key: str, inventory: dict[str, Any]) -> None:
    """Persist inventory to disk cache atomically."""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_key": cache_key,
            "inventory": inventory,
        }
        tmp = cache_file.with_name(f".{cache_file.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, cache_file)
    except Exception:
        pass
