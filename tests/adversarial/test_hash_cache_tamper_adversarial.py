"""Adversarial tests for persistent BGE-M3 hash verdict cache and tamper resistance.

Verifies:
1. Valid verdicts persist across separate cache instances (simulating separate CLI processes).
2. Tampering with verdict bytes, metadata, or signatures is detected and rejected via HMAC.
3. Corrupted or truncated cache files fall back safely to clean disk hashing without crashing.
4. Manifest digest changes invalidate cached entries properly.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hash_cache import GenerationalHashCache


class TestHashCacheTamperAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.cache_file = self.root / "cache" / "verdicts.json"
        self.key_file = self.root / "cache" / ".key"

        # Create a sample model file
        self.model_file = self.root / "model_weights.bin"
        self.model_file.write_bytes(
            b"Simulated heavy model weight bytes for testing 1234567890" * 100
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_cross_process_persistent_cache_hit(self) -> None:
        """Process 1 computes and saves verdict; Process 2 loads and gets cache hit."""
        cache1 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        digest1 = cache1.get(self.model_file, manifest_digest="manifest_v1")

        self.assertEqual(cache1.misses, 1)
        self.assertEqual(cache1.hits, 0)
        self.assertTrue(self.cache_file.is_file())

        # Second process with fresh cache instance
        cache2 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        self.assertFalse(cache2.tamper_detected)
        self.assertEqual(cache2.size(), 1)

        digest2 = cache2.get(self.model_file, manifest_digest="manifest_v1")
        self.assertEqual(digest1, digest2)
        self.assertEqual(cache2.hits, 1)
        self.assertEqual(cache2.misses, 0)

    def test_adversarial_tamper_sha256_detection(self) -> None:
        """Adversary modifies cached sha256 in file; cache detects tamper and rejects entry."""
        cache1 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        real_digest = cache1.get(self.model_file, manifest_digest="manifest_v1")

        # Tamper with the verdict JSON file
        data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        data["verdicts"][0]["sha256"] = (
            "0000000000000000000000000000000000000000000000000000000000000000"
        )
        self.cache_file.write_text(json.dumps(data), encoding="utf-8")

        # Process 2 loads tampered cache
        cache2 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        self.assertTrue(cache2.tamper_detected)
        self.assertEqual(cache2.size(), 0)  # Tampered entry excluded

        # Re-get computes real hash from disk safely
        recomputed = cache2.get(self.model_file, manifest_digest="manifest_v1")
        self.assertEqual(recomputed, real_digest)
        self.assertEqual(cache2.misses, 1)

    def test_adversarial_tamper_signature_detection(self) -> None:
        """Adversary modifies HMAC signature; cache rejects tampered entry."""
        cache1 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        cache1.get(self.model_file, manifest_digest="manifest_v1")

        data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        data["verdicts"][0]["hmac"] = "deadbeef" * 8
        self.cache_file.write_text(json.dumps(data), encoding="utf-8")

        cache2 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        self.assertTrue(cache2.tamper_detected)
        self.assertEqual(cache2.size(), 0)

    def test_adversarial_tamper_metadata_detection(self) -> None:
        """Adversary modifies size_bytes in cache file; HMAC mismatch rejects entry."""
        cache1 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        cache1.get(self.model_file, manifest_digest="manifest_v1")

        data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        data["verdicts"][0]["size_bytes"] = 9999999
        self.cache_file.write_text(json.dumps(data), encoding="utf-8")

        cache2 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        self.assertTrue(cache2.tamper_detected)
        self.assertEqual(cache2.size(), 0)

    def test_truncated_cache_file_graceful_recovery(self) -> None:
        """Corrupted/truncated JSON cache file does not crash and recovers safely."""
        cache1 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        real_digest = cache1.get(self.model_file, manifest_digest="manifest_v1")

        # Write truncated/corrupted content
        self.cache_file.write_text(
            '{"schema_version": "1.0.0", "verdicts": [{"path": "', encoding="utf-8"
        )

        cache2 = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        self.assertTrue(cache2.tamper_detected)
        self.assertEqual(cache2.size(), 0)

        # Computes digest without error
        recomputed = cache2.get(self.model_file, manifest_digest="manifest_v1")
        self.assertEqual(recomputed, real_digest)

    def test_manifest_digest_sensitivity(self) -> None:
        """Changing manifest_digest causes cache miss and recomputation under new manifest."""
        cache = GenerationalHashCache(cache_file=self.cache_file, key_file=self.key_file)
        digest_v1 = cache.get(self.model_file, manifest_digest="manifest_v1")
        self.assertEqual(cache.misses, 1)

        # Same file but different manifest digest -> cache miss
        digest_v2 = cache.get(self.model_file, manifest_digest="manifest_v2")
        self.assertEqual(cache.misses, 2)
        self.assertEqual(digest_v1, digest_v2)


if __name__ == "__main__":
    unittest.main()
