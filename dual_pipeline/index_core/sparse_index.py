"""In-memory generational sparse inverted index for multi-lane hybrid retrieval.

Eliminates per-query SQLite SELECT JOIN scans over sparse_weight and retrieval_record
by pre-compiling an in-memory inverted index keyed by lane and active generation.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger("dual_pipeline.index_core.sparse_index")


@dataclass
class LaneSparseIndex:
    """In-memory sparse inverted index for a single retrieval lane."""

    lane: str
    record_ids: List[str]
    id_to_index: Dict[str, int]
    postings: Dict[str, Tuple[np.ndarray, np.ndarray]]

    @property
    def count(self) -> int:
        return len(self.record_ids)

    @property
    def token_count(self) -> int:
        return len(self.postings)


class SparseIndexCache:
    """Thread-safe generational in-memory sparse index cache."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation_id: Optional[str] = None
        self._sidecar_path: Optional[str] = None
        self._sidecar_mtime: float = 0.0
        self._lane_cache: Dict[str, LaneSparseIndex] = {}
        self._hits: int = 0
        self._misses: int = 0

    def clear(self) -> None:
        """Evicts all cached sparse indices and resets tracking."""
        with self._lock:
            self._generation_id = None
            self._sidecar_path = None
            self._sidecar_mtime = 0.0
            self._lane_cache.clear()
            self._hits = 0
            self._misses = 0

    def _check_invalidation(
        self,
        sidecar_path: Path,
        generation_id: Optional[str] = None,
    ) -> None:
        resolved = str(sidecar_path.resolve())
        try:
            mtime = os.path.getmtime(resolved)
        except OSError:
            mtime = 0.0

        if (
            self._sidecar_path != resolved
            or self._sidecar_mtime != mtime
            or (generation_id is not None and self._generation_id != generation_id)
        ):
            self._lane_cache.clear()
            self._sidecar_path = resolved
            self._sidecar_mtime = mtime
            self._generation_id = generation_id

    def get_or_load_lane(
        self,
        sidecar_path: Path | str,
        lane: str,
        generation_id: Optional[str] = None,
    ) -> LaneSparseIndex:
        """Retrieves or compiles the in-memory inverted index for a lane."""
        path = Path(sidecar_path).resolve()
        with self._lock:
            self._check_invalidation(path, generation_id)
            if lane in self._lane_cache:
                self._hits += 1
                return self._lane_cache[lane]

            self._misses += 1
            from storage.connection import sqlite3

            from ..storage.governed_context import GovernedStorageContext

            conn = GovernedStorageContext.connect(str(path), readonly=True)
            try:
                conn.row_factory = sqlite3.Row
                record_ids = [
                    row["record_id"]
                    for row in conn.execute(
                        "SELECT record_id FROM retrieval_record WHERE lane = ? ORDER BY record_id",
                        (lane,),
                    )
                ]
                id_to_index = {rid: i for i, rid in enumerate(record_ids)}

                token_postings: dict[str, list[tuple[int, float]]] = {}
                for row in conn.execute(
                    """SELECT sw.record_id, sw.token, sw.weight
                       FROM sparse_weight sw
                       JOIN retrieval_record r ON r.record_id = sw.record_id
                       WHERE r.lane = ?""",
                    (lane,),
                ):
                    rid = row["record_id"]
                    idx = id_to_index.get(rid)
                    if idx is not None:
                        token = row["token"]
                        if token not in token_postings:
                            token_postings[token] = []
                        token_postings[token].append((idx, float(row["weight"])))
            finally:
                conn.close()

            compiled: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for tok, plist in token_postings.items():
                indices = np.array([idx for idx, _ in plist], dtype=np.int32)
                weights = np.array([w for _, w in plist], dtype=np.float64)
                compiled[tok] = (indices, weights)

            index = LaneSparseIndex(
                lane=lane,
                record_ids=record_ids,
                id_to_index=id_to_index,
                postings=compiled,
            )
            self._lane_cache[lane] = index
            logger.debug(
                f"[SPARSE-CACHE] Cached {index.count} records, "
                f"{index.token_count} unique tokens for lane '{lane}'"
            )
            return index

    def score_sparse(
        self,
        sidecar_path: Path | str,
        lane: str,
        query_sparse: dict[str, float],
        authorized_record_ids: Optional[Set[str]] = None,
        generation_id: Optional[str] = None,
        sparse_limit: int = 100,
    ) -> List[Tuple[float, str]]:
        """Vectorized sparse scoring against the pre-compiled in-memory index."""
        if not query_sparse:
            return []

        index = self.get_or_load_lane(sidecar_path, lane, generation_id)
        if index.count == 0:
            return []

        scores = np.zeros(index.count, dtype=np.float64)
        for tok, q_weight in query_sparse.items():
            entry = index.postings.get(tok)
            if entry is not None:
                doc_indices, weights = entry
                scores[doc_indices] += weights * float(q_weight)

        nonzero = np.flatnonzero(scores)
        if len(nonzero) == 0:
            return []

        res: List[Tuple[float, str]] = []
        for idx in nonzero:
            rid = index.record_ids[idx]
            if authorized_record_ids is None or rid in authorized_record_ids:
                res.append((float(scores[idx]), rid))

        res.sort(key=lambda item: (-item[0], item[1]))
        return res[:sparse_limit]

    def stats(self) -> dict[str, int]:
        """Returns cache telemetry metrics."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "cached_lanes": len(self._lane_cache),
            }


_GLOBAL_SPARSE_INDEX_CACHE = SparseIndexCache()


def get_global_sparse_index_cache() -> SparseIndexCache:
    """Returns the singleton global in-memory sparse index cache."""
    return _GLOBAL_SPARSE_INDEX_CACHE
