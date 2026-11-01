"""Generational in-memory vector cache for multi-lane dense retrieval.

Eliminates per-query full-table SQLite vector scans and deserialization heap churn
by pre-unpacking float32 NumPy matrices keyed by lane and active generation.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from storage.connection import sqlite3

logger = logging.getLogger("dual_pipeline.index_core.vector_cache")


@dataclass
class LaneDenseVectors:
    """Pre-unpacked dense vectors for a single retrieval lane."""
    lane: str
    record_ids: List[str]
    matrix: np.ndarray  # 2D array of shape (N, D), dtype float32
    id_to_index: Dict[str, int]
    record_id_set: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.record_id_set and self.record_ids:
            self.record_id_set = frozenset(self.record_ids)

    @property
    def count(self) -> int:
        return len(self.record_ids)

    @property
    def dimensions(self) -> int:
        return self.matrix.shape[1] if self.matrix.ndim == 2 and self.matrix.shape[0] > 0 else 0


class VectorCache:
    """Thread-safe generational in-memory vector cache for dense retrieval."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation_id: Optional[str] = None
        self._sidecar_path: Optional[str] = None
        self._sidecar_mtime: float = 0.0
        self._lane_cache: Dict[str, LaneDenseVectors] = {}

    def clear(self) -> None:
        """Evicts all cached matrices and resets generation tracking."""
        with self._lock:
            self._generation_id = None
            self._sidecar_path = None
            self._sidecar_mtime = 0.0
            self._lane_cache.clear()
        try:
            from .sparse_index import get_global_sparse_index_cache
            get_global_sparse_index_cache().clear()
        except ImportError:
            pass

    def _check_invalidation(
        self,
        sidecar_path: Path,
        generation_id: Optional[str] = None,
    ) -> None:
        """Invalidates cache if sidecar path, mtime, or generation ID changes."""
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
            logger.debug(
                f"[VECTOR-CACHE] Invalidating cache: path={resolved}, "
                f"gen={generation_id} (prev_gen={self._generation_id})"
            )
            self._lane_cache.clear()
            self._sidecar_path = resolved
            self._sidecar_mtime = mtime
            self._generation_id = generation_id

    def get_or_load_lane(
        self,
        sidecar_path: Path | str,
        lane: str,
        generation_id: Optional[str] = None,
    ) -> LaneDenseVectors:
        """Loads and caches dense vectors for a lane directly into contiguous float32 matrix."""
        path = Path(sidecar_path).resolve()
        with self._lock:
            self._check_invalidation(path, generation_id)
            if lane in self._lane_cache:
                return self._lane_cache[lane]

            # Fast direct SQLite query into zero-copy float32 numpy buffers
            record_ids: List[str] = []
            buffers: List[np.ndarray] = []

            from ..storage.governed_context import GovernedStorageContext
            conn = GovernedStorageContext.connect(str(path), readonly=True)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT d.record_id, d.vector
                    FROM dense_vector d
                    JOIN retrieval_record r ON r.record_id = d.record_id
                    WHERE r.lane = ?
                    ORDER BY d.record_id
                    """,
                    (lane,),
                )
                for row in cursor:
                    record_ids.append(row["record_id"])
                    # np.frombuffer is zero-copy and avoids creating 1024 Python floats
                    vec = np.frombuffer(row["vector"], dtype=np.float32)
                    buffers.append(vec)
            finally:
                conn.close()

            if buffers:
                matrix = np.vstack(buffers).astype(np.float32)
            else:
                matrix = np.empty((0, 1024), dtype=np.float32)

            id_to_index = {rid: i for i, rid in enumerate(record_ids)}
            lane_data = LaneDenseVectors(
                lane=lane,
                record_ids=record_ids,
                matrix=matrix,
                id_to_index=id_to_index,
                record_id_set=frozenset(record_ids),
            )
            self._lane_cache[lane] = lane_data
            logger.debug(f"[VECTOR-CACHE] Cached {lane_data.count} dense vectors for lane '{lane}'")
            return lane_data

    def score_dense(
        self,
        sidecar_path: Path | str,
        lane: str,
        query_vector: List[float] | np.ndarray,
        authorized_record_ids: Optional[Set[str]] = None,
        generation_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[float, str]]:
        """
        Executes in-memory matrix multiplication over pre-cached dense vectors.
        Returns sorted [(score, record_id), ...] optionally bounded to top-k.
        """
        lane_data = self.get_or_load_lane(sidecar_path, lane, generation_id)
        if lane_data.count == 0:
            return []

        q_vec = np.asarray(query_vector, dtype=np.float32)

        # Authorized ID filtering with zero-copy fastpath
        if authorized_record_ids is not None:
            if len(authorized_record_ids) >= lane_data.count and lane_data.record_id_set.issubset(
                authorized_record_ids
            ):
                sims = np.dot(lane_data.matrix, q_vec)
                doc_ids = lane_data.record_ids
            else:
                indices = [
                    lane_data.id_to_index[rid]
                    for rid in authorized_record_ids
                    if rid in lane_data.id_to_index
                ]
                if not indices:
                    return []
                indices.sort()
                sub_matrix = lane_data.matrix[indices]
                sims = np.dot(sub_matrix, q_vec)
                doc_ids = [lane_data.record_ids[i] for i in indices]
        else:
            sims = np.dot(lane_data.matrix, q_vec)
            doc_ids = lane_data.record_ids

        # O(N) top-k partition when candidate count exceeds limit
        if limit is not None and 0 < limit < len(sims):
            part_indices = np.argpartition(-sims, limit - 1)[:limit]
            min_score = float(sims[part_indices].min())
            if np.count_nonzero(sims == min_score) > 1:
                candidate_indices = np.where(sims >= min_score)[0]
            else:
                candidate_indices = part_indices

            scores = [(float(sims[i]), doc_ids[i]) for i in candidate_indices]
            scores.sort(key=lambda item: (-item[0], item[1]))
            return scores[:limit]

        scores = [(float(score), doc_id) for doc_id, score in zip(doc_ids, sims, strict=True)]
        scores.sort(key=lambda item: (-item[0], item[1]))
        if limit is not None:
            return scores[:limit]
        return scores


_GLOBAL_VECTOR_CACHE = VectorCache()


def get_global_vector_cache() -> VectorCache:
    """Returns the singleton global in-memory vector cache."""
    return _GLOBAL_VECTOR_CACHE


def clear_global_vector_cache() -> None:
    """Resets the singleton global in-memory vector cache."""
    _GLOBAL_VECTOR_CACHE.clear()
