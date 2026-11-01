#!/usr/bin/env python3
"""Unit tests for dense-only BGE-M3 encoding optimization (Wave 2)."""

from __future__ import annotations

import math
import os
import unittest
from typing import Any

from dual_pipeline.index_core.backends import EmbeddingBackend
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS
from tests.embedding_fixture import BgeM3TestBackend


class TestDenseOnlyEncodingUnit(unittest.TestCase):
    """Verifies dense-only embedding extraction contracts, parity, and batching."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("NEE_RUN_BGE_TESTS") != "1":
            raise unittest.SkipTest("real-BGE tests disabled; set NEE_RUN_BGE_TESTS=1 explicitly")
        cls.backend = BgeM3TestBackend()

    def test_encode_dense_only_dimensions_and_finiteness(self) -> None:
        text = "What are the SLA targets for the query pipeline?"
        dense = self.backend.encode_dense_only(text)
        self.assertIsInstance(dense, list)
        self.assertEqual(len(dense), DENSE_DIMENSIONS)
        self.assertEqual(len(dense), 1024)
        for val in dense:
            self.assertIsInstance(val, float)
            self.assertTrue(math.isfinite(val))
        self.assertTrue(any(abs(v) > 0 for v in dense))

    def test_encode_dense_only_parity_with_full_encode(self) -> None:
        text = "Hybrid retrieval with dense, sparse, and ColBERT late interaction."
        full_res = self.backend.encode(text)
        dense_only = self.backend.encode_dense_only(text)

        full_dense = full_res["dense"]
        self.assertEqual(len(full_dense), len(dense_only))
        max_diff = max(abs(a - b) for a, b in zip(full_dense, dense_only, strict=True))
        self.assertLess(max_diff, 1e-5, f"Dense parity drift exceeded tolerance: {max_diff}")

    def test_encode_many_dense_only_batching(self) -> None:
        texts = [
            "Query optimization wave 2",
            "Dense-only encoding for semantic cache lookup",
            "Eliminating unnecessary quad-vector overhead",
        ]
        batch_dense = self.backend.encode_many_dense_only(texts)
        self.assertEqual(len(batch_dense), len(texts))
        for item in batch_dense:
            self.assertEqual(len(item), DENSE_DIMENSIONS)
            self.assertTrue(all(math.isfinite(v) for v in item))

        for text, dense in zip(texts, batch_dense, strict=True):
            single_dense = self.backend.encode_dense_only(text)
            max_diff = max(abs(a - b) for a, b in zip(dense, single_dense, strict=True))
            self.assertLess(max_diff, 1e-3)

    def test_encode_many_dense_only_empty(self) -> None:
        res = self.backend.encode_many_dense_only([])
        self.assertEqual(res, [])

    def test_protocol_conformance(self) -> None:
        class MinimalBackend:
            backend_id = "test-stub"
            model_id = "BAAI/bge-m3"
            model_revision = "0" * 40
            production_qualified = False
            assurance_tier = "development"
            model_manifest_digest = None
            colbert_dimensions = 1024
            retrieval_encoder = "test"

            def encode(self, text: str) -> dict[str, Any]:
                return {"dense": [0.1] * 1024, "sparse": {}, "colbert": []}

            def encode_dense_only(self, text: str) -> list[float]:
                return [0.1] * 1024

            def encode_many_dense_only(
                self, texts: Any, *, batch_size: int | None = None
            ) -> list[list[float]]:
                return [[0.1] * 1024 for _ in texts]

        stub = MinimalBackend()
        self.assertIsInstance(stub, EmbeddingBackend)


if __name__ == "__main__":
    unittest.main()
