"""Generational in-memory retriever cache for HybridRetriever.

Eliminates redundant SQLite metadata validation, backend alignment verification,
and repetitive artifact/ColBERT parsing across queries within the same generation.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import numpy as np

from .backends import EmbeddingBackend
from .vector_cache import VectorCache

if TYPE_CHECKING:
    from .retriever import HybridRetriever

logger = logging.getLogger("dual_pipeline.index_core.retriever_cache")

K = TypeVar("K")
V = TypeVar("V")


class BoundedLRUDict(OrderedDict[K, V]):
    """Thread-compatible OrderedDict subclass with maximum entry bound and LRU eviction."""

    def __init__(self, maxsize: int, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.maxsize = max(1, maxsize)

    def __getitem__(self, key: K) -> V:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def get(self, key: K, default: Any = None) -> Any:
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        return default

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.maxsize:
            self.popitem(last=False)

    def setdefault(self, key: K, default: V) -> V:
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        self[key] = default
        return default


class PrePaddedColbertCache:
    """Thread-safe cache for pre-padded ColBERT candidate matrices with entry and byte bounds."""

    def __init__(
        self,
        max_entries: int = 128,
        max_bytes: int = 64 * 1024 * 1024,  # 64 MB default byte budget
    ) -> None:
        self._lock = threading.RLock()
        self._cache: dict[
            tuple[str, ...],
            tuple[np.ndarray, np.ndarray, np.ndarray | None],
        ] = {}
        self._entry_bytes: dict[tuple[str, ...], int] = {}
        self._max_entries = max(1, max_entries)
        self._max_bytes = max(1024, max_bytes)
        self._current_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions_count = 0

    @staticmethod
    def _calculate_bytes(
        doc_stack: np.ndarray,
        lengths: np.ndarray,
        mask: np.ndarray | None,
    ) -> int:
        b = int(doc_stack.nbytes + lengths.nbytes)
        if mask is not None:
            b += int(mask.nbytes)
        return b

    def get(
        self, record_ids: tuple[str, ...]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None] | None:
        with self._lock:
            val = self._cache.pop(record_ids, None)
            if val is not None:
                self._cache[record_ids] = val
                self._hits += 1
            else:
                self._misses += 1
            return val

    def set(
        self,
        record_ids: tuple[str, ...],
        doc_stack: np.ndarray,
        lengths: np.ndarray,
        mask: np.ndarray | None,
    ) -> None:
        new_bytes = self._calculate_bytes(doc_stack, lengths, mask)
        with self._lock:
            if new_bytes > self._max_bytes:
                return

            if record_ids in self._entry_bytes:
                self._current_bytes -= self._entry_bytes[record_ids]

            while self._cache and (
                len(self._cache) >= self._max_entries
                or (self._current_bytes + new_bytes > self._max_bytes)
            ):
                first_key = next(iter(self._cache))
                del self._cache[first_key]
                removed_bytes = self._entry_bytes.get(first_key, 0)
                if first_key in self._entry_bytes:
                    del self._entry_bytes[first_key]
                self._current_bytes -= removed_bytes
                self._evictions_count += 1

            self._cache[record_ids] = (doc_stack, lengths, mask)
            self._entry_bytes[record_ids] = new_bytes
            self._current_bytes += new_bytes

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._entry_bytes.clear()
            self._current_bytes = 0
            self._hits = 0
            self._misses = 0
            self._evictions_count = 0

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def evictions_count(self) -> int:
        with self._lock:
            return self._evictions_count

    def stats(self, detailed: bool = False) -> dict[str, int]:
        with self._lock:
            res = {
                "hits": self._hits,
                "misses": self._misses,
                "cached_blocks": len(self._cache),
            }
            if detailed:
                res.update(
                    {
                        "current_bytes": self._current_bytes,
                        "max_bytes": self._max_bytes,
                        "max_entries": self._max_entries,
                        "evictions_count": self._evictions_count,
                    }
                )
            return res


class GenerationalRetrieverCache:
    """Thread-safe generational cache for HybridRetriever instances."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[tuple[Any, ...], HybridRetriever] = {}
        self._hits: int = 0
        self._misses: int = 0

    def _make_key(
        self,
        resolved_path: Path,
        backend: EmbeddingBackend,
        generation_id: str | None,
    ) -> tuple[Any, ...]:
        try:
            stat = os.stat(resolved_path)
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError:
            mtime_ns = 0
            size = 0

        return (
            str(resolved_path),
            mtime_ns,
            size,
            getattr(backend, "backend_id", ""),
            getattr(backend, "model_id", ""),
            getattr(backend, "model_revision", ""),
            getattr(backend, "model_manifest_digest", "") or "",
            generation_id or "",
        )

    def get(
        self,
        sidecar_path: Path | str,
        backend: EmbeddingBackend,
        generation_id: str | None = None,
        vector_cache: VectorCache | None = None,
    ) -> HybridRetriever:
        """Retrieves or creates a cached HybridRetriever bound to generation/mtime."""
        path = Path(sidecar_path).resolve()
        key = self._make_key(path, backend, generation_id)

        with self._lock:
            if key in self._cache:
                self._hits += 1
                return self._cache[key]

            self._misses += 1
            from .retriever import HybridRetriever

            retriever = HybridRetriever(
                path,
                backend,
                vector_cache=vector_cache,
            )
            # Evict stale entries for the same sidecar path
            path_str = str(path)
            stale_keys = [k for k in self._cache if k[0] == path_str]
            for stale_key in stale_keys:
                del self._cache[stale_key]

            self._cache[key] = retriever
            return retriever

    def invalidate(self, sidecar_path: Path | str | None = None) -> None:
        """Invalidates cache entries for a specific path, or all entries if None."""
        with self._lock:
            if sidecar_path is None:
                self._cache.clear()
            else:
                path_str = str(Path(sidecar_path).resolve())
                stale_keys = [k for k in self._cache if k[0] == path_str]
                for stale_key in stale_keys:
                    del self._cache[stale_key]

    def clear(self) -> None:
        """Clears all cached retrievers and resets performance metrics."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        """Returns hit, miss, and cached item counts."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "cached_retrievers": len(self._cache),
            }


_GLOBAL_RETRIEVER_CACHE: GenerationalRetrieverCache | None = None
_GLOBAL_LOCK = threading.RLock()


def get_global_retriever_cache() -> GenerationalRetrieverCache:
    """Returns the process-wide generational retriever cache singleton."""
    global _GLOBAL_RETRIEVER_CACHE
    with _GLOBAL_LOCK:
        if _GLOBAL_RETRIEVER_CACHE is None:
            _GLOBAL_RETRIEVER_CACHE = GenerationalRetrieverCache()
        return _GLOBAL_RETRIEVER_CACHE


def cached_retriever(
    sidecar_path: Path | str,
    backend: EmbeddingBackend,
    generation_id: str | None = None,
) -> HybridRetriever:
    """Convenience helper to retrieve a cached retriever from the global cache."""
    return get_global_retriever_cache().get(
        sidecar_path,
        backend,
        generation_id,
    )
