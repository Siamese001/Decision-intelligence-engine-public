"""Unit tests for Wave 8: Dense Vector Pre-Filtering & Top-K Partitioning."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.core import connect_sqlite
from dual_pipeline.index_core.vector_cache import LaneDenseVectors, VectorCache


class TestDensePrefilterUnit(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sidecar_path = Path(self.temp_dir.name) / "test_sidecar.db"
        self._init_test_db()
        self.cache = VectorCache()

    def tearDown(self) -> None:
        self.cache.clear()
        self.temp_dir.cleanup()

    def _init_test_db(self) -> None:
        conn = connect_sqlite(self.sidecar_path)
        conn.execute(
            """
            CREATE TABLE retrieval_record (
                record_id TEXT PRIMARY KEY,
                lane TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE dense_vector (
                record_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            )
            """
        )
        # Insert 50 test records with known 4-dimensional vectors
        np.random.seed(42)
        for i in range(50):
            rid = f"rec_{i:03d}"
            vec = np.random.randn(4).astype(np.float32)
            conn.execute("INSERT INTO retrieval_record VALUES (?, 'approved')", (rid,))
            conn.execute("INSERT INTO dense_vector VALUES (?, ?)", (rid, vec.tobytes()))
        conn.commit()
        conn.close()

    def test_score_dense_without_limit(self) -> None:
        q_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        scores = self.cache.score_dense(
            self.sidecar_path,
            "approved",
            q_vec,
            generation_id="gen-1",
        )
        self.assertEqual(len(scores), 50)
        # Assert strictly sorted descending
        for i in range(len(scores) - 1):
            self.assertGreaterEqual(scores[i][0], scores[i + 1][0])

    def test_top_k_partition_exact_parity_with_full_sort(self) -> None:
        q_vec = np.array([0.5, -0.5, 1.0, -0.2], dtype=np.float32)
        full_scores = self.cache.score_dense(
            self.sidecar_path,
            "approved",
            q_vec,
            generation_id="gen-1",
        )
        for k in [1, 5, 10, 20, 50]:
            top_k_scores = self.cache.score_dense(
                self.sidecar_path,
                "approved",
                q_vec,
                generation_id="gen-1",
                limit=k,
            )
            self.assertEqual(len(top_k_scores), min(k, 50))
            self.assertEqual(top_k_scores, full_scores[:k], f"Mismatch for top-{k} partition")

    def test_authorized_record_ids_fastpath(self) -> None:
        q_vec = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        # 1. Full set fastpath
        all_ids = {f"rec_{i:03d}" for i in range(50)}
        scores_all = self.cache.score_dense(
            self.sidecar_path,
            "approved",
            q_vec,
            authorized_record_ids=all_ids,
            generation_id="gen-1",
            limit=5,
        )
        self.assertEqual(len(scores_all), 5)

        # 2. Subset filtering
        subset_ids = {f"rec_{i:03d}" for i in range(10)}
        scores_sub = self.cache.score_dense(
            self.sidecar_path,
            "approved",
            q_vec,
            authorized_record_ids=subset_ids,
            generation_id="gen-1",
            limit=5,
        )
        self.assertEqual(len(scores_sub), 5)
        for _, rid in scores_sub:
            self.assertIn(rid, subset_ids)

    def test_synthetic_boundary_ties_handling(self) -> None:
        """Verify boundary tie resolution where multiple items share the exact cutoff score."""
        matrix = np.array(
            [
                [1.0, 0.0],
                [0.5, 0.0],
                [0.5, 0.0],
                [0.5, 0.0],
                [0.1, 0.0],
            ],
            dtype=np.float32,
        )
        record_ids = ["rec_a", "rec_b", "rec_c", "rec_d", "rec_e"]
        lane_data = LaneDenseVectors(
            lane="test_lane",
            record_ids=record_ids,
            matrix=matrix,
            id_to_index={rid: i for i, rid in enumerate(record_ids)},
        )
        resolved = str(self.sidecar_path.resolve())
        self.cache._sidecar_path = resolved
        self.cache._sidecar_mtime = os.path.getmtime(resolved)
        self.cache._generation_id = None
        self.cache._lane_cache["test_lane"] = lane_data

        q_vec = np.array([1.0, 0.0], dtype=np.float32)
        # Full sort baseline
        scores_full = self.cache.score_dense(self.sidecar_path, "test_lane", q_vec)
        # Top-3 partition
        scores_top3 = self.cache.score_dense(self.sidecar_path, "test_lane", q_vec, limit=3)
        self.assertEqual(scores_top3, scores_full[:3])
        self.assertEqual([rid for _, rid in scores_top3], ["rec_a", "rec_b", "rec_c"])

    def test_empty_lane_and_out_of_bounds_limit(self) -> None:
        q_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        # Empty lane
        empty_scores = self.cache.score_dense(self.sidecar_path, "nonexistent_lane", q_vec)
        self.assertEqual(empty_scores, [])

        # Limit larger than count
        scores = self.cache.score_dense(self.sidecar_path, "approved", q_vec, limit=1000)
        self.assertEqual(len(scores), 50)


if __name__ == "__main__":
    unittest.main()
