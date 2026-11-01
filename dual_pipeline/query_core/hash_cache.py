"""Query core hash cache facade re-exporting GenerationalHashCache."""

from __future__ import annotations

from dual_pipeline.hash_cache import (
    GenerationalHashCache,
    cached_sha256_file,
    get_global_hash_cache,
)

__all__ = [
    "GenerationalHashCache",
    "cached_sha256_file",
    "get_global_hash_cache",
]
