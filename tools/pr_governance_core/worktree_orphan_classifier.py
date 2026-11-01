"""
tools/pr_governance_core/worktree_orphan_classifier.py

Classification and uniqueness detection for orphaned worktree directories.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 digest of a file in 64KB chunks."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _is_safe_path(p: Path, repo_root: Path) -> bool:
    """Verifies that a path or symlink does not resolve outside repo root."""
    try:
        resolved = p.resolve()
        return repo_root.resolve() in resolved.parents or resolved == repo_root.resolve()
    except Exception:
        return False


def _is_deliverable_file(suffix: str, filename: str, rel_str: str) -> bool:
    """Determines whether a file qualifies as an uncommitted meeting deliverable."""
    if suffix in (".md", ".vtt", ".png", ".svg"):
        return True
    if suffix == ".jsonl" and not filename.startswith("session_journal"):
        return True
    if suffix == ".json" and "output" in rel_str:
        if filename.startswith("session_state") or filename.startswith("session_journal"):
            return False
        return not (filename.endswith("manifest.json") or "metadata" in filename)
    return False


def _check_unique_file(
    file_path: Path, orphan_dir: Path, repo_root: Path
) -> Optional[Tuple[Dict[str, Any], str]]:
    """Checks if a file in an orphan directory is unique from the main repo."""
    try:
        rel_path = file_path.relative_to(orphan_dir)
    except ValueError:
        return None

    main_target = repo_root / rel_path
    file_size = 0
    with contextlib.suppress(Exception):
        file_size = file_path.stat().st_size

    is_unique = False
    if not main_target.exists():
        is_unique = True
    else:
        try:
            if main_target.stat().st_size != file_size:
                is_unique = True
            else:
                h_orphan = compute_file_sha256(file_path)
                h_main = compute_file_sha256(main_target)
                if h_orphan and h_main and h_orphan != h_main:
                    is_unique = True
        except Exception:
            is_unique = True

    if not is_unique:
        return None

    rel_str = str(rel_path)
    file_info = {
        "path": rel_str,
        "size": file_size,
        "sha256": compute_file_sha256(file_path),
    }

    suffix = file_path.suffix.lower()
    cat = "other"
    if _is_deliverable_file(suffix, file_path.name, rel_str):
        cat = "deliverable"
    elif suffix == ".wav":
        cat = "audio"
    elif "reports" in rel_str or "readiness" in rel_str:
        cat = "receipt"

    return file_info, cat


def _process_orphan_entry(
    file_path: Path, orphan_dir: Path, repo_root: Path
) -> Optional[Tuple[Dict[str, Any], str]]:
    if not _is_safe_path(file_path, repo_root) or file_path.is_symlink():
        return None
    return _check_unique_file(file_path, orphan_dir, repo_root)


def _scan_orphan_files(
    orphan_dir: Path, repo_root: Path
) -> Tuple[List[Dict[str, Any]], bool, bool, bool]:
    unique_files: List[Dict[str, Any]] = []
    has_deliv = False
    has_audio = False
    has_receipts = False

    for root, dirs, files in os.walk(orphan_dir):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".hypothesis")]

        for f in files:
            res = _process_orphan_entry(Path(root) / f, orphan_dir, repo_root)
            if not res:
                continue

            file_info, cat = res
            unique_files.append(file_info)
            if cat == "deliverable":
                has_deliv = True
            elif cat == "audio":
                has_audio = True
            elif cat == "receipt":
                has_receipts = True

    return unique_files, has_deliv, has_audio, has_receipts


def _determine_orphan_status(
    has_deliverables: bool, has_audio: bool, has_receipts: bool
) -> str:
    if has_deliverables:
        return "ORPHAN_DELIVERABLES"
    if has_audio:
        return "ORPHAN_AUDIO_ONLY"
    if has_receipts:
        return "ORPHAN_RECEIPTS_ONLY"
    return "ORPHAN_STUB"


def classify_orphan_directory(
    orphan_dir: Path, repo_root: Path
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Inspects an orphaned directory under .worktrees/ and classifies it.
    Returns (classification, list_of_unique_files).
    """
    if not orphan_dir.is_dir():
        return "NON_DIRECTORY", []

    unique_files, has_deliv, has_audio, has_receipts = _scan_orphan_files(
        orphan_dir, repo_root
    )
    status = _determine_orphan_status(has_deliv, has_audio, has_receipts)
    return status, unique_files
