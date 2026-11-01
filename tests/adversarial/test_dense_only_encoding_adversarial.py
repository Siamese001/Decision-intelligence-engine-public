#!/usr/bin/env python3
"""Adversarial and edge-case tests for dense-only BGE-M3 encoding (Wave 2).

Covers:
1. Boundary payloads: empty string, massive text, whitespace, unicode/emojis.
2. Batch size edge cases: batch_size=1, batch_size exceeding input length.
3. Concurrency races: concurrent multi-threaded encode_dense_only calls.
4. Invariant defense: strict validation of 1024-dim finite non-zero vectors.
"""

from __future__ import annotations

import concurrent.futures
import math
import os
import unittest

from dual_pipeline.index_core.constants import DENSE_DIMENSIONS
from tests.embedding_fixture import BgeM3TestBackend


class TestDenseOnlyEncodingAdversarial(unittest.TestCase):
    """Adversarial and boundary stress tests for dense-only encoding."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("NEE_RUN_BGE_TESTS") != "1":
            raise unittest.SkipTest("real-BGE tests disabled; set NEE_RUN_BGE_TESTS=1 explicitly")
        cls.backend = BgeM3TestBackend()

    def test_boundary_empty_and_whitespace_strings(self) -> None:
        """Boundary payload: empty string, spaces, tabs, newlines."""
        payloads = ["", "   ", "\t\t\n\r\n", "   \n   "]
        for p in payloads:
            dense = self.backend.encode_dense_only(p)
            self.assertEqual(len(dense), DENSE_DIMENSIONS)
            self.assertTrue(all(math.isfinite(v) for v in dense))
            self.assertTrue(any(abs(v) > 0 for v in dense))

    def test_boundary_massive_text_and_unicode_emojis(self) -> None:
        """Boundary payload: extreme length (5000+ chars) and high unicode/emojis."""
        massive = "Decision Intelligence Enterprise Architecture " * 200
        dense_massive = self.backend.encode_dense_only(massive)
        self.assertEqual(len(dense_massive), DENSE_DIMENSIONS)
        self.assertTrue(all(math.isfinite(v) for v in dense_massive))

        unicode_str = "🚀 智能决策引擎 🎯 Strategic Alignment & AI Governance 日本語 한국어 äöüß"
        dense_unicode = self.backend.encode_dense_only(unicode_str)
        self.assertEqual(len(dense_unicode), DENSE_DIMENSIONS)
        self.assertTrue(all(math.isfinite(v) for v in dense_unicode))

    def test_batch_size_edge_cases(self) -> None:
        """Boundary: batch_size=1, batch_size=len(texts)*2, uneven batches."""
        texts = [f"Edge case candidate item {i}" for i in range(7)]
        # batch_size=1
        b1 = self.backend.encode_many_dense_only(texts, batch_size=1)
        self.assertEqual(len(b1), 7)
        # batch_size=100 (exceeds len)
        b100 = self.backend.encode_many_dense_only(texts, batch_size=100)
        self.assertEqual(len(b100), 7)

        # Parity between different batch sizes
        for v1, v100 in zip(b1, b100, strict=True):
            max_diff = max(abs(a - b) for a, b in zip(v1, v100, strict=True))
            self.assertLess(max_diff, 1e-3)

    def test_concurrency_race_condition_resilience(self) -> None:
        """Concurrency stress: multiple threads encoding concurrently using the same backend."""
        queries = [
            f"Concurrent stress query pattern {i} for latency reduction"
            for i in range(20)
        ]
        results: list[list[float]] = []

        def _encode_worker(query: str) -> list[float]:
            return self.backend.encode_dense_only(query)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(_encode_worker, q) for q in queries]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        self.assertEqual(len(results), len(queries))
        for dense in results:
            self.assertEqual(len(dense), DENSE_DIMENSIONS)
            self.assertTrue(all(math.isfinite(v) for v in dense))
            self.assertTrue(any(abs(v) > 0 for v in dense))


if __name__ == "__main__":
    unittest.main()
