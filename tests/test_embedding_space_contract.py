"""Unit tests for embedding space contract, dimension checks, and cache key bindings."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

from dual_pipeline.index_core.embedding_space_contract import (
    EmbeddingSpaceMismatch,
    assert_query_vector_contract,
    build_query_cache_key,
    validate_embedding_space_contract,
)


class TestEmbeddingSpaceContract(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = SimpleNamespace(
            backend_id="bge-m3",
            model_id="BAAI/bge-m3",
            model_revision="5617a9f61b028005a4858fdac845db406aefb181",
            model_manifest_digest="a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e",
        )
        self.sidecar_meta = {
            "embedding_backend": "bge-m3",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "model_manifest_digest": "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e",
            "dense_dimensions": 1024,
        }

    def test_query_vector_contract_valid(self) -> None:
        rng = np.random.RandomState(42)
        v = rng.randn(1024).astype(np.float32)
        v /= np.linalg.norm(v)
        res = assert_query_vector_contract(v)
        self.assertEqual(res.shape, (1024,))
        self.assertAlmostEqual(float(np.linalg.norm(res)), 1.0, places=4)

    def test_query_vector_contract_dimension_mismatch(self) -> None:
        # 64-d vector
        v_64 = np.ones(64, dtype=np.float32) / np.sqrt(64)
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            assert_query_vector_contract(v_64)
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("64", str(ctx.exception))

    def test_query_vector_contract_nan_or_inf(self) -> None:
        v_nan = np.ones(1024, dtype=np.float32)
        v_nan[10] = np.nan
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            assert_query_vector_contract(v_nan)
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("NaN", str(ctx.exception))

        v_inf = np.ones(1024, dtype=np.float32)
        v_inf[5] = np.inf
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            assert_query_vector_contract(v_inf)
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))

    def test_query_vector_contract_zero_norm(self) -> None:
        v_zero = np.zeros(1024, dtype=np.float32)
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            assert_query_vector_contract(v_zero)
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("zero", str(ctx.exception).lower())

    def test_query_vector_contract_unnormalized(self) -> None:
        v_unnorm = np.ones(1024, dtype=np.float32) * 2.0  # norm = 64
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            assert_query_vector_contract(v_unnorm)
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("normalization", str(ctx.exception).lower())

    def test_validate_embedding_space_contract_pass(self) -> None:
        rng = np.random.RandomState(42)
        v = rng.randn(1024).astype(np.float32)
        v /= np.linalg.norm(v)
        validate_embedding_space_contract(
            backend=self.backend,
            sidecar_metadata=self.sidecar_meta,
            query_vector=v,
        )

    def test_validate_embedding_space_contract_model_mismatch(self) -> None:
        bad_backend = SimpleNamespace(
            backend_id="bge-m3",
            model_id="other-model",
            model_revision="5617a9f61b028005a4858fdac845db406aefb181",
        )
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            validate_embedding_space_contract(
                backend=bad_backend,
                sidecar_metadata=self.sidecar_meta,
            )
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("model_id mismatch", str(ctx.exception))

    def test_validate_embedding_space_contract_revision_mismatch(self) -> None:
        bad_backend = SimpleNamespace(
            backend_id="bge-m3",
            model_id="BAAI/bge-m3",
            model_revision="outdated-revision-hash",
        )
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            validate_embedding_space_contract(
                backend=bad_backend,
                sidecar_metadata=self.sidecar_meta,
            )
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("model_revision mismatch", str(ctx.exception))

    def test_validate_embedding_space_contract_backend_mismatch(self) -> None:
        bad_backend = SimpleNamespace(
            backend_id="openai-embed",
            model_id="BAAI/bge-m3",
            model_revision="5617a9f61b028005a4858fdac845db406aefb181",
        )
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            validate_embedding_space_contract(
                backend=bad_backend,
                sidecar_metadata=self.sidecar_meta,
            )
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("backend mismatch", str(ctx.exception))

    def test_build_query_cache_key_revision_binding(self) -> None:
        key1 = build_query_cache_key(self.backend, "What was decided about Phase 1?")
        backend_rev2 = SimpleNamespace(
            backend_id="bge-m3",
            model_id="BAAI/bge-m3",
            model_revision="updated-rev-9999",
            model_manifest_digest="a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e",
        )
        key2 = build_query_cache_key(backend_rev2, "What was decided about Phase 1?")
        self.assertNotEqual(key1, key2, "Cache keys must differ when model revision changes!")


if __name__ == "__main__":
    unittest.main()
