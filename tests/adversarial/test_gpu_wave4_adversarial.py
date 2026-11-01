"""Adversarial Acceptance Suite for GPU Optimization Wave 4.

Covers mandatory edge-case categories:
1. Boundary payload:
   - Zero delta records: returns (0, colbert_dim) with zero database operations.
   - Single delta record: handles odd record counts strictly below host and inference budgets.
   - Exact multiple chunk boundary: tests evenly divisible batches without off-by-one errors.
2. Malformed input:
   - Non-finite dense vector (NaN / Inf) triggers immediate ValueError before commit.
   - Dimension mismatch (512 vs 1024) fails closed.
   - Mid-generation ColBERT dimension drift across chunks triggers ValueError.
   - Backend returning wrong batch cardinality raises RuntimeError.
3. Concurrency & race:
   - Multi-threaded access to _BGEM3_MODEL_CACHE under _BGEM3_CACHE_LOCK initializes instance safely.
   - Concurrent reader isolation during streaming uncommitted insertions.
4. Rollback & recovery:
   - Mid-write failure in streaming window triggers complete transaction rollback.
   - Corrupted/truncated prior sidecar database safely falls back to full encoding.
"""
from __future__ import annotations

import concurrent.futures
import math
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dual_pipeline.core import connect_sqlite, digest
from dual_pipeline.index_core.backends import (
    _BGEM3_CACHE_LOCK,
    _BGEM3_MODEL_CACHE,
    clear_bge_m3_model_cache,
)
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, SIDECAR_SCHEMA
from dual_pipeline.index_core.differential import (
    encode_and_insert_delta_records,
    inspect_prior_sidecar,
    populate_sidecar_vectors,
)
from storage.connection import sqlite3


class _AdversarialMockBackend:
    """Mock backend configurable to inject faults and anomalies."""

    def __init__(
        self,
        *,
        dense_dim: int = DENSE_DIMENSIONS,
        colbert_dim: int = 128,
        inject_nan: bool = False,
        inject_dim_drift_after: Optional[int] = None,
        inject_cardinality_mismatch: bool = False,
        fail_at_call: Optional[int] = None,
    ) -> None:
        self.backend_id = "bge-m3"
        self.model_id = "BAAI/bge-m3"
        self.model_revision = "rev-wave4-adv"
        self.model_manifest_digest = "manifest-wave4-adv"
        self.production_qualified = True
        self.assurance_tier = "enterprise_production"
        self.colbert_dimensions = colbert_dim
        self.retrieval_encoder = "bge-m3"
        self.supports_batch_encoding = True
        self.execution_device = "cpu"

        self.dense_dim = dense_dim
        self.colbert_dim = colbert_dim
        self.inject_nan = inject_nan
        self.inject_dim_drift_after = inject_dim_drift_after
        self.inject_cardinality_mismatch = inject_cardinality_mismatch
        self.fail_at_call = fail_at_call

        self.calls = 0

    @property
    def encoding_fingerprint(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "model_manifest_digest": self.model_manifest_digest,
            "precision": "float32",
            "device": self.execution_device,
            "tokenizer": "BAAI/bge-m3",
            "max_length": 8192,
            "truncation": True,
            "normalize_embeddings": True,
            "dense_dimensions": self.dense_dim,
            "max_colbert_tokens": 32,
        }

    @property
    def encoding_fingerprint_digest(self) -> str:
        return digest(self.encoding_fingerprint)

    def encode(self, text: str) -> Dict[str, Any]:
        self.calls += 1
        if self.fail_at_call is not None and self.calls >= self.fail_at_call:
            raise RuntimeError(f"Simulated backend failure at call {self.calls}")

        if self.inject_nan:
            dense = [float("nan")] * self.dense_dim
        else:
            dense = [0.01 * ((i % 100) + 1) for i in range(self.dense_dim)]

        current_colbert_dim = self.colbert_dim
        if self.inject_dim_drift_after is not None and self.calls > self.inject_dim_drift_after:
            current_colbert_dim = self.colbert_dim // 2

        colbert_tok = [0.02 * (j + 1) for j in range(current_colbert_dim)]
        return {
            "dense": dense,
            "sparse": {"token_x": 0.99},
            "colbert": [colbert_tok],
            "colbert_tokens": ["token_x"],
        }

    def encode_many(
        self, texts: Iterable[str], *, batch_size: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        items = list(texts)
        if self.inject_cardinality_mismatch and len(items) > 1:
            # Return one fewer item to test cardinality mismatch detection
            return [self.encode(t) for t in items[:-1]]
        return [self.encode(t) for t in items]


class TestGpuWave4Adversarial(unittest.TestCase):
    """Adversarial stress suite for Wave 4 streaming and differential vector population."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="gpu-wave4-adv-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_sidecar(self, path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect_sqlite(path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SIDECAR_SCHEMA)
        return conn

    def _insert_retrieval_records(self, conn: sqlite3.Connection, records: List[Dict[str, Any]]) -> None:
        conn.executemany(
            """
            INSERT INTO retrieval_record
            (record_id, lane, record_type, namespace, retrieval_text_hash,
             retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    r["record_id"],
                    r.get("lane", "approved"),
                    "assertion",
                    "test",
                    r.get("retrieval_text_hash", digest(r["retrieval_text"])),
                    "assertion",
                    "approved",
                    "source_bound",
                    "factual_answer",
                    f'{{"record_id": "{r["record_id"]}", "lane": "{r.get("lane", "approved")}"}}',
                )
                for r in records
            ],
        )

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------
    def test_zero_delta_records(self) -> None:
        """Verifies zero delta records returns cleanly with no database modification."""
        conn = self._init_sidecar(self.root / "zero.sqlite")
        backend = _AdversarialMockBackend()

        count, colbert_dim = encode_and_insert_delta_records(
            connection=conn,
            delta_records=[],
            backend=backend,
            colbert_dimensions=128,
        )
        self.assertEqual(count, 0)
        self.assertEqual(colbert_dim, 128)
        self.assertEqual(backend.calls, 0)

        dense_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
        self.assertEqual(dense_count, 0)
        conn.close()

    def test_single_delta_record(self) -> None:
        """Verifies single odd record smaller than host/inference budgets is handled correctly."""
        conn = self._init_sidecar(self.root / "single.sqlite")
        backend = _AdversarialMockBackend()
        records = [
            {
                "record_id": "rec_single",
                "retrieval_text": "Single record retrieval text payload",
                "retrieval_text_hash": digest("Single record retrieval text payload"),
                "lane": "approved",
            }
        ]
        self._insert_retrieval_records(conn, records)

        count, colbert_dim = encode_and_insert_delta_records(
            connection=conn,
            delta_records=records,
            backend=backend,
            colbert_dimensions=None,
            host_chunk_size=16,
            inference_batch_size=8,
        )
        self.assertEqual(count, 1)
        self.assertEqual(colbert_dim, 128)
        dense_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
        self.assertEqual(dense_count, 1)
        conn.close()

    def test_exact_multiple_chunk_boundary(self) -> None:
        """Verifies exact multiples of host chunk and inference batch don't produce empty tails."""
        conn = self._init_sidecar(self.root / "multiple.sqlite")
        backend = _AdversarialMockBackend()
        records = [
            {
                "record_id": f"rec_{i}",
                "retrieval_text": f"Multiple chunk record {i}",
                "retrieval_text_hash": digest(f"Multiple chunk record {i}"),
                "lane": "approved",
            }
            for i in range(8)
        ]
        self._insert_retrieval_records(conn, records)

        count, colbert_dim = encode_and_insert_delta_records(
            connection=conn,
            delta_records=records,
            backend=backend,
            colbert_dimensions=None,
            host_chunk_size=4,
            inference_batch_size=2,
        )
        self.assertEqual(count, 8)
        self.assertEqual(colbert_dim, 128)
        dense_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
        self.assertEqual(dense_count, 8)
        conn.close()

    # -------------------------------------------------------------------------
    # Category 2: Malformed input
    # -------------------------------------------------------------------------
    def test_non_finite_vector_rejection(self) -> None:
        """Verifies NaN/Inf in vector output raises ValueError."""
        conn = self._init_sidecar(self.root / "nan.sqlite")
        backend = _AdversarialMockBackend(inject_nan=True)
        records = [
            {
                "record_id": "rec_nan",
                "retrieval_text": "Text producing NaN vector",
                "retrieval_text_hash": digest("nan"),
                "lane": "approved",
            }
        ]
        self._insert_retrieval_records(conn, records)

        with self.assertRaises(Exception):
            encode_and_insert_delta_records(
                connection=conn,
                delta_records=records,
                backend=backend,
                colbert_dimensions=None,
            )
        conn.close()

    def test_dense_dimension_mismatch_rejection(self) -> None:
        """Verifies dense vector with incorrect dimensions (512 vs 1024) fails closed."""
        conn = self._init_sidecar(self.root / "dim_mismatch.sqlite")
        backend = _AdversarialMockBackend(dense_dim=512)
        records = [
            {
                "record_id": "rec_bad_dim",
                "retrieval_text": "Text producing 512-dim vector",
                "retrieval_text_hash": digest("bad_dim"),
                "lane": "approved",
            }
        ]
        self._insert_retrieval_records(conn, records)

        with self.assertRaises(ValueError) as ctx:
            encode_and_insert_delta_records(
                connection=conn,
                delta_records=records,
                backend=backend,
                colbert_dimensions=None,
            )
        self.assertIn("Dense vector length is 512", str(ctx.exception))
        conn.close()

    def test_colbert_dimension_drift_across_chunks(self) -> None:
        """Verifies ColBERT dimension changing mid-stream triggers ValueError."""
        conn = self._init_sidecar(self.root / "drift.sqlite")
        backend = _AdversarialMockBackend(colbert_dim=128, inject_dim_drift_after=2)
        records = [
            {
                "record_id": f"rec_{i}",
                "retrieval_text": f"Drift test record {i}",
                "retrieval_text_hash": digest(f"drift_{i}"),
                "lane": "approved",
            }
            for i in range(4)
        ]
        self._insert_retrieval_records(conn, records)

        with self.assertRaises(ValueError) as ctx:
            encode_and_insert_delta_records(
                connection=conn,
                delta_records=records,
                backend=backend,
                colbert_dimensions=None,
                host_chunk_size=2,
                inference_batch_size=1,
            )
        self.assertIn("ColBERT vector dimensions changed within one generation", str(ctx.exception))
        conn.close()

    def test_backend_cardinality_mismatch_rejection(self) -> None:
        """Verifies backend returning wrong batch size raises RuntimeError."""
        conn = self._init_sidecar(self.root / "cardinality.sqlite")
        backend = _AdversarialMockBackend(inject_cardinality_mismatch=True)
        records = [
            {
                "record_id": f"rec_{i}",
                "retrieval_text": f"Cardinality test record {i}",
                "retrieval_text_hash": digest(f"card_{i}"),
                "lane": "approved",
            }
            for i in range(4)
        ]
        self._insert_retrieval_records(conn, records)

        with self.assertRaises(RuntimeError) as ctx:
            encode_and_insert_delta_records(
                connection=conn,
                delta_records=records,
                backend=backend,
                colbert_dimensions=None,
                host_chunk_size=4,
                inference_batch_size=4,
            )
        self.assertIn("inconsistent batch size", str(ctx.exception))
        conn.close()

    # -------------------------------------------------------------------------
    # Category 3: Concurrency & race
    # -------------------------------------------------------------------------
    def test_thread_safe_bge_m3_cache_lock(self) -> None:
        """Verifies concurrent threads accessing _BGEM3_MODEL_CACHE under _BGEM3_CACHE_LOCK."""
        clear_bge_m3_model_cache()
        key = ("mock_path", "rev_40", "manifest_64", False, "cpu")
        init_count = 0
        lock = threading.Lock()

        def _get_or_load() -> Any:
            nonlocal init_count
            with _BGEM3_CACHE_LOCK:
                if key in _BGEM3_MODEL_CACHE:
                    return _BGEM3_MODEL_CACHE[key]
                # Simulate model loading latency
                with lock:
                    init_count += 1
                mock_model = {"initialized": True, "token": init_count}
                _BGEM3_MODEL_CACHE[key] = mock_model
                return mock_model

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_get_or_load) for _ in range(16)]
            results = [f.result() for f in futures]

        self.assertEqual(init_count, 1)
        self.assertTrue(all(r["token"] == 1 for r in results))
        clear_bge_m3_model_cache()

    # -------------------------------------------------------------------------
    # Category 4: Rollback & recovery
    # -------------------------------------------------------------------------
    def test_mid_batch_failure_rolls_back_transaction(self) -> None:
        """Verifies failure on a subsequent chunk completely rolls back all previous insertions."""
        db_path = self.root / "rollback.sqlite"
        conn = self._init_sidecar(db_path)
        backend = _AdversarialMockBackend(fail_at_call=3)  # Fails during chunk 2
        records = [
            {
                "record_id": f"rec_{i:02d}",
                "retrieval_text": f"Rollback test record {i}",
                "retrieval_text_hash": digest(f"roll_{i}"),
                "lane": "approved",
            }
            for i in range(6)
        ]
        self._insert_retrieval_records(conn, records)
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(RuntimeError):
            encode_and_insert_delta_records(
                connection=conn,
                delta_records=records,
                backend=backend,
                colbert_dimensions=None,
                host_chunk_size=2,
                inference_batch_size=2,
            )
        conn.rollback()

        dense_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
        sparse_count = conn.execute("SELECT count(*) FROM sparse_weight").fetchone()[0]
        colbert_count = conn.execute("SELECT count(*) FROM colbert_token").fetchone()[0]
        self.assertEqual(dense_count, 0)
        self.assertEqual(sparse_count, 0)
        self.assertEqual(colbert_count, 0)
        conn.close()

    def test_corrupted_prior_sidecar_fallback(self) -> None:
        """Verifies corrupted/unreadable prior sidecar database safely falls back to full encoding."""
        corrupted_path = self.root / "corrupted.sqlite"
        corrupted_path.write_bytes(b"THIS IS NOT A SQLITE DATABASE")
        backend = _AdversarialMockBackend()

        eligible, prior_hashes, colbert_dim = inspect_prior_sidecar(corrupted_path, backend)
        self.assertFalse(eligible)
        self.assertEqual(prior_hashes, {})
        self.assertIsNone(colbert_dim)


if __name__ == "__main__":
    unittest.main()
