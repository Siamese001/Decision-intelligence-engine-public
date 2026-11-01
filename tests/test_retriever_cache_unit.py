"""Unit tests for GenerationalRetrieverCache."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.index_core.retriever import HybridRetriever
from dual_pipeline.index_core.retriever_cache import (
    GenerationalRetrieverCache,
    cached_retriever,
    get_global_retriever_cache,
)
from dual_pipeline.release_core.manager import ReleaseManager
from tests.embedding_fixture import MetadataOnlyBgeM3Backend


class TestGenerationalRetrieverCache(unittest.TestCase):
    """Test GenerationalRetrieverCache behavior and invalidation contracts."""

    @classmethod
    def setUpClass(cls) -> None:
        manager = ReleaseManager(Path(".local/dual_pipeline"))
        cls.pair = manager.active_pair()
        cls.orig_sidecar = manager._resolve(cls.pair["sidecar_path"])
        cls.generation_id = cls.pair.get("generation_id")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sidecar_path = Path(self.temp_dir.name) / "sidecar.sqlite"
        shutil.copy2(self.orig_sidecar, self.sidecar_path)
        self.backend = MetadataOnlyBgeM3Backend()
        self.cache = GenerationalRetrieverCache()

    def tearDown(self) -> None:
        self.cache.clear()
        self.temp_dir.cleanup()

    def test_cache_hit_returns_identical_instance(self) -> None:
        r1 = self.cache.get(self.sidecar_path, self.backend, generation_id=self.generation_id)
        r2 = self.cache.get(self.sidecar_path, self.backend, generation_id=self.generation_id)
        self.assertIs(r1, r2)
        stats = self.cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["cached_retrievers"], 1)

    def test_invalidation_on_mtime_change(self) -> None:
        r1 = self.cache.get(self.sidecar_path, self.backend, generation_id=self.generation_id)
        time.sleep(0.01)
        # Modify the sidecar mtime and size
        conn = sqlite3.connect(str(self.sidecar_path))
        conn.execute("INSERT OR REPLACE INTO metadata VALUES ('test_mutation', 'true')")
        conn.commit()
        conn.close()

        r2 = self.cache.get(self.sidecar_path, self.backend, generation_id=self.generation_id)
        self.assertIsNot(r1, r2)
        stats = self.cache.stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 2)
        self.assertEqual(stats["cached_retrievers"], 1)

    def test_invalidation_on_generation_id_change(self) -> None:
        r1 = self.cache.get(self.sidecar_path, self.backend, generation_id="gen_001")
        r2 = self.cache.get(self.sidecar_path, self.backend, generation_id="gen_002")
        self.assertIsNot(r1, r2)
        stats = self.cache.stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 2)

    def test_invalidation_on_backend_change(self) -> None:
        key1 = self.cache._make_key(self.sidecar_path.resolve(), self.backend, self.generation_id)
        alt_backend = MetadataOnlyBgeM3Backend()
        alt_backend.model_revision = "alt_revision_test"
        key2 = self.cache._make_key(self.sidecar_path.resolve(), alt_backend, self.generation_id)
        self.assertNotEqual(key1, key2)

    def test_explicit_invalidate_and_clear(self) -> None:
        self.cache.get(self.sidecar_path, self.backend, generation_id=self.generation_id)
        self.assertEqual(self.cache.stats()["cached_retrievers"], 1)
        self.cache.invalidate(self.sidecar_path)
        self.assertEqual(self.cache.stats()["cached_retrievers"], 0)

        self.cache.get(self.sidecar_path, self.backend, generation_id=self.generation_id)
        self.assertEqual(self.cache.stats()["cached_retrievers"], 1)
        self.cache.clear()
        self.assertEqual(self.cache.stats()["cached_retrievers"], 0)
        self.assertEqual(self.cache.stats()["hits"], 0)
        self.assertEqual(self.cache.stats()["misses"], 0)

    def test_global_retriever_cache_singleton(self) -> None:
        gc1 = get_global_retriever_cache()
        gc2 = get_global_retriever_cache()
        self.assertIs(gc1, gc2)

        r = cached_retriever(self.sidecar_path, self.backend, generation_id=self.generation_id)
        self.assertIsInstance(r, HybridRetriever)

    def test_lane_artifacts_cached_across_retrieval(self) -> None:
        retriever = self.cache.get(
            self.sidecar_path, self.backend, generation_id=self.generation_id
        )
        self.assertNotIn("approved", retriever._lane_artifacts_cache)

        fake_encoded = {
            "dense": [1.0] + [0.0] * 1023,
            "sparse": {"test": 1.0},
            "colbert": [[0.0] * 32],
        }
        res1 = retriever.retrieve_lane(
            query="test",
            lane="approved",
            classification="internal",
            audiences=["engineering"],
            encoded_query=fake_encoded,
        )
        self.assertIn("approved", retriever._lane_artifacts_cache)
        cached_count = len(retriever._lane_artifacts_cache["approved"])
        self.assertGreater(cached_count, 0)

        # Second call uses in-memory cache without hitting SQLite retrieval_record query
        res2 = retriever.retrieve_lane(
            query="test2",
            lane="approved",
            classification="internal",
            audiences=["engineering"],
            encoded_query=fake_encoded,
        )
        self.assertEqual(len(res1["results"]), len(res2["results"]))


if __name__ == "__main__":
    unittest.main()
