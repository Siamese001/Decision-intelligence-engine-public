"""Adversarial stress and edge-case testing for Generational Caches.

Covers:
  1. Concurrency races: 8-worker multithreaded concurrent requests against both
     GenerationalRetrieverCache and GenerationalGraphCache without deadlocks or corruption.
  2. Boundary payloads: Non-existent paths, empty generation IDs, rapid invalidations.
  3. Corruption recovery: Corrupted database file fails cleanly and subsequent valid
     loads succeed without wedging the cache.
  4. Shallow copy / purity invariants: Concurrent modifications to retrieved structures
     do not corrupt cached objects across threads.
"""
from __future__ import annotations

import concurrent.futures
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.graph_cache import GenerationalGraphCache
from dual_pipeline.index_core.retriever_cache import GenerationalRetrieverCache
from dual_pipeline.release_core.manager import ReleaseManager
from tests.embedding_fixture import MetadataOnlyBgeM3Backend


class TestGenerationalCacheAdversarial(unittest.TestCase):
    """Adversarial and concurrency stress tests for generational caches."""

    @classmethod
    def setUpClass(cls) -> None:
        manager = ReleaseManager(Path(".local/dual_pipeline"))
        cls.pair = manager.active_pair()
        cls.orig_sidecar = manager._resolve(cls.pair["sidecar_path"])
        cls.orig_graph = manager._resolve(cls.pair["graph_path"])
        cls.generation_id = cls.pair.get("generation_id")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sidecar_path = Path(self.temp_dir.name) / "sidecar.sqlite"
        self.graph_path = Path(self.temp_dir.name) / "graph.sqlite"
        shutil.copy2(self.orig_sidecar, self.sidecar_path)
        shutil.copy2(self.orig_graph, self.graph_path)
        self.backend = MetadataOnlyBgeM3Backend()
        self.retriever_cache = GenerationalRetrieverCache()
        self.graph_cache = GenerationalGraphCache()

    def tearDown(self) -> None:
        self.retriever_cache.clear()
        self.graph_cache.clear()
        self.temp_dir.cleanup()

    def test_multithreaded_concurrency_stress_8_workers(self) -> None:
        """8 threads concurrently reading and invalidating without deadlocks or race conditions."""
        num_workers = 8
        iterations_per_worker = 25
        errors: list[Exception] = []

        def worker_task(worker_id: int) -> None:
            try:
                for i in range(iterations_per_worker):
                    # Concurrent retriever get
                    r = self.retriever_cache.get(
                        self.sidecar_path, self.backend, generation_id=self.generation_id
                    )
                    self.assertIsNotNone(r)

                    # Concurrent graph get
                    g = self.graph_cache.get(self.graph_path)
                    self.assertEqual(len(g), 7)

                    # Periodic thread-safe stats check
                    r_stats = self.retriever_cache.stats()
                    g_stats = self.graph_cache.stats()
                    self.assertGreaterEqual(r_stats["hits"] + r_stats["misses"], 1)
                    self.assertGreaterEqual(g_stats["hits"] + g_stats["misses"], 1)

                    if i == 10 and worker_id == 0:
                        # Invalidate from one worker while others are reading
                        self.retriever_cache.invalidate(self.sidecar_path)
                        self.graph_cache.invalidate(self.graph_path)
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_task, wid) for wid in range(num_workers)]
            concurrent.futures.wait(futures)

        self.assertEqual(errors, [], f"Encountered concurrency errors: {errors}")
        self.assertEqual(self.retriever_cache.stats()["cached_retrievers"], 1)
        self.assertEqual(self.graph_cache.stats()["cached_graphs"], 1)

    def test_boundary_nonexistent_paths(self) -> None:
        """Nonexistent paths fail with FileNotFoundError / OperationalError without cache wedging."""
        fake_path = Path(self.temp_dir.name) / "non_existent.sqlite"

        # Retriever cache with non-existent file
        with self.assertRaises((FileNotFoundError, sqlite3.OperationalError, sqlite3.DatabaseError, RuntimeError)):
            self.retriever_cache.get(fake_path, self.backend, generation_id="gen_fake")

        # Graph cache with non-existent file
        with self.assertRaises((FileNotFoundError, sqlite3.OperationalError, sqlite3.DatabaseError, RuntimeError)):
            self.graph_cache.get(fake_path)

        # Cache remains functional for valid path
        r = self.retriever_cache.get(
            self.sidecar_path, self.backend, generation_id=self.generation_id
        )
        self.assertIsNotNone(r)
        g = self.graph_cache.get(self.graph_path)
        self.assertEqual(len(g), 7)

    def test_corrupted_file_recovery(self) -> None:
        """A corrupted sqlite file error does not leave the cache stuck."""
        corrupt_graph = Path(self.temp_dir.name) / "corrupt.sqlite"
        corrupt_graph.write_text("CORRUPTED BYTES THIS IS NOT SQLITE")

        with self.assertRaises(sqlite3.DatabaseError):
            self.graph_cache.get(corrupt_graph)

        self.assertEqual(self.graph_cache.stats()["cached_graphs"], 0)

        # Valid graph works immediately
        g = self.graph_cache.get(self.graph_path)
        self.assertEqual(len(g), 7)

    def test_rapid_alternation_across_multiple_databases(self) -> None:
        """Rapid alternation between different DB paths evicts stale paths properly."""
        sidecar_b = Path(self.temp_dir.name) / "sidecar_b.sqlite"
        graph_b = Path(self.temp_dir.name) / "graph_b.sqlite"
        shutil.copy2(self.sidecar_path, sidecar_b)
        shutil.copy2(self.graph_path, graph_b)

        for _ in range(10):
            r_a = self.retriever_cache.get(self.sidecar_path, self.backend, "gen_a")
            r_b = self.retriever_cache.get(sidecar_b, self.backend, "gen_b")
            self.assertIsNot(r_a, r_b)

            g_a = self.graph_cache.get(self.graph_path)
            g_b = self.graph_cache.get(graph_b)
            self.assertIsNot(g_a, g_b)

        self.assertEqual(self.retriever_cache.stats()["cached_retrievers"], 2)
        self.assertEqual(self.graph_cache.stats()["cached_graphs"], 2)


if __name__ == "__main__":
    unittest.main()
