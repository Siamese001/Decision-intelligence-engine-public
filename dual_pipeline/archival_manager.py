from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional, Union

from .core import ROOT

logger = logging.getLogger(__name__)

class ArchivalManager:
    """
    Manages safe archival of transcript artifacts.
    If a meeting is re-processed, existing artifacts are moved to an archive directory
    to prevent silent overwrites and data loss.
    """

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)

    @staticmethod
    def _find_matching_items(target_dir: Path, meeting_stem: Optional[str]) -> List[Path]:
        """Finds artifact items in target directory matching criteria."""
        if meeting_stem:
            return [
                item for item in target_dir.iterdir()
                if item.is_file() and not item.name.startswith(".") and meeting_stem in item.name
            ]

        has_artifacts = any(
            item.is_file() and (item.name.startswith("01_") or item.name.startswith("02_") or item.name.startswith("03_"))
            for item in target_dir.iterdir()
        )
        if not has_artifacts:
            return []
        return [
            item for item in target_dir.iterdir()
            if item.is_file() and not item.name.startswith(".")
        ]

    def archive_if_exists(self, project: str, date_folder: str, meeting_stem: Optional[str] = None) -> Optional[str]:
        """
        Checks if the target project/date folder has existing artifacts.
        If meeting_stem is provided, only archives artifacts matching that stem.
        If so, creates an archive folder and moves them into it.
        Returns the path to the archive folder if archival occurred, else None.
        """
        target_dir = self.root_dir / project / date_folder
        if not target_dir.exists():
            return None

        matching_items = self._find_matching_items(target_dir, meeting_stem)
        if not matching_items:
            return None

        timestamp = time.strftime("%Y%m%d%H%M%S")
        archive_dir = ROOT / ".archive" / project / date_folder / f"archive_{timestamp}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Existing artifacts found in {target_dir}. Archiving {len(matching_items)} files to {archive_dir}")

        for item in matching_items:
            shutil.move(str(item), str(archive_dir / item.name))

        logger.info(f"Successfully archived {len(matching_items)} files.")
        return str(archive_dir)

    def cleanup_cache(
        self,
        cache_dir: Optional[Union[str, Path]] = None,
        max_bytes: int = 10 * 1024 * 1024 * 1024,
        max_age_seconds: float = 7 * 86400,
        dry_run: bool = False,
        include_globs: Optional[tuple[str, ...]] = None,
        exclude_globs: Optional[tuple[str, ...]] = None,
    ) -> dict:
        target = cache_dir or (self.root_dir / "output" / "transcript_cache")
        return cleanup_transcript_cache(
            target,
            max_bytes=max_bytes,
            max_age_seconds=max_age_seconds,
            dry_run=dry_run,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )

    def sweep_temp_files(self, max_age_seconds: float = 3600.0) -> List[str]:
        return sweep_stale_temp_files(self.root_dir, max_age_seconds=max_age_seconds)


def _matches_globs(
    f: str,
    include_globs: Optional[tuple[str, ...]],
    exclude_globs: Optional[tuple[str, ...]],
) -> bool:
    if include_globs and not any(f.endswith(g.lstrip("*")) or Path(f).match(g) for g in include_globs):
        return False
    if exclude_globs and any(f.endswith(g.lstrip("*")) or Path(f).match(g) for g in exclude_globs):
        return False
    return True


def _evict_lru_files(
    all_files: List[tuple[Path, os.stat_result]],
    total_bytes: int,
    max_bytes: int,
    dry_run: bool,
) -> tuple[int, int, List[dict], int]:
    evicted_lru = 0
    reclaimed = 0
    evicted_files: List[dict] = []
    if total_bytes <= max_bytes:
        return evicted_lru, reclaimed, evicted_files, total_bytes

    all_files.sort(key=lambda item: item[1].st_mtime)
    for file_path, st in all_files:
        if total_bytes <= max_bytes:
            break
        try:
            file_size = st.st_size
            if not dry_run:
                file_path.unlink(missing_ok=True)
            total_bytes -= file_size
            reclaimed += file_size
            evicted_lru += 1
            evicted_files.append({
                "path": str(file_path),
                "name": file_path.name,
                "size_bytes": file_size,
                "mtime": st.st_mtime,
                "reason": "lru",
            })
        except (OSError, FileNotFoundError):
            continue
    return evicted_lru, reclaimed, evicted_files, total_bytes


def _process_candidate_file(
    file_path: Path,
    f: str,
    now: float,
    max_age_seconds: float,
    dry_run: bool,
) -> tuple[Optional[dict], Optional[tuple[Path, os.stat_result]]]:
    try:
        st = file_path.stat()
        if now - st.st_mtime > max_age_seconds:
            if not dry_run:
                file_path.unlink(missing_ok=True)
            evicted = {
                "path": str(file_path),
                "name": f,
                "size_bytes": st.st_size,
                "mtime": st.st_mtime,
                "reason": "ttl",
            }
            return evicted, None
        return None, (file_path, st)
    except (OSError, FileNotFoundError):
        return None, None


def cleanup_transcript_cache(
    cache_dir: Union[str, Path],
    max_bytes: int = 10 * 1024 * 1024 * 1024,
    max_age_seconds: float = 7 * 86400,
    *,
    dry_run: bool = False,
    include_globs: Optional[tuple[str, ...]] = None,
    exclude_globs: Optional[tuple[str, ...]] = None,
) -> dict:
    """
    LRU and TTL eviction for transcript cache directory.
    1. Removes files older than max_age_seconds (TTL eviction).
    2. If total size still exceeds max_bytes, evicts oldest files by mtime (LRU eviction).
    Returns summary dict of evicted files and reclaimed bytes.
    """
    p = Path(cache_dir).expanduser().resolve()
    if not p.exists():
        return {
            "evicted_ttl_count": 0,
            "evicted_lru_count": 0,
            "reclaimed_bytes": 0,
            "remaining_bytes": 0,
            "evicted_files": [],
            "dry_run": dry_run,
        }

    now = time.time()
    evicted_ttl = 0
    reclaimed_bytes = 0
    evicted_files: List[dict] = []
    all_files: List[tuple[Path, os.stat_result]] = []

    for root, _, files in os.walk(p):
        for f in files:
            if not _matches_globs(f, include_globs, exclude_globs):
                continue
            evicted, kept = _process_candidate_file(
                Path(root) / f, f, now, max_age_seconds, dry_run
            )
            if evicted:
                evicted_ttl += 1
                reclaimed_bytes += evicted["size_bytes"]
                evicted_files.append(evicted)
            elif kept:
                all_files.append(kept)

    total_bytes = sum(st.st_size for _, st in all_files)
    lru_count, lru_reclaimed, lru_files, total_bytes = _evict_lru_files(
        all_files, total_bytes, max_bytes, dry_run
    )
    reclaimed_bytes += lru_reclaimed
    evicted_files.extend(lru_files)

    return {
        "evicted_ttl_count": evicted_ttl,
        "evicted_lru_count": lru_count,
        "reclaimed_bytes": reclaimed_bytes,
        "remaining_bytes": total_bytes,
        "evicted_files": evicted_files,
        "dry_run": dry_run,
    }


def sweep_stale_temp_files(
    root_dir: Union[str, Path],
    max_age_seconds: float = 3600.0,
) -> List[str]:
    """
    Sweeps stale temporary files (*.tmp_*, .tmp_*) older than max_age_seconds.
    Returns list of unlinked file paths.
    """
    p = Path(root_dir).expanduser().resolve()
    if not p.exists():
        return []

    now = time.time()
    unlinked: List[str] = []

    for root, _, files in os.walk(p):
        for f in files:
            if ".tmp_" in f or f.startswith(".tmp_"):
                file_path = Path(root) / f
                try:
                    st = file_path.stat()
                    if now - st.st_mtime > max_age_seconds:
                        file_path.unlink(missing_ok=True)
                        unlinked.append(str(file_path))
                except (OSError, FileNotFoundError):
                    continue

    return unlinked

