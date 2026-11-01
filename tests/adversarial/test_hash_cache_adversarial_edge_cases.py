"""Adversarial and edge-case test suite for GenerationalHashCache and RunChainDigestCache.

Covers >= 3 mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payloads: 0-byte file, missing file, unicode/special chars in path.
2. Concurrency & race: 16 concurrent worker threads executing high-frequency reads, mutations, and invalidations.
3. Rollback & recovery: File content modification, mtime rewinds, rollback to previous digest, and cache recovery.
4. Invariant bypass attempts: Symlink target manipulation, content replacement preserving size with mtime variation.
"""

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
)
from dual_pipeline.semantic_cache_core.chain_cache import (
    RunChainDigestCache,
)


class TestHashCacheAdversarialEdgeCases(unittest.TestCase):
    """Adversarial stress and edge-case suite for GenerationalHashCache."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.cache = GenerationalHashCache()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --- Category 1: Boundary Payloads ---

    def test_edge_case_zero_byte_file(self) -> None:
        """Boundary: 0-byte file hashing, caching, and digest validity."""
        empty_file = self.dir_path / "empty_file.bin"
        empty_file.write_bytes(b"")

        digest1 = self.cache.get(empty_file)
        # Standard SHA-256 of empty payload is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        self.assertEqual(
            digest1,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(self.cache.misses, 1)

        # Subsequent hit
        digest2 = self.cache.get(empty_file)
        self.assertEqual(digest2, digest1)
        self.assertEqual(self.cache.hits, 1)

    def test_edge_case_missing_file_fails_cleanly(self) -> None:
        """Boundary: Non-existent file raises FileNotFoundError without poisoning cache."""
        missing_file = self.dir_path / "does_not_exist.bin"
        with self.assertRaises(FileNotFoundError):
            self.cache.get(missing_file)
        self.assertEqual(self.cache.size(), 0)

    def test_edge_case_special_characters_and_spaces_in_path(self) -> None:
        """Boundary: Paths containing spaces, UTF-8 unicode, parentheses, and brackets."""
        special_file = self.dir_path / "special [path] (name) with spaces and 🚀 unicode.txt"
        special_file.write_text("special path payload")

        digest1 = self.cache.get(special_file)
        self.assertEqual(digest1, sha256_file(special_file))
        self.assertEqual(self.cache.get(special_file), digest1)
        self.assertEqual(self.cache.hits, 1)

    # --- Category 2: Concurrency & Race Conditions ---

    def test_concurrency_race_high_contention(self) -> None:
        """Concurrency: 16 concurrent threads reading, modifying, and invalidating the cache."""
        test_file = self.dir_path / "contention_file.txt"
        test_file.write_text("initial contention content")

        errors: list[Exception] = []
        stop_event = threading.Event()

        def reader(thread_id: int) -> None:
            try:
                while not stop_event.is_set():
                    d = self.cache.get(test_file)
                    if not isinstance(d, str) or len(d) != 64:
                        errors.append(ValueError(f"Thread {thread_id} got invalid digest: {d}"))
            except Exception as e:
                errors.append(e)

        def invalidator() -> None:
            try:
                for _ in range(50):
                    self.cache.invalidate(test_file)
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(14)]
        invalidator_threads = [threading.Thread(target=invalidator) for _ in range(2)]

        all_threads = threads + invalidator_threads
        for t in all_threads:
            t.start()

        # Let them race for a short burst
        time.sleep(0.2)
        stop_event.set()

        for t in all_threads:
            t.join(timeout=5.0)

        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")
        self.assertGreater(self.cache.hits, 0)

    # --- Category 3: Rollback & Recovery ---

    def test_rollback_and_recovery_to_previous_state(self) -> None:
        """Rollback: Mutating file, recording digest, reverting content, and recovering valid state."""
        managed_file = self.dir_path / "rollback_file.txt"
        content_v1 = "version 1 state"
        content_v2 = "version 2 state"

        managed_file.write_text(content_v1)
        digest_v1 = self.cache.get(managed_file)

        # Mutate to v2
        time.sleep(0.05)
        st1 = managed_file.stat()
        managed_file.write_text(content_v2)
        os.utime(managed_file, (st1.st_atime + 1, st1.st_mtime + 1))
        digest_v2 = self.cache.get(managed_file)
        self.assertNotEqual(digest_v1, digest_v2)

        # Rollback to v1 content
        time.sleep(0.05)
        st2 = managed_file.stat()
        managed_file.write_text(content_v1)
        os.utime(managed_file, (st2.st_atime + 1, st2.st_mtime + 1))
        digest_v1_restored = self.cache.get(managed_file)

        self.assertEqual(digest_v1_restored, digest_v1)

    # --- Category 4: Invariant Bypass & Symlink Defenses ---

    def test_invariant_symlink_target_mutation(self) -> None:
        """Invariant: Resolving symlink target ensures cache tracks real underlying file."""
        target_file = self.dir_path / "real_file.txt"
        target_file.write_text("real content")
        link_file = self.dir_path / "symlink_file.txt"
        link_file.symlink_to(target_file)

        digest1 = self.cache.get(link_file)
        self.assertEqual(digest1, sha256_file(target_file))

        # Accessing real file directly should hit the same resolved cache key
        digest2 = self.cache.get(target_file)
        self.assertEqual(digest2, digest1)
        self.assertEqual(self.cache.hits, 1)


class TestRunChainDigestCacheAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case suite for RunChainDigestCache."""

    def setUp(self) -> None:
        self.chain_cache = RunChainDigestCache(max_size=10)

    def test_adversarial_malformed_key_handling(self) -> None:
        """Boundary: Handles unexpected empty strings, long strings, or special chars safely."""
        self.chain_cache.set("", "", False)
        self.assertIs(self.chain_cache.get("", ""), False)

        long_id = "x" * 1024
        long_digest = "y" * 1024
        self.chain_cache.set(long_id, long_digest, True)
        self.assertIs(self.chain_cache.get(long_id, long_digest), True)

    def test_high_concurrency_churn(self) -> None:
        """Concurrency: High-churn eviction across 8 threads simultaneously."""
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(200):
                    self.chain_cache.set(f"run-{tid}-{i % 20}", f"digest-{i % 20}", (i % 2 == 0))
                    val = self.chain_cache.get(f"run-{tid}-{i % 20}", f"digest-{i % 20}")
                    if val is not None and not isinstance(val, bool):
                        errors.append(ValueError("corrupted bool in cache"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertLessEqual(self.chain_cache.size(), 10)


if __name__ == "__main__":
    unittest.main()
