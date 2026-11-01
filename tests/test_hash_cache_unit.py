"""Unit tests for GenerationalHashCache and RunChainDigestCache."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.hash_cache import (
    GenerationalHashCache,
    cached_sha256_file,
    get_global_hash_cache,
)
from dual_pipeline.semantic_cache_core.chain_cache import (
    RunChainDigestCache,
)


class TestGenerationalHashCacheUnit(unittest.TestCase):
    """Unit tests for GenerationalHashCache."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.test_file = self.dir_path / "test_data.txt"
        self.test_file.write_text("initial content for hashing")
        self.cache_file = self.dir_path / "test_cache.json"
        self.cache = GenerationalHashCache(cache_file=self.cache_file)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cache_miss_then_hit(self) -> None:
        """First access is a miss, subsequent access is a hit."""
        expected_digest = sha256_file(self.test_file)

        digest1 = self.cache.get(self.test_file)
        self.assertEqual(digest1, expected_digest)
        self.assertEqual(self.cache.misses, 1)
        self.assertEqual(self.cache.hits, 0)

        digest2 = self.cache.get(self.test_file)
        self.assertEqual(digest2, expected_digest)
        self.assertEqual(self.cache.misses, 1)
        self.assertEqual(self.cache.hits, 1)

    def test_invalidation_on_content_change_with_size_change(self) -> None:
        """Cache automatically invalidates when content and size change."""
        digest1 = self.cache.get(self.test_file)

        self.test_file.write_text("longer new content that definitely changes the size")
        digest2 = self.cache.get(self.test_file)

        self.assertNotEqual(digest1, digest2)
        self.assertEqual(self.cache.misses, 2)
        self.assertEqual(self.cache.hits, 0)

    def test_invalidation_on_content_change_with_same_size(self) -> None:
        """Cache automatically invalidates when content changes with same size (via mtime)."""
        content1 = "same_size_content_1"
        content2 = "same_size_content_2"
        self.test_file.write_text(content1)
        digest1 = self.cache.get(self.test_file)

        # Ensure mtime changes even on filesystems with 1s resolution
        time.sleep(0.05)
        st = self.test_file.stat()
        self.test_file.write_text(content2)
        os.utime(self.test_file, (st.st_atime + 1, st.st_mtime + 1))

        digest2 = self.cache.get(self.test_file)
        self.assertNotEqual(digest1, digest2)
        self.assertEqual(self.cache.misses, 2)

    def test_explicit_invalidation_single_path(self) -> None:
        """Explicit path invalidation evicts only that path."""
        file2 = self.dir_path / "test_file_2.txt"
        file2.write_text("file 2 content")

        self.cache.get(self.test_file)
        self.cache.get(file2)
        self.assertEqual(self.cache.size(), 2)

        self.cache.invalidate(self.test_file)
        self.assertEqual(self.cache.size(), 1)

        # Accessing file2 is a hit
        self.cache.get(file2)
        self.assertEqual(self.cache.hits, 1)

        # Accessing test_file is a miss
        self.cache.get(self.test_file)
        self.assertEqual(self.cache.misses, 3)

    def test_explicit_invalidation_all(self) -> None:
        """Invalidate with None clears all entries."""
        file2 = self.dir_path / "test_file_2.txt"
        file2.write_text("file 2 content")
        self.cache.get(self.test_file)
        self.cache.get(file2)
        self.assertEqual(self.cache.size(), 2)

        self.cache.invalidate(None)
        self.assertEqual(self.cache.size(), 0)

    def test_global_hash_cache_convenience(self) -> None:
        """cached_sha256_file uses the global cache instance."""
        global_cache = get_global_hash_cache()
        global_cache.invalidate(self.test_file)

        d1 = cached_sha256_file(self.test_file)
        d2 = cached_sha256_file(self.test_file)
        self.assertEqual(d1, d2)
        self.assertEqual(d1, sha256_file(self.test_file))


class TestRunChainDigestCacheUnit(unittest.TestCase):
    """Unit tests for RunChainDigestCache."""

    def setUp(self) -> None:
        self.chain_cache = RunChainDigestCache(max_size=5)

    def test_get_set_miss_hit(self) -> None:
        """Test get, set, hit, and miss behavior."""
        self.assertIsNone(self.chain_cache.get("run-1", "chain-digest-1"))
        self.assertEqual(self.chain_cache.misses, 1)
        self.assertEqual(self.chain_cache.hits, 0)

        self.chain_cache.set("run-1", "chain-digest-1", True)
        self.assertTrue(self.chain_cache.get("run-1", "chain-digest-1"))
        self.assertEqual(self.chain_cache.hits, 1)

    def test_cache_size_bounding(self) -> None:
        """Test that cache respects max_size and evicts oldest."""
        for i in range(5):
            self.chain_cache.set(f"run-{i}", f"digest-{i}", True)
        self.assertEqual(self.chain_cache.size(), 5)

        # Adding a 6th should evict run-0
        self.chain_cache.set("run-5", "digest-5", True)
        self.assertEqual(self.chain_cache.size(), 5)
        self.assertIsNone(self.chain_cache.get("run-0", "digest-0"))
        self.assertTrue(self.chain_cache.get("run-5", "digest-5"))

    def test_clear(self) -> None:
        """Test clearing the cache."""
        self.chain_cache.set("run-1", "digest-1", True)
        self.assertEqual(self.chain_cache.size(), 1)
        self.chain_cache.clear()
        self.assertEqual(self.chain_cache.size(), 0)

    def test_concurrent_access(self) -> None:
        """Test thread-safety under concurrent get/set calls."""
        errors: list[Exception] = []

        def worker(w_id: int) -> None:
            try:
                for i in range(100):
                    self.chain_cache.set(f"run-{w_id}-{i}", f"digest-{i}", True)
                    val = self.chain_cache.get(f"run-{w_id}-{i}", f"digest-{i}")
                    if val is not None and val is not True:
                        errors.append(ValueError("Unexpected cached value"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
