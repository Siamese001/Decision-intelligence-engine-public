"""Primitive filesystem, hashing, and JSON loading helpers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .constants import BASE_PRUNE_NAMES, REFERENCE_KEYS, RUNTIME_PRUNE_NAMES


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_browser_profile_directory(name: str) -> bool:
    lowered = name.casefold()
    return lowered.startswith(("edge-", "chrome-")) and "profile" in lowered


def _walk_for_active_pairs(root: Path) -> list[Path]:
    root = _resolved(root)
    if not root.is_dir():
        return []
    discovered: list[Path] = []
    for directory, child_names, file_names in os.walk(root, followlinks=False):
        child_names[:] = [
            name
            for name in child_names
            if name not in BASE_PRUNE_NAMES and not _is_browser_profile_directory(name)
        ]
        directory_path = Path(directory)
        if "active_pair.json" in file_names:
            discovered.append(_resolved(directory_path / "active_pair.json"))
            child_names[:] = [
                name for name in child_names if name not in RUNTIME_PRUNE_NAMES
            ]
    return sorted(set(discovered), key=lambda value: str(value).casefold())


def _iter_reference_values(value: Any) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in REFERENCE_KEYS and isinstance(item, str) and item.strip():
                yield key, item.strip()
            yield from _iter_reference_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_reference_values(item)


def _resolve_reference(raw: str, repository_root: Path) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = repository_root / candidate
    return _resolved(candidate)


def _path_is_within(path: str | Path, root: Path) -> bool:
    candidate = _resolved(Path(path))
    root = _resolved(root)
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True

