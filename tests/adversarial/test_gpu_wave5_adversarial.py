"""Adversarial and edge-case acceptance test suite for GPU Optimization Wave 5.

Covers mandatory edge-case categories:
1. Boundary payload: 0 candidates, 1 candidate, exact tile size, tile_size + 1, empty query, all empty docs.
2. Malformed input: Non-finite query tokens, dimension mismatch across tiles, oversize cache items.
3. Concurrency & race: Multi-threaded PrePaddedColbertCache eviction stress, concurrent tiled scoring.
4. Rollback & recovery: Mid-scoring GPU device failure / OOM fallback, generation invalidation.
5. Exact production regression: Deterministic tie-breaking preservation across CPU and GPU paths.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import numpy as np
import pytest

from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.index_core.gpu_scoring import (
    DEFAULT_COLBERT_TILE_SIZE,
    GpuColbertScoringAdapter,
    score_colbert_tiled,
)
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache


def _make_in_memory_db(docs: dict[str, list[list[float]]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE colbert_token (
            record_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            token TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL
        )"""
    )
    for rid, mat in docs.items():
        for pos, vec in enumerate(mat):
            blob = np.array(vec, dtype=np.float32).tobytes()
            conn.execute(
                "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
                (rid, pos, f"tok_{pos}", len(vec), blob),
            )
    conn.commit()
    return conn


class TestGpuWave5Adversarial:
    """Wave 5 Adversarial and Edge-Case Test Suite."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------

    def test_boundary_zero_candidate_records(self) -> None:
        """Verifies that an empty candidate list returns empty list immediately."""
        conn = sqlite3.connect(":memory:")
        res = score_colbert(conn, [[0.1, 0.2]], [], strict=True)
        assert res == []
        conn.close()

    def test_boundary_single_candidate_record(self) -> None:
        """Verifies that a single candidate record scores correctly."""
        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        doc = np.random.randn(8, dim).astype(np.float32)
        conn = _make_in_memory_db({"doc_single": doc.tolist()})

        scores = score_colbert(conn, q, ["doc_single"], strict=True)
        assert len(scores) == 1
        expected = float(np.sum(np.max(np.matmul(q, doc.T), axis=1)))
        assert np.isclose(scores[0][0], expected, atol=1e-4)
        assert scores[0][1] == "doc_single"
        conn.close()

    def test_boundary_exact_tile_size_and_plus_one(self) -> None:
        """Verifies boundary handling when candidate count equals tile_size and tile_size + 1."""
        dim = 16
        tile_size = 16
        q = np.random.randn(4, dim).astype(np.float32)

        # 16 candidates: exactly 1 tile
        docs_16 = {f"d_{i:02d}": np.random.randn(5, dim).astype(np.float32) for i in range(16)}
        conn_16 = _make_in_memory_db({k: v.tolist() for k, v in docs_16.items()})
        scores_16 = score_colbert(conn_16, q, list(docs_16.keys()), tile_size=tile_size, strict=True)
        assert len(scores_16) == 16
        conn_16.close()

        # 17 candidates: exactly 2 tiles (16 + 1)
        docs_17 = {f"d_{i:02d}": np.random.randn(5, dim).astype(np.float32) for i in range(17)}
        conn_17 = _make_in_memory_db({k: v.tolist() for k, v in docs_17.items()})
        scores_17 = score_colbert(conn_17, q, list(docs_17.keys()), tile_size=tile_size, strict=True)
        assert len(scores_17) == 17
        conn_17.close()

    def test_boundary_zero_query_tokens_and_all_empty_docs(self) -> None:
        """Verifies that empty query matrix or empty docs produce zero scores gracefully."""
        dim = 16
        conn = _make_in_memory_db({"d1": [], "d2": []})
        scores = score_colbert(conn, np.empty((0, dim)), ["d1", "d2"], strict=True)
        assert len(scores) == 2
        assert scores == [(0.0, "d1"), (0.0, "d2")]
        conn.close()

    # -------------------------------------------------------------------------
    # Category 2: Malformed input
    # -------------------------------------------------------------------------

    def test_malformed_non_finite_query_matrix_rejection(self) -> None:
        """Verifies that non-finite values in query matrix fail closed with ValueError."""
        conn = sqlite3.connect(":memory:")
        q_nan = np.array([[1.0, np.nan], [0.5, 0.5]], dtype=np.float32)
        with pytest.raises(ValueError, match="Non-finite values detected in query ColBERT matrix"):
            score_colbert(conn, q_nan, ["d1"], strict=True)
        conn.close()

    def test_malformed_dimension_mismatch_across_tiles(self) -> None:
        """Verifies that dimension mismatch in a later tile fails closed."""
        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        docs = {
            "d_01": np.random.randn(5, dim).astype(np.float32),
            "d_02": np.random.randn(5, dim).astype(np.float32),
            "d_03": np.random.randn(5, 32).astype(np.float32),  # Mismatched dimension 32 != 16
            "d_04": np.random.randn(5, dim).astype(np.float32),
        }
        token_cache = docs
        with pytest.raises(ValueError, match="Dimension mismatch"):
            score_colbert_tiled(
                q, list(docs.keys()), token_cache, None, strict=True, tile_size=2
            )

    def test_malformed_huge_single_item_exceeds_cache_byte_budget(self) -> None:
        """Verifies that an item singly exceeding max_bytes is safely not cached without error."""
        # 10 records, 50 tokens, 128 dims = 10 * 50 * 128 * 4 = 256,000 bytes
        dim = 128
        stack = np.ones((10, 50, dim), dtype=np.float32)
        lengths = np.full((10,), 50, dtype=np.int32)
        item_bytes = PrePaddedColbertCache._calculate_bytes(stack, lengths, None)

        # Set max_bytes smaller than this single item
        cache = PrePaddedColbertCache(max_entries=10, max_bytes=item_bytes - 1000)
        cache.set(("r_big",), stack, lengths, None)

        assert cache.get(("r_big",)) is None
        assert cache.current_bytes == 0
        assert cache.stats()["cached_blocks"] == 0

    # -------------------------------------------------------------------------
    # Category 3: Concurrency & race
    # -------------------------------------------------------------------------

    def test_concurrency_multithreaded_cache_eviction_stress(self) -> None:
        """Stress-tests PrePaddedColbertCache under high-concurrency multi-threaded writes/evictions."""
        dim = 16
        stack = np.ones((2, 5, dim), dtype=np.float32)
        lengths = np.array([5, 5], dtype=np.int32)
        single_bytes = PrePaddedColbertCache._calculate_bytes(stack, lengths, None)
        # Capacity for ~5 items
        cache = PrePaddedColbertCache(max_entries=5, max_bytes=single_bytes * 5)

        def worker(worker_id: int) -> int:
            hits = 0
            for i in range(25):
                key = (f"w_{worker_id}_k_{i % 8}",)
                cache.set(key, stack, lengths, None)
                if cache.get(key) is not None:
                    hits += 1
            return hits

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, wid) for wid in range(8)]
            results = [f.result() for f in futures]

        assert all(r > 0 for r in results)
        assert cache.current_bytes <= cache.max_bytes
        assert cache.stats()["cached_blocks"] <= 5
        assert cache.evictions_count > 0

    def test_concurrency_multithreaded_tiled_scoring(self) -> None:
        """Verifies thread safety of tiled scoring across concurrent threads."""
        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        docs = {f"doc_{i:02d}": np.random.randn(6, dim).astype(np.float32) for i in range(24)}
        target_ids = list(docs.keys())
        token_cache = docs
        padded_cache = PrePaddedColbertCache(max_entries=50)

        def score_worker() -> list[tuple[float, str]]:
            return score_colbert_tiled(
                q, target_ids, token_cache, padded_cache, strict=True, tile_size=8
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(score_worker) for _ in range(6)]
            results = [f.result() for f in futures]

        # All concurrent runs must produce identical results
        first_res = results[0]
        for other_res in results[1:]:
            assert len(other_res) == len(first_res)
            for (s1, r1), (s2, r2) in zip(first_res, other_res):
                assert r1 == r2
                assert np.isclose(s1, s2, atol=1e-5)

    # -------------------------------------------------------------------------
    # Category 4: Rollback & recovery
    # -------------------------------------------------------------------------

    def test_rollback_gpu_device_error_clean_cpu_fallback(self) -> None:
        """Verifies that an unhandled GPU runtime exception cleanly rolls back to CPU."""
        adapter = GpuColbertScoringAdapter()

        # Monkey-patch score_tile_gpu to simulate sudden device out-of-memory or crash
        def failing_gpu(*args, **kwargs):
            raise RuntimeError("MPS out of device memory during tensor allocation")

        adapter.score_tile_gpu = failing_gpu

        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        docs = {f"doc_{i}": np.random.randn(5, dim).astype(np.float32) for i in range(8)}
        conn = _make_in_memory_db({k: v.tolist() for k, v in docs.items()})

        # Must NOT raise exception; must log warning and fall back to CPU
        scores = score_colbert(
            conn, q, list(docs.keys()), gpu_adapter=adapter, strict=True
        )
        assert len(scores) == 8
        for s, rid in scores:
            assert np.isfinite(s)
            assert s > 0.0

        conn.close()

    def test_rollback_generation_invalidation_releases_memory(self) -> None:
        """Verifies generation invalidation safely purges resident tensors and clears cache."""
        adapter = GpuColbertScoringAdapter()
        adapter.bind_generation("gen_abandoned", {"r1": np.ones((5, 16), dtype=np.float32)})
        assert adapter._current_generation_id == "gen_abandoned"

        # Simulating abnormal termination / rollback
        adapter.invalidate_generation("gen_abandoned")
        assert adapter._current_generation_id is None
        assert len(adapter._resident_tensors) == 0

    # -------------------------------------------------------------------------
    # Category 5: Exact production regression
    # -------------------------------------------------------------------------

    def test_deterministic_tie_breaking_preserves_rankings(self) -> None:
        """Verifies that identical scores are deterministically broken by record_id alphabetically."""
        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        identical_doc = np.random.randn(5, dim).astype(np.float32)

        # Three documents with identical vectors
        docs = {
            "zeta_doc": identical_doc,
            "alpha_doc": identical_doc,
            "gamma_doc": identical_doc,
        }
        conn = _make_in_memory_db({k: v.tolist() for k, v in docs.items()})

        scores = score_colbert(conn, q, ["zeta_doc", "alpha_doc", "gamma_doc"], strict=True)
        assert len(scores) == 3

        # Scores should be identical
        assert np.isclose(scores[0][0], scores[1][0], atol=1e-5)
        assert np.isclose(scores[1][0], scores[2][0], atol=1e-5)

        # Tie breaking must sort alphabetically: alpha_doc, gamma_doc, zeta_doc
        assert scores[0][1] == "alpha_doc"
        assert scores[1][1] == "gamma_doc"
        assert scores[2][1] == "zeta_doc"

        conn.close()
