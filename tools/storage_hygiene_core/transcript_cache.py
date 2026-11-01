"""Transcript cache hygiene planner and scanner."""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dual_pipeline.archival_manager import cleanup_transcript_cache

from .pins import get_main_repo_root
from .policy import StorageRetentionPolicy, load_storage_retention_policy

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
MAIN_REPO_ROOT = get_main_repo_root(ROOT)
DEFAULT_CACHE_DIR = MAIN_REPO_ROOT / "output" / "transcript_cache"


@dataclass
class TranscriptCachePlanResult:
    total_files_count: int = 0
    total_bytes: int = 0
    wav_files_count: int = 0
    wav_bytes: int = 0
    preserved_files_count: int = 0
    preserved_bytes: int = 0
    candidate_count: int = 0
    reclaimable_bytes: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    preserved_summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_transcript_cache(
    cache_dir: Path | None = None,
    policy: StorageRetentionPolicy | None = None,
) -> TranscriptCachePlanResult:
    """Analyze output/transcript_cache without deleting any files."""
    target_dir = (cache_dir or DEFAULT_CACHE_DIR).expanduser().resolve()
    pol = policy or load_storage_retention_policy()

    result = TranscriptCachePlanResult()
    if not target_dir.exists():
        return result

    ttl_seconds = pol.transcript_cache.wav_ttl_days * 86400.0

    # Execute non-destructive dry-run targeting strictly .wav files
    eviction_summary = cleanup_transcript_cache(
        target_dir,
        max_age_seconds=ttl_seconds,
        dry_run=True,
        include_globs=("*.wav",),
    )

    result.candidate_count = eviction_summary.get("evicted_ttl_count", 0) + eviction_summary.get(
        "evicted_lru_count", 0
    )
    result.reclaimable_bytes = eviction_summary.get("reclaimed_bytes", 0)
    result.candidates = eviction_summary.get("evicted_files", [])

    # Audit total directory files
    by_ext: dict[str, int] = {}
    for root, _, files in os.walk(target_dir):
        for f in files:
            p = Path(root) / f
            try:
                st = p.stat()
                fsize = st.st_size
                result.total_files_count += 1
                result.total_bytes += fsize

                ext = p.suffix.lower() or "no_ext"
                by_ext[ext] = by_ext.get(ext, 0) + 1

                if ext == ".wav":
                    result.wav_files_count += 1
                    result.wav_bytes += fsize
                else:
                    result.preserved_files_count += 1
                    result.preserved_bytes += fsize
            except (OSError, FileNotFoundError):
                continue

    result.preserved_summary = by_ext
    return result
