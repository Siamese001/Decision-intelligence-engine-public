"""
dual_pipeline/naming.py

SSOT Meeting Slug and Container Naming Unification.
Ensures identical slug derivation between 'record-meeting' and 'ingest-meeting'
and provides container directory detection to prevent duplicate meeting collisions.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from dual_pipeline.canonical_pipeline.worktree_naming import slugify_branch_name

slugify_branch = slugify_branch_name
from dual_pipeline.meeting_paths import (
    extract_slug_from_meeting_filename,
    normalize_time_str,
    sanitize_filename_slug,
    standardize_meeting_slug as _base_standardize_meeting_slug,
)

logger = logging.getLogger("dual_pipeline.naming")

# Pattern matching date directory names e.g. 10-14-2026 or 2026-10-14
_DATE_DIR_RE = re.compile(r"^(\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2})$")


def is_live_meeting_container(dir_path: Union[str, Path]) -> bool:
    """Checks whether a directory is an authentic live meeting recording container."""
    p = Path(dir_path).resolve()
    if not p.is_dir():
        return False

    # Explicit rejection of cache/chunk subdirectories, input repositories, and date directories
    if p.name in (".cache", "chunks", ".chunks", "transcript_cache", "inputs", "transcripts") or _DATE_DIR_RE.match(p.name):
        return False

    markers = (
        "record_start_receipt.json",
        "speaker_hints.json",
        "speaker_hints_queue.json",
        "session_manifest.json",
    )
    if any((p / m).is_file() for m in markers):
        return True

    # Check for glob patterns
    if any(p.glob("*_manifest.json")) or any(p.glob("*_live_transcript.md")) or any(p.glob("00_*_master_*.wav")):
        return True

    # Check path pattern: .../output/<project>/<date>/<slug>
    parts = p.parts
    if "output" in parts:
        idx = parts.index("output")
        if len(parts) >= idx + 4 and _DATE_DIR_RE.match(parts[idx + 2]):
            if parts[idx + 3] not in (".cache", "chunks", ".chunks", "transcript_cache") and not _DATE_DIR_RE.match(parts[idx + 3]):
                return True

    return False


def _extract_cache_slug(p: Path) -> Optional[str]:
    slug = extract_slug_from_meeting_filename(p.name)
    if slug or not p.is_file():
        return slug or (p.name if p.is_dir() and not _DATE_DIR_RE.match(p.name) else None)
    stem = re.sub(r"^(?:00|01)_\d{2}-\d{2}-\d{4}_", "", p.stem)
    stem = re.sub(r"(_master.*|_segments|_live_transcript)$", "", stem)
    return stem or None


def _find_date_subcontainer(date_path: Path, slug: Optional[str]) -> Optional[Path]:
    if not date_path.is_dir():
        return None
    for sub in date_path.iterdir():
        if sub.is_dir() and is_live_meeting_container(sub):
            if not slug or (slug in sub.name or sub.name in slug):
                return sub
    return None


def _find_by_glob(root: Path, slug: Optional[str]) -> Optional[Path]:
    if not slug or not root.is_dir():
        return None
    for cand in root.glob(f"*/*/{slug}"):
        if cand.is_dir() and is_live_meeting_container(cand):
            return cand
    return None


def _resolve_from_transcript_cache(p: Path) -> Optional[Path]:
    if "transcript_cache" not in p.parts:
        return None
    slug = _extract_cache_slug(p)
    tc_idx = p.parts.index("transcript_cache")
    root = Path(*p.parts[:tc_idx])
    if root.name != "output" and (root / "output").is_dir():
        root = root / "output"
    rel = p.parts[tc_idx + 1:]
    if len(rel) >= 2:
        cand = root / rel[0] / rel[1] / (slug or "")
        if slug and cand.is_dir() and is_live_meeting_container(cand):
            return cand
        found = _find_date_subcontainer(root / rel[0] / rel[1], slug)
        if found:
            return found
    return _find_by_glob(root, slug)


def resolve_meeting_container_dir(input_path: Union[str, Path, None]) -> Optional[Path]:
    """Resolves the live meeting container directory from an input file or folder path."""
    if not input_path:
        return None
    try:
        p = Path(input_path).expanduser().resolve()
    except Exception:
        return None

    if p.is_dir():
        if p.name in (".cache", "chunks", ".chunks", "transcript_cache") and is_live_meeting_container(p.parent):
            return p.parent
        if is_live_meeting_container(p):
            return p
    elif p.is_file():
        if is_live_meeting_container(p.parent):
            return p.parent
        if p.parent.name in (".cache", "chunks", ".chunks", "transcript_cache") and is_live_meeting_container(p.parent.parent):
            return p.parent.parent

    return _resolve_from_transcript_cache(p)


def resolve_meeting_container_slug(
    input_path: Union[str, Path, None],
    fallback_name: Optional[str] = None,
    attendees: Optional[Any] = None,
    project_name: Optional[str] = None,
) -> Optional[str]:
    """
    Resolves the canonical slug for a meeting container.
    If input points inside an existing recording container, adopts its folder name.
    """
    container_dir = resolve_meeting_container_dir(input_path)
    if container_dir is not None:
        cand_slug = container_dir.name
        if cand_slug not in (".cache", "chunks", ".chunks", "transcript_cache") and not _DATE_DIR_RE.match(cand_slug):
            logger.info(
                "resolve_meeting_container_slug: Reusing container slug '%s' from %s",
                cand_slug,
                container_dir,
            )
            return cand_slug

    if fallback_name:
        return standardize_meeting_slug(
            fallback_name,
            attendees=attendees,
            project_name=project_name,
        )
    return None


def standardize_meeting_slug(
    name: str,
    attendees: Optional[Union[str, Sequence[Any], dict[str, Any]]] = None,
    max_len: int = 40,
    project_name: Optional[str] = None,
    initial_slug: Optional[str] = None,
) -> str:
    """
    Standardizes meeting names across all pipeline stages (record, ingest, and query).
    Delegates to SSOT in dual_pipeline.meeting_paths with guaranteed length ceiling.
    """
    return _base_standardize_meeting_slug(
        name=name,
        attendees=attendees,
        max_len=max_len,
        project_name=project_name,
        initial_slug=initial_slug,
    )


def resolve_container_or_slug(
    input_cand: Union[str, Path, None],
    meeting_name: str,
    attendees: Optional[Any] = None,
    project_name: Optional[str] = None,
) -> tuple[Optional[Path], str]:
    """Resolves container dir and standardized slug."""
    container_dir = resolve_meeting_container_dir(input_cand)
    if container_dir is not None:
        return container_dir, container_dir.name
    slug = standardize_meeting_slug(
        meeting_name,
        attendees=attendees,
        max_len=40,
        project_name=project_name,
    )
    return None, slug


__all__ = [
    "is_live_meeting_container",
    "resolve_meeting_container_dir",
    "resolve_meeting_container_slug",
    "resolve_container_or_slug",
    "standardize_meeting_slug",
    "sanitize_filename_slug",
    "normalize_time_str",
    "extract_slug_from_meeting_filename",
    "slugify_branch",
]

