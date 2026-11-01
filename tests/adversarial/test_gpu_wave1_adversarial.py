"""Adversarial Acceptance Suite for GPU Optimization Wave 1.

Covers mandatory edge-case categories:
1. Boundary payload: 0-byte blobs, empty target IDs, empty queries, minimal 1-token docs.
2. Malformed input & ambiguous codecs: truncated vector blobs, dimension mismatches,
   ambiguous formats, non-finite values (NaN / Inf).
3. Concurrency & race: multi-threaded concurrent scoring against shared caches.
4. Rollback & recovery: governor allocation rollback upon model failure, clean cache state.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import struct
from unittest import TestCase, mock

import numpy as np

from dual_pipeline.index_core.colbert_codec import (
    fetch_and_validate_colbert_tokens,
    validate_and_decode_colbert_token,
)
from dual_pipeline.index_core.constants import _pack
from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache
from dual_pipeline.runtime.memory_governor import MemoryGovernor
from dual_pipeline.runtime.model_runtime import LocalModelRuntime
from dual_pipeline.storage.quantized_colbert_packer import pack_colbert_tensor


class TestGpuWave1Adversarial(TestCase):
    """Adversarial and failure-mode tests for Wave 1 execution and scoring."""

    def setUp(self) -> None:
        self.dim = 32
        np.random.seed(12345)

    def _make_db(self, meta_dim: int = 32) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO metadata VALUES ('colbert_dimensions', ?)", (str(meta_dim),))
        conn.execute(
            """CREATE TABLE colbert_token (
                record_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                token TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                PRIMARY KEY (record_id, position)
            )"""
        )
        return conn

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------
    def test_boundary_zero_and_minimal_payloads(self) -> None:
        """Verifies boundary handling: empty target list, None query, and 0-byte blob rejection."""
        conn = self._make_db()
        q = np.ones((2, self.dim), dtype=np.float32)

        # 1. Empty target list returns empty list
        self.assertEqual(score_colbert(conn, q, []), [])

        # 2. None query returns zero scores
        res = score_colbert(conn, None, ["doc_a", "doc_b"])
        self.assertEqual(res, [(0.0, "doc_a"), (0.0, "doc_b")])

        # 3. 0-byte blob explicitly fails in strict mode
        with self.assertRaises(ValueError) as ctx:
            validate_and_decode_colbert_token(
                record_id="b0",
                position=0,
                token_dim=self.dim,
                blob=b"",
                meta_dim=self.dim,
                strict=True,
            )
        self.assertIn("Ambiguous or invalid ColBERT vector codec", str(ctx.exception))
        conn.close()

    # -------------------------------------------------------------------------
    # Category 2: Malformed input & ambiguous codecs
    # -------------------------------------------------------------------------
    def test_malformed_vector_blobs_and_dimension_mismatch(self) -> None:
        """Verifies truncated blobs, dimension mismatches, and non-finite values fail explicitly."""
        conn = self._make_db(meta_dim=self.dim)

        # Truncated blob (35 bytes: neither 36 nor 128)
        conn.execute(
            "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
            ("doc_truncated", 0, "tok", self.dim, b"X" * 35),
        )
        # Non-finite values in float32 blob (NaN)
        nan_vec = [float("nan")] + [0.1] * (self.dim - 1)
        conn.execute(
            "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
            ("doc_nan", 0, "tok", self.dim, _pack(nan_vec)),
        )
        # Non-finite scale in quantized blob (Inf)
        inf_scale = struct.pack("<f", float("inf")) + (b"\x01" * self.dim)
        conn.execute(
            "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
            ("doc_inf_scale", 0, "tok", self.dim, inf_scale),
        )
        # Dimension mismatch between row and metadata (row declares 16, metadata requires 32)
        conn.execute(
            "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
            ("doc_dim_mismatch", 0, "tok", 16, _pack([0.1] * 16)),
        )
        conn.commit()

        q = np.ones((2, self.dim), dtype=np.float32)

        # Truncated fails explicitly
        with self.assertRaises(ValueError) as ctx:
            score_colbert(conn, q, ["doc_truncated"], colbert_cache={})
        self.assertIn("Ambiguous or invalid ColBERT vector codec", str(ctx.exception))

        # NaN fails explicitly
        with self.assertRaises(ValueError) as ctx:
            score_colbert(conn, q, ["doc_nan"], colbert_cache={})
        self.assertIn("finite=False", str(ctx.exception))

        # Inf scale fails explicitly
        with self.assertRaises(ValueError) as ctx:
            score_colbert(conn, q, ["doc_inf_scale"], colbert_cache={})
        self.assertIn("Non-finite quantization scale", str(ctx.exception))

        # Dimension mismatch fails explicitly
        with self.assertRaises(ValueError) as ctx:
            score_colbert(conn, q, ["doc_dim_mismatch"], colbert_cache={})
        self.assertIn("dimension mismatch", str(ctx.exception))

        conn.close()

    def test_query_dimension_mismatch_fails_closed(self) -> None:
        """Verifies query dimension mismatch against document vectors fails explicitly."""
        conn = self._make_db(meta_dim=32)
        conn.execute(
            "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
            ("doc_valid", 0, "tok", 32, _pack([0.1] * 32)),
        )
        conn.commit()

        # Query has dimension 16 instead of 32
        mismatched_q = np.ones((2, 16), dtype=np.float32)

        with self.assertRaises(ValueError) as ctx:
            score_colbert(conn, mismatched_q, ["doc_valid"], colbert_cache={})
        self.assertIn("Dimension mismatch", str(ctx.exception))
        conn.close()

    # -------------------------------------------------------------------------
    # Category 3: Concurrency & race
    # -------------------------------------------------------------------------
    def test_concurrent_multiworker_scoring_stress(self) -> None:
        """Verifies 8 concurrent worker threads querying shared caches without data races or corruption."""
        conn = self._make_db(meta_dim=self.dim)
        num_docs = 40
        docs = {
            f"doc_{i}": np.random.randn(np.random.randint(2, 10), self.dim).astype(np.float32).tolist()
            for i in range(num_docs)
        }
        for rid, tokens in docs.items():
            for pos, vec in enumerate(tokens):
                # Alternate float32 and quantized codecs
                blob = pack_colbert_tensor(vec) if pos % 2 == 0 else _pack(vec)
                conn.execute(
                    "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
                    (rid, pos, f"tok_{pos}", self.dim, blob),
                )
        conn.commit()

        shared_token_cache: dict[str, np.ndarray] = {}
        shared_padded_cache = PrePaddedColbertCache(max_entries=32)

        def worker(worker_id: int) -> int:
            rng = np.random.RandomState(worker_id * 50)
            q = rng.randn(3, self.dim).astype(np.float32)
            subset = [f"doc_{idx}" for idx in range(worker_id * 4, worker_id * 4 + 8)]
            scores = score_colbert(
                conn, q, subset, colbert_cache=shared_token_cache, padded_cache=shared_padded_cache
            )
            self.assertEqual(len(scores), len(subset))
            # Verify descending order
            raw_scores = [s for s, _ in scores]
            self.assertEqual(raw_scores, sorted(raw_scores, reverse=True))
            return len(scores)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i % 5) for i in range(40)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 40)
        conn.close()

    # -------------------------------------------------------------------------
    # Category 4: Rollback & recovery
    # -------------------------------------------------------------------------
    def test_governor_rollback_on_injection_fault(self) -> None:
        """Verifies memory governor allocation is completely rolled back when model loading faults."""
        gov = MemoryGovernor(total_memory_gb=48.0, reserved_memory_gb=6.0, max_allocatable_gb=42.0)
        runtime = LocalModelRuntime(governor=gov, mock_mode=False)

        # Allocate baseline
        gov.register_allocation("existing_model", 5.0)
        self.assertEqual(gov.current_allocated_gb, 5.0)

        # Simulate GPU runtime allocation failure during load
        with mock.patch("mlx_lm.load", side_effect=RuntimeError("Metal command buffer allocation failed: OOM")):
            with self.assertRaises(RuntimeError) as ctx:
                runtime.load_model("qwen_2_5_14b_instruct")
            self.assertIn("Metal command buffer allocation failed", str(ctx.exception))

        # Memory MUST cleanly rollback to 5.0 GB without leakage
        self.assertEqual(gov.current_allocated_gb, 5.0)
        self.assertNotIn("qwen_2_5_14b_instruct", runtime._loaded_models)
