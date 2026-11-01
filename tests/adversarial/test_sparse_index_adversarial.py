"""Adversarial and stress tests for SparseIndexCache (Wave 5)."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.index_core.sparse_index import SparseIndexCache


def _create_sidecar(
    db_path: Path,
    records: list[tuple[str, str]],
    postings: list[tuple[str, str, float]],
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
    conn.executemany("INSERT INTO retrieval_record VALUES (?, ?)", records)
    conn.executemany("INSERT INTO sparse_weight VALUES (?, ?, ?)", postings)
    conn.commit()
    conn.close()


class TestSparseIndexAdversarial(unittest.TestCase):
    def test_adversarial_large_scale_vocabulary(self) -> None:
        """Stress test with 200 documents, 1,000 tokens, and high-frequency terms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            num_docs = 200
            records = [(f"doc_{i}", "approved") for i in range(num_docs)]

            postings = []
            rng = np.random.default_rng(42)
            for i in range(num_docs):
                doc_id = f"doc_{i}"
                num_tokens = rng.integers(20, 50)
                token_ids = rng.choice(1000, size=num_tokens, replace=False)
                weights = rng.uniform(0.1, 2.0, size=num_tokens)
                for tok_id, w in zip(token_ids, weights, strict=True):
                    postings.append((doc_id, f"tok_{tok_id}", float(w)))

            _create_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            query_sparse = {f"tok_{t}": float(rng.uniform(0.5, 1.5)) for t in rng.choice(1000, size=10, replace=False)}

            scores = cache.score_sparse(db_path, "approved", query_sparse, sparse_limit=50)
            self.assertGreater(len(scores), 0)
            score_values = [s for s, _ in scores]
            self.assertTrue(all(score_values[i] >= score_values[i + 1] for i in range(len(score_values) - 1)))

    def test_adversarial_concurrency_race(self) -> None:
        """Concurrent multi-threaded scoring across 16 threads against shared cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            records = [(f"doc_{i}", "approved") for i in range(100)]
            postings = [(f"doc_{i}", f"word_{i % 10}", float(i * 0.1)) for i in range(100)]
            _create_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            num_workers = 16
            queries_per_worker = 25

            def worker_query(worker_id: int) -> list[tuple[float, str]]:
                target_token = f"word_{worker_id % 10}"
                return cache.score_sparse(db_path, "approved", {target_token: 1.0}, sparse_limit=10)

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [
                    executor.submit(worker_query, w)
                    for w in range(num_workers * queries_per_worker)
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

            self.assertEqual(len(results), num_workers * queries_per_worker)
            stats = cache.stats()
            self.assertEqual(stats["misses"], 1)
            self.assertEqual(stats["hits"], (num_workers * queries_per_worker) - 1)

    def test_adversarial_zero_and_negative_weights(self) -> None:
        """Handles zero weights, negative weights, and zero-sum outcomes gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            records = [
                ("doc_zero", "approved"),
                ("doc_neg", "approved"),
                ("doc_pos", "approved"),
            ]
            postings = [
                ("doc_zero", "term", 0.0),
                ("doc_neg", "term", -1.5),
                ("doc_pos", "term", 2.0),
            ]
            _create_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            res = cache.score_sparse(db_path, "approved", {"term": 1.0})
            self.assertEqual(len(res), 2)
            self.assertEqual(res[0], (2.0, "doc_pos"))
            self.assertEqual(res[1], (-1.5, "doc_neg"))

            res_zero = cache.score_sparse(db_path, "approved", {"term": 0.0})
            self.assertEqual(res_zero, [])

    def test_adversarial_unregistered_record_ids(self) -> None:
        """Gracefully handles postings referencing records not present in retrieval_record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            records = [("doc_valid", "approved")]
            postings = [
                ("doc_valid", "token_a", 1.0),
                ("doc_ghost", "token_a", 5.0),
            ]
            _create_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            res = cache.score_sparse(db_path, "approved", {"token_a": 1.0})
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0], (1.0, "doc_valid"))

    def test_adversarial_cache_invalidation_under_concurrent_load(self) -> None:
        """Repeatedly clears cache while reader threads execute queries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sidecar.db"
            records = [(f"doc_{i}", "approved") for i in range(50)]
            postings = [(f"doc_{i}", "shared_token", 1.0) for i in range(50)]
            _create_sidecar(db_path, records, postings)

            cache = SparseIndexCache()
            stop_event = False

            def reader() -> int:
                count = 0
                while not stop_event:
                    res = cache.score_sparse(db_path, "approved", {"shared_token": 1.0})
                    self.assertEqual(len(res), 50)
                    count += 1
                    if count > 50:
                        break
                return count

            def invalidator() -> int:
                count = 0
                while not stop_event:
                    cache.clear()
                    count += 1
                    if count > 10:
                        break
                return count

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                reader_futs = [executor.submit(reader) for _ in range(4)]
                invalidator_futs = [executor.submit(invalidator) for _ in range(2)]

                for f in invalidator_futs:
                    f.result()
                stop_event = True
                for f in reader_futs:
                    f.result()


if __name__ == "__main__":
    unittest.main()
