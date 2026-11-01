"""Unit tests for QueryEncodingCache."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.retrieval_release_core.query_encoding_cache import QueryEncodingCache


class MockBackend:
    backend_id = "flagembedding-bge-m3"
    model_id = "BAAI/bge-m3"
    model_revision = "5617a9f61b028005a4858fdac845db406aefb181"
    model_manifest_digest = "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e"


class QueryEncodingCacheTests(unittest.TestCase):
    def test_cache_roundtrip_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / ".query_encoding_cache.json"
            cache = QueryEncodingCache(cache_file)
            backend = MockBackend()

            query = "What is the primary power generation asset?"
            encoded = {
                "dense": [0.1, 0.2, 0.3],
                "sparse": {"power": 1.5, "asset": 2.0},
                "colbert_tokens": ["token-0", "token-1"],
                "colbert": [[0.1, 0.2], [0.3, 0.4]],
            }

            self.assertIsNone(cache.get(backend, query))
            cache.set(backend, query, encoded)
            self.assertEqual(cache.get(backend, query), encoded)

            cache.flush()
            self.assertTrue(cache_file.exists())

            # Load new cache instance from disk
            loaded = QueryEncodingCache(cache_file)
            self.assertEqual(loaded.get(backend, query), encoded)

            # Different backend revision misses cache
            class ModifiedBackend(MockBackend):
                model_revision = "0000000000000000000000000000000000000000"

            self.assertIsNone(loaded.get(ModifiedBackend(), query))
