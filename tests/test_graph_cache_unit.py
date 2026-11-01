"""Unit tests for GenerationalGraphCache."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.graph_cache import (
    GenerationalGraphCache,
    cached_load_graph,
    get_global_graph_cache,
    read_graph_from_disk,
)
from dual_pipeline.graph_retrieval_core.traversal import _load_graph
from dual_pipeline.release_core.manager import ReleaseManager


class TestGenerationalGraphCache(unittest.TestCase):
    """Test GenerationalGraphCache behavior and invalidation contracts."""

    @classmethod
    def setUpClass(cls) -> None:
        manager = ReleaseManager(Path(".local/dual_pipeline"))
        cls.pair = manager.active_pair()
        cls.orig_graph = manager._resolve(cls.pair["graph_path"])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "graph.sqlite"
        shutil.copy2(self.orig_graph, self.graph_path)
        self.cache = GenerationalGraphCache()

    def tearDown(self) -> None:
        self.cache.clear()
        self.temp_dir.cleanup()

    def test_cache_hit_returns_identical_tuple(self) -> None:
        g1 = self.cache.get(self.graph_path)
        g2 = self.cache.get(self.graph_path)
        self.assertIs(g1, g2)
        stats = self.cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["cached_graphs"], 1)

    def test_structural_completeness(self) -> None:
        g = self.cache.get(self.graph_path)
        self.assertEqual(len(g), 7)
        nodes, assertions, supporting, adjacency, contradicting, conflicts, excluded = g

        self.assertIsInstance(nodes, dict)
        self.assertIsInstance(assertions, dict)
        self.assertIsInstance(supporting, dict)
        self.assertIsInstance(adjacency, dict)
        self.assertIsInstance(contradicting, dict)
        self.assertIsInstance(conflicts, dict)
        self.assertIsInstance(excluded, dict)

        self.assertGreater(len(nodes), 0)
        self.assertGreater(len(assertions), 0)

    def test_matches_read_graph_from_disk(self) -> None:
        disk_data = read_graph_from_disk(self.graph_path)
        cached_data = self.cache.get(self.graph_path)

        # Structure checks: node count and assertion count match
        self.assertEqual(len(disk_data[0]), len(cached_data[0]))
        self.assertEqual(len(disk_data[1]), len(cached_data[1]))
        self.assertEqual(set(disk_data[0].keys()), set(cached_data[0].keys()))

    def test_invalidation_on_file_mutation(self) -> None:
        g1 = self.cache.get(self.graph_path)
        time.sleep(0.01)

        conn = sqlite3.connect(str(self.graph_path))
        conn.execute("CREATE TABLE IF NOT EXISTS _test_mutation (k TEXT)")
        conn.commit()
        conn.close()

        g2 = self.cache.get(self.graph_path)
        self.assertIsNot(g1, g2)
        stats = self.cache.stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 2)
        self.assertEqual(stats["cached_graphs"], 1)

    def test_explicit_invalidate_and_clear(self) -> None:
        self.cache.get(self.graph_path)
        self.assertEqual(self.cache.stats()["cached_graphs"], 1)

        self.cache.invalidate(self.graph_path)
        self.assertEqual(self.cache.stats()["cached_graphs"], 0)

        self.cache.get(self.graph_path)
        self.assertEqual(self.cache.stats()["cached_graphs"], 1)

        self.cache.clear()
        self.assertEqual(self.cache.stats()["cached_graphs"], 0)
        self.assertEqual(self.cache.stats()["hits"], 0)
        self.assertEqual(self.cache.stats()["misses"], 0)

    def test_global_singleton_and_helpers(self) -> None:
        gc1 = get_global_graph_cache()
        gc2 = get_global_graph_cache()
        self.assertIs(gc1, gc2)

        g = cached_load_graph(self.graph_path)
        self.assertEqual(len(g), 7)

    def test_traversal_load_graph_uses_cache(self) -> None:
        global_cache = get_global_graph_cache()
        global_cache.clear()

        g1 = _load_graph(self.graph_path)
        stats1 = global_cache.stats()
        self.assertEqual(stats1["misses"], 1)
        self.assertEqual(stats1["hits"], 0)

        g2 = _load_graph(self.graph_path)
        stats2 = global_cache.stats()
        self.assertEqual(stats2["misses"], 1)
        self.assertEqual(stats2["hits"], 1)
        self.assertIs(g1, g2)


if __name__ == "__main__":
    unittest.main()
