"""In-memory memoization cache for validated semantic cache run chains.

Prevents repeated synchronous disk reads of learning_events and ledger_payloads
during semantic cache candidate validation.
"""

from __future__ import annotations

import threading
from typing import NamedTuple


class _ChainKey(NamedTuple):
    run_id: str
    expected_chain: str


class RunChainDigestCache:
    """Thread-safe memoization cache for validated run event chains.

    Prevents repeated synchronous disk reads of learning_events and ledger_payloads
    during semantic cache candidate validation.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._lock = threading.Lock()
        self._cache: dict[_ChainKey, bool] = {}
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, run_id: str, expected_chain: str) -> bool | None:
        """Return cached validation status (True/False) or None if not cached."""
        key = _ChainKey(run_id=run_id, expected_chain=expected_chain)
        with self._lock:
            val = self._cache.pop(key, None)
            if val is not None:
                self._cache[key] = val
                self._hits += 1
            else:
                self._misses += 1
            return val

    def set(self, run_id: str, expected_chain: str, is_valid: bool) -> None:
        """Cache validation status with simple size bounding."""
        key = _ChainKey(run_id=run_id, expected_chain=expected_chain)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._max_size:
                # Evict oldest entry (dict maintains insertion order)
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[key] = is_valid

    def clear(self) -> None:
        """Clear all cached validation statuses."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Return number of cached entries."""
        with self._lock:
            return len(self._cache)

    @property
    def hits(self) -> int:
        """Return number of cache hits."""
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        """Return number of cache misses."""
        with self._lock:
            return self._misses

    def reset_metrics(self) -> None:
        """Reset hit and miss counters."""
        with self._lock:
            self._hits = 0
            self._misses = 0


_GLOBAL_CHAIN_CACHE = RunChainDigestCache()


def get_global_chain_cache() -> RunChainDigestCache:
    """Return the global RunChainDigestCache singleton."""
    return _GLOBAL_CHAIN_CACHE
