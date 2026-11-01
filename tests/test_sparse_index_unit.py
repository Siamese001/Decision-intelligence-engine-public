"""Unit tests for in-memory LaneSparseIndex and SparseIndexCache (Wave 5)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.index_core.sparse_index import (
    LaneSparseIndex,
    SparseIndexCache,
    get_global_sparse_index_cache,
)


def _create_test_sidecar(
    db_path: Path,
    records: list[tuple[str, str]],  # (record_id, lane)
    sparse_postings: list[tuple[str, str, float]],  # (record_id, token, weight)
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE retrieval_record (
            record_id TEXT PRIMARY KEY,
            lane TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE sparse_weight (
            record_id TEXT NOT NULL,
            token TEXT NOT NULL,
            weight REAL NOT NULL
        )"""
    )
    for rid, lane in records:
        conn.execute("INSERT INTO retrieval_record VALUES (?, ?)", (rid, lane))
    for rid, tok, weight in sparse_postings:
        conn.execute("INSERT INTO sparse_weight VALUES (?, ?, ?)", (rid, tok, weight))
    conn.commit()
    conn.close()


class TestSparseIndexUnit(unittest.TestCase):
    def test_lane_sparse_index_properties(self) -> None:
        idx = LaneSparseIndex(
            lane="approved",
            record_ids=["doc_1", "doc_2"],
            id_to_index={"doc_1": 0, "doc_2": 1},
            postings={},
        )
        self.assertEqual(idx.count, 2)
        self.assertEqual(idx.token_count, 0)

    def test_empty_query_and_empty_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            _create_test_sidecar(db_path, [("doc_1", "approved")], [("doc_1", "alpha", 0.8)])

            cache = SparseIndexCache()
            # Empty query returns empty list
            self.assertEqual(cache.score_sparse(db_path, "approved", {}), [])

            # Empty lane returns empty list
            res = cache.score_sparse(db_path, "nonexistent", {"alpha": 1.0})
            self.assertEqual(res, [])

    def test_score_sparse_exact_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            records = [
                ("doc_1", "approved"),
                ("doc_2", "approved"),
                ("doc_3", "approved"),
            ]
            postings = [
                ("doc_1", "revenue", 0.5),
                ("doc_1", "growth", 0.3),
                ("doc_2", "revenue", 0.9),
                ("doc_3", "growth", 0.7),
            ]
            _create_test_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            query = {"revenue": 2.0, "growth": 1.0}

            scores = cache.score_sparse(db_path, "approved", query)
            self.assertEqual(len(scores), 3)
            self.assertEqual(scores[0], (1.8, "doc_2"))
            self.assertEqual(scores[1], (1.3, "doc_1"))
            self.assertEqual(scores[2], (0.7, "doc_3"))

    def test_sparse_cache_hits_and_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            _create_test_sidecar(
                db_path,
                [("doc_1", "approved"), ("doc_2", "candidate")],
                [("doc_1", "term", 1.0), ("doc_2", "term", 0.5)],
            )

            cache = SparseIndexCache()
            self.assertEqual(cache.stats()["hits"], 0)
            self.assertEqual(cache.stats()["misses"], 0)

            # First access: cache miss
            cache.score_sparse(db_path, "approved", {"term": 1.0})
            self.assertEqual(cache.stats()["misses"], 1)
            self.assertEqual(cache.stats()["hits"], 0)

            # Second access: cache hit
            cache.score_sparse(db_path, "approved", {"term": 2.0})
            self.assertEqual(cache.stats()["hits"], 1)
            self.assertEqual(cache.stats()["misses"], 1)

            # Different lane: cache miss
            cache.score_sparse(db_path, "candidate", {"term": 1.0})
            self.assertEqual(cache.stats()["misses"], 2)
            self.assertEqual(cache.stats()["hits"], 1)

    def test_authorized_record_ids_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            records = [
                ("doc_1", "approved"),
                ("doc_2", "approved"),
                ("doc_3", "approved"),
            ]
            postings = [
                ("doc_1", "target", 1.0),
                ("doc_2", "target", 2.0),
                ("doc_3", "target", 3.0),
            ]
            _create_test_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            res = cache.score_sparse(
                db_path,
                "approved",
                {"target": 1.0},
                authorized_record_ids={"doc_1", "doc_3"},
            )
            self.assertEqual(len(res), 2)
            self.assertEqual(res[0], (3.0, "doc_3"))
            self.assertEqual(res[1], (1.0, "doc_1"))

    def test_sparse_limit_and_tie_breaking(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            records = [
                ("doc_b", "approved"),
                ("doc_a", "approved"),
                ("doc_c", "approved"),
            ]
            postings = [
                ("doc_b", "score_tie", 1.0),
                ("doc_a", "score_tie", 1.0),
                ("doc_c", "score_tie", 0.5),
            ]
            _create_test_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            res = cache.score_sparse(db_path, "approved", {"score_tie": 1.0}, sparse_limit=2)
            self.assertEqual(len(res), 2)
            self.assertEqual(res[0], (1.0, "doc_a"))
            self.assertEqual(res[1], (1.0, "doc_b"))

    def test_invalidation_on_mtime_and_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            _create_test_sidecar(
                db_path,
                [("doc_1", "approved")],
                [("doc_1", "query_token", 1.0)],
            )

            cache = SparseIndexCache()
            cache.score_sparse(db_path, "approved", {"query_token": 1.0}, generation_id="gen_v1")
            self.assertEqual(cache.stats()["misses"], 1)
            self.assertEqual(cache.stats()["hits"], 0)

            # Generation ID change forces eviction
            cache.score_sparse(db_path, "approved", {"query_token": 1.0}, generation_id="gen_v2")
            self.assertEqual(cache.stats()["misses"], 2)
            self.assertEqual(cache.stats()["hits"], 0)

            # Mtime change forces eviction
            new_mtime = os.path.getmtime(db_path) + 10.0
            os.utime(db_path, (new_mtime, new_mtime))
            cache.score_sparse(db_path, "approved", {"query_token": 1.0}, generation_id="gen_v2")
            self.assertEqual(cache.stats()["misses"], 3)

    def test_global_sparse_index_cache_lifecycle(self) -> None:
        global_cache = get_global_sparse_index_cache()
        global_cache.clear()
        self.assertEqual(global_cache.stats()["hits"], 0)
        self.assertEqual(global_cache.stats()["misses"], 0)
        self.assertEqual(global_cache.stats()["cached_lanes"], 0)


if __name__ == "__main__":
    unittest.main()
