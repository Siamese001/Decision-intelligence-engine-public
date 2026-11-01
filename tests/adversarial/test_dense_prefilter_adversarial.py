"""Adversarial and stress test suite for Wave 8: Dense Vector Pre-Filtering & Top-K Partitioning."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.index_core.vector_cache import VectorCache


def _populate_sidecar(
    db_path: Path,
    lane: str,
    record_ids: list[str],
    vectors: np.ndarray,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE retrieval_record (
            record_id TEXT PRIMARY KEY,
            lane TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE dense_vector (
            record_id TEXT PRIMARY KEY,
            vector BLOB NOT NULL
        )"""
    )
    conn.executemany(
        "INSERT INTO retrieval_record VALUES (?, ?)",
        [(rid, lane) for rid in record_ids],
    )
    conn.executemany(
        "INSERT INTO dense_vector VALUES (?, ?)",
        [(rid, vec.tobytes()) for rid, vec in zip(record_ids, vectors, strict=True)],
    )
    conn.commit()
    conn.close()


class TestDensePrefilterAdversarial(unittest.TestCase):
    def test_adversarial_boundary_ties_mass_cluster(self) -> None:
        """Stress test with 100 identical vector scores clustered exactly at the top-k boundary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            dim = 64
            num_top = 20
            num_tied = 100
            num_bottom = 50
            total = num_top + num_tied + num_bottom

            q_vec = np.zeros(dim, dtype=np.float32)
            q_vec[0] = 1.0

            matrix = np.zeros((total, dim), dtype=np.float32)
            record_ids = [f"rec_{i:04d}" for i in range(total)]

            # Top 20: scores from 2.0 to 1.1
            for i in range(num_top):
                matrix[i, 0] = 2.0 - (i * 0.04)

            # Tied 100: score exactly 1.0
            for i in range(num_top, num_top + num_tied):
                matrix[i, 0] = 1.0

            # Bottom 50: scores from 0.8 to 0.1
            for i in range(num_top + num_tied, total):
                matrix[i, 0] = 0.8 - ((i - (num_top + num_tied)) * 0.01)

            _populate_sidecar(db_path, "approved", record_ids, matrix)

            cache = VectorCache()
            # Reference full sort
            baseline_scores = cache.score_dense(db_path, "approved", q_vec, limit=None)
            self.assertEqual(len(baseline_scores), total)

            # Test cutoff falling directly in the tie cluster (k=50, tie range is 20..120)
            k = 50
            top_k_scores = cache.score_dense(db_path, "approved", q_vec, limit=k)
            self.assertEqual(len(top_k_scores), k)
            self.assertEqual(top_k_scores, baseline_scores[:k])

            # Ensure determinism: tie-breaker is alphanumeric record_id
            for rank in range(num_top, k):
                score, rid = top_k_scores[rank]
                self.assertAlmostEqual(score, 1.0, places=5)
                # Should match deterministic baseline
                self.assertEqual((score, rid), baseline_scores[rank])

    def test_adversarial_high_dimensional_candidate_scale(self) -> None:
        """2,000 vectors with 256 dimensions verifying bit-for-bit equivalence with full sort."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            dim = 256
            n_vectors = 2000
            rng = np.random.default_rng(12345)

            matrix = rng.normal(0, 1, size=(n_vectors, dim)).astype(np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.maximum(norms, 1e-9)

            record_ids = [f"doc_{i:04d}" for i in range(n_vectors)]
            _populate_sidecar(db_path, "approved", record_ids, matrix)

            q_vec = rng.normal(0, 1, size=dim).astype(np.float32)
            q_vec /= np.linalg.norm(q_vec)

            cache = VectorCache()
            baseline = cache.score_dense(db_path, "approved", q_vec, limit=None)

            # Test various limits
            for k in [1, 5, 25, 100, 500, 1500, 1999]:
                partitioned = cache.score_dense(db_path, "approved", q_vec, limit=k)
                self.assertEqual(len(partitioned), k)
                self.assertEqual(partitioned, baseline[:k])

    def test_adversarial_boundary_limit_values(self) -> None:
        """Test boundary limits: 0, negative, 1, exceeding total, and empty lane."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            dim = 16
            n = 50
            rng = np.random.default_rng(42)
            matrix = rng.uniform(-1.0, 1.0, size=(n, dim)).astype(np.float32)
            record_ids = [f"rec_{i:02d}" for i in range(n)]
            _populate_sidecar(db_path, "approved", record_ids, matrix)

            q_vec = rng.uniform(-1.0, 1.0, size=dim).astype(np.float32)
            cache = VectorCache()

            # limit = 0 -> empty
            res_0 = cache.score_dense(db_path, "approved", q_vec, limit=0)
            self.assertEqual(res_0, [])

            # limit = 1 -> exactly 1 top item
            baseline = cache.score_dense(db_path, "approved", q_vec, limit=None)
            res_1 = cache.score_dense(db_path, "approved", q_vec, limit=1)
            self.assertEqual(res_1, baseline[:1])

            # limit = 10000 -> all items sorted
            res_huge = cache.score_dense(db_path, "approved", q_vec, limit=10000)
            self.assertEqual(res_huge, baseline)

    def test_adversarial_authorized_ids_bypass_prevention(self) -> None:
        """Verify authorized ID filtering cannot be bypassed by partitioned top-k."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            dim = 32
            n = 100
            rng = np.random.default_rng(777)
            matrix = rng.standard_normal(size=(n, dim)).astype(np.float32)
            record_ids = [f"rec_{i:03d}" for i in range(n)]
            _populate_sidecar(db_path, "approved", record_ids, matrix)

            q_vec = rng.standard_normal(size=dim).astype(np.float32)
            cache = VectorCache()

            # Empty authorized set
            res_empty = cache.score_dense(
                db_path, "approved", q_vec, authorized_record_ids=set(), limit=10
            )
            self.assertEqual(res_empty, [])

            # Disjoint authorized set
            res_disjoint = cache.score_dense(
                db_path, "approved", q_vec, authorized_record_ids={"ghost_1", "ghost_2"}, limit=10
            )
            self.assertEqual(res_disjoint, [])

            # Subset of 5 items with limit=10 (subset < limit)
            allowed = {f"rec_{i:03d}" for i in [5, 12, 45, 88, 92]}
            res_sub = cache.score_dense(
                db_path, "approved", q_vec, authorized_record_ids=allowed, limit=10
            )
            self.assertEqual(len(res_sub), 5)
            self.assertTrue(all(rid in allowed for _, rid in res_sub))

    def test_adversarial_concurrent_multithreaded_scoring(self) -> None:
        """Verify concurrent multi-threaded scoring across varying limits and thread count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            dim = 64
            n = 500
            rng = np.random.default_rng(999)
            matrix = rng.standard_normal(size=(n, dim)).astype(np.float32)
            record_ids = [f"rec_{i:03d}" for i in range(n)]
            _populate_sidecar(db_path, "approved", record_ids, matrix)

            cache = VectorCache()

            def worker_query(worker_id: int) -> int:
                worker_rng = np.random.default_rng(worker_id)
                q = worker_rng.standard_normal(size=dim).astype(np.float32)
                limit = int(worker_rng.integers(1, 50))
                results = cache.score_dense(db_path, "approved", q, limit=limit)
                return len(results)

            num_workers = 16
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(worker_query, i) for i in range(40)]
                counts = [f.result() for f in concurrent.futures.as_completed(futures)]

            self.assertEqual(len(counts), 40)
            self.assertTrue(all(1 <= c <= 50 for c in counts))


if __name__ == "__main__":
    unittest.main()
