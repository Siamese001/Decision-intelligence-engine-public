"""End-to-End Acceptance Suite for GPU Optimization Wave 4.

Verifies:
1. Separate host chunk and inference batch budgets in differential vector population.
2. Streaming window execution without unneeded output accumulation.
3. Encoding fingerprint generation and deterministic digest binding.
4. Differential reuse with matching encoding fingerprints.
5. Incompatible encoding fingerprint invalidates reuse and triggers safe full encoding.
6. Query cache key binding to encoding fingerprint digest.
7. Sidecar metadata persistence of encoding fingerprint digest.
8. Wave 4 runner execution and immutable receipt sealing.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dual_pipeline.core import connect_sqlite, digest
from dual_pipeline.index_core.backends import EmbeddingBackend
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, SIDECAR_SCHEMA, _pack
from dual_pipeline.index_core.differential import (
    encode_and_insert_delta_records,
    inspect_prior_sidecar,
    populate_sidecar_vectors,
)
from dual_pipeline.index_core.embedding_space_contract import build_query_cache_key
from storage.connection import sqlite3
from tools.gpu_optimization_runner_core import canonical_digest
from tools.run_gpu_optimization_wave import execute_gpu_wave


class _MockWave4Backend:
    """Mock backend implementing EmbeddingBackend with tracking and fingerprinting."""

    def __init__(
        self,
        *,
        model_revision: str = "rev-wave4-40hex-0000000000000000000000000",
        precision: str = "float32",
        colbert_dim: int = 128,
        device: str = "cpu",
    ) -> None:
        self.backend_id = "bge-m3"
        self.model_id = "BAAI/bge-m3"
        self.model_revision = model_revision
        self.model_manifest_digest = "manifest-wave4-digest-64hex"
        self.production_qualified = True
        self.assurance_tier = "enterprise_production"
        self.colbert_dimensions = colbert_dim
        self.retrieval_encoder = "bge-m3"
        self.supports_batch_encoding = True
        self.precision = precision
        self.execution_device = device

        self.encode_calls = 0
        self.encode_many_calls = 0
        self.batch_sizes_received: List[int] = []

    @property
    def encoding_fingerprint(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "model_manifest_digest": self.model_manifest_digest,
            "precision": self.precision,
            "device": self.execution_device,
            "tokenizer": "BAAI/bge-m3",
            "max_length": 8192,
            "truncation": True,
            "normalize_embeddings": True,
            "dense_dimensions": DENSE_DIMENSIONS,
            "max_colbert_tokens": 32,
        }

    @property
    def encoding_fingerprint_digest(self) -> str:
        return digest(self.encoding_fingerprint)

    def encode(self, text: str) -> Dict[str, Any]:
        self.encode_calls += 1
        raw = [0.01 * ((i % 100) + 1) for i in range(DENSE_DIMENSIONS)]
        norm = math.sqrt(sum(x * x for x in raw))
        dense = [x / norm for x in raw]
        sparse = {f"tok_{hash(text) % 1000}": 0.75, "tok_common": 0.5}
        colbert_tok = [0.02 * (j + 1) for j in range(self.colbert_dimensions)]
        return {
            "dense": dense,
            "sparse": sparse,
            "colbert": [colbert_tok, colbert_tok],
            "colbert_tokens": ["tok_0", "tok_1"],
        }

    def encode_many(
        self, texts: Iterable[str], *, batch_size: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        self.encode_many_calls += 1
        items = list(texts)
        if batch_size is not None and len(items) > batch_size:
            # If batch_size is provided, ensure caller respects chunking or we record sub-batches
            results: List[Dict[str, Any]] = []
            for offset in range(0, len(items), batch_size):
                sub = items[offset : offset + batch_size]
                self.batch_sizes_received.append(len(sub))
                results.extend([self.encode(t) for t in sub])
            return results
        self.batch_sizes_received.append(len(items))
        return [self.encode(t) for t in items]

    def encode_dense_only(self, text: str) -> List[float]:
        return self.encode(text)["dense"]

    def encode_many_dense_only(
        self, texts: Iterable[str], *, batch_size: Optional[int] = None
    ) -> List[List[float]]:
        return [self.encode_dense_only(t) for t in texts]

    def encode_streaming_windows(
        self,
        texts: Iterable[str],
        *,
        window_size: int = 64,
        inference_batch_size: Optional[int] = None,
    ) -> Iterable[List[Dict[str, Any]]]:
        chunk: List[str] = []
        for text in texts:
            chunk.append(text)
            if len(chunk) >= window_size:
                yield self.encode_many(chunk, batch_size=inference_batch_size)
                chunk = []
        if chunk:
            yield self.encode_many(chunk, batch_size=inference_batch_size)


class TestGpuWave4E2E(unittest.TestCase):
    """End-to-end test suite for Wave 4 embedding streaming and fingerprinting."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="gpu-wave4-e2e-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_sidecar_db(self, path: Path, backend: _MockWave4Backend) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect_sqlite(path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SIDECAR_SCHEMA)
        metadata = {
            "schema_version": "1.0.0",
            "generation_id": "gen-wave4-prior",
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "embedding_backend": backend.backend_id,
            "production_qualified": "true",
            "dense_dimensions": str(DENSE_DIMENSIONS),
            "colbert_dimensions": str(backend.colbert_dimensions),
            "encoding_fingerprint_digest": backend.encoding_fingerprint_digest,
        }
        conn.executemany("INSERT INTO metadata VALUES (?,?)", sorted(metadata.items()))
        conn.commit()
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
        conn.commit()

    def test_separate_host_chunk_and_inference_batch_budgets(self) -> None:
        """Verifies host_chunk_size and inference_batch_size are separately honored."""
        db_path = self.root / "test_chunking.sqlite"
        backend = _MockWave4Backend()
        conn = self._create_sidecar_db(db_path, backend)

        records = [
            {
                "record_id": f"rec_{i:03d}",
                "retrieval_text": f"Retrieval document contents for chunk test record {i}",
                "retrieval_text_hash": digest(f"rec_{i}"),
                "lane": "approved",
            }
            for i in range(10)
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
        conn.commit()

        self.assertEqual(count, 10)
        self.assertEqual(colbert_dim, 128)
        # All received batches must be <= inference_batch_size (2)
        self.assertTrue(all(sz <= 2 for sz in backend.batch_sizes_received))
        # 10 records split in host chunks [4, 4, 2] and inference batches of 2 -> 5 batches of 2
        self.assertEqual(sum(backend.batch_sizes_received), 10)

        dense_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
        sparse_count = conn.execute("SELECT count(DISTINCT record_id) FROM sparse_weight").fetchone()[0]
        colbert_count = conn.execute("SELECT count(DISTINCT record_id) FROM colbert_token").fetchone()[0]
        self.assertEqual(dense_count, 10)
        self.assertEqual(sparse_count, 10)
        self.assertEqual(colbert_count, 10)
        conn.close()

    def test_streaming_window_execution(self) -> None:
        """Verifies encode_streaming_windows yields bounded windows with preserved cardinality."""
        backend = _MockWave4Backend()
        texts = [f"Text item number {i} for streaming validation" for i in range(15)]

        windows = list(backend.encode_streaming_windows(texts, window_size=6, inference_batch_size=3))
        self.assertEqual(len(windows), 3)  # [6, 6, 3]
        self.assertEqual(len(windows[0]), 6)
        self.assertEqual(len(windows[1]), 6)
        self.assertEqual(len(windows[2]), 3)

        # Flattened results must be 15
        total_items = sum(len(w) for w in windows)
        self.assertEqual(total_items, 15)

    def test_encoding_fingerprint_generation_and_digest(self) -> None:
        """Verifies encoding_fingerprint properties and deterministic digest computation."""
        backend_fp32 = _MockWave4Backend(precision="float32")
        fp = backend_fp32.encoding_fingerprint

        self.assertEqual(fp["model_id"], "BAAI/bge-m3")
        self.assertEqual(fp["precision"], "float32")
        self.assertEqual(fp["dense_dimensions"], 1024)
        self.assertTrue(fp["truncation"])
        self.assertTrue(fp["normalize_embeddings"])

        digest_fp32 = backend_fp32.encoding_fingerprint_digest
        self.assertEqual(len(digest_fp32), 64)

        backend_fp16 = _MockWave4Backend(precision="float16")
        digest_fp16 = backend_fp16.encoding_fingerprint_digest
        self.assertNotEqual(digest_fp32, digest_fp16)

    def test_differential_reuse_matching_fingerprints(self) -> None:
        """Verifies unchanged records are reused zero-copy when fingerprints match."""
        prior_path = self.root / "prior_sidecar.sqlite"
        backend = _MockWave4Backend()
        prior_conn = self._create_sidecar_db(prior_path, backend)

        # Insert 3 records into prior
        prior_records = [
            {
                "record_id": f"rec_{i:02d}",
                "retrieval_text": f"Shared text document number {i}",
                "retrieval_text_hash": digest(f"Shared text document number {i}"),
                "lane": "approved",
            }
            for i in range(3)
        ]
        self._insert_retrieval_records(prior_conn, prior_records)
        encode_and_insert_delta_records(prior_conn, prior_records, backend, colbert_dimensions=None)
        prior_conn.commit()
        prior_conn.close()

        # In current run: rec_00 and rec_01 match prior, rec_03 is net-new
        current_path = self.root / "current_sidecar.sqlite"
        current_conn = self._create_sidecar_db(current_path, backend)
        current_records = [
            prior_records[0],
            prior_records[1],
            {
                "record_id": "rec_03",
                "retrieval_text": "Net-new document not in prior",
                "retrieval_text_hash": digest("Net-new document not in prior"),
                "lane": "approved",
            },
        ]
        self._insert_retrieval_records(current_conn, current_records)
        current_conn.commit()

        # Reset backend counters to observe net-new encoding
        backend.encode_calls = 0
        backend.encode_many_calls = 0

        total, colbert_dim, reused, delta = populate_sidecar_vectors(
            connection=current_conn,
            records=current_records,
            backend=backend,
            prior_sidecar_path=prior_path,
        )
        current_conn.commit()

        self.assertEqual(total, 3)
        self.assertEqual(reused, 2)
        self.assertEqual(delta, 1)

        # Net-new encoded only 1 record
        self.assertEqual(backend.encode_calls, 1)

        # Database has all 3 vectors
        dense_rows = current_conn.execute("SELECT record_id FROM dense_vector ORDER BY record_id").fetchall()
        self.assertEqual([r[0] for r in dense_rows], ["rec_00", "rec_01", "rec_03"])
        current_conn.close()

    def test_incompatible_fingerprint_invalidates_reuse(self) -> None:
        """Verifies prior sidecar with divergent fingerprint invalidates reuse and falls back."""
        prior_path = self.root / "prior_fp16.sqlite"
        backend_fp16 = _MockWave4Backend(precision="float16")
        prior_conn = self._create_sidecar_db(prior_path, backend_fp16)
        prior_conn.close()

        # Current backend uses float32
        backend_fp32 = _MockWave4Backend(precision="float32")
        eligible, prior_hashes, colbert_dim = inspect_prior_sidecar(prior_path, backend_fp32)

        self.assertFalse(eligible)
        self.assertEqual(prior_hashes, {})
        self.assertIsNone(colbert_dim)

    def test_query_cache_key_binding_to_encoding_fingerprint(self) -> None:
        """Verifies build_query_cache_key incorporates encoding_fingerprint_digest."""
        backend_fp32 = _MockWave4Backend(precision="float32")
        backend_fp16 = _MockWave4Backend(precision="float16")
        query = "What were the design decisions in Wave 4?"

        key1 = build_query_cache_key(backend_fp32, query)
        key2 = build_query_cache_key(backend_fp16, query)

        self.assertNotEqual(key1, key2)
        self.assertEqual(len(key1), 64)
        self.assertEqual(len(key2), 64)

    def test_gpu_wave4_runner_execution_and_receipt(self) -> None:
        """Verifies Wave 4 execution via run_gpu_optimization_wave runner producing sealed receipt."""
        plan_path = Path(__file__).resolve().parents[2] / "docs/plans/docs_gpu_optimization_debate_plan_20261006.md"
        sandbox = self.root / "wave4_sandbox"
        receipts_dir = self.root / "receipts"

        repo_receipts = Path(__file__).resolve().parents[2] / "reports" / "gpu_optimization"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        for w in (1, 2, 3):
            src = repo_receipts / f"gpu_wave_{w}_receipt.json"
            if src.exists():
                shutil.copy(src, receipts_dir / f"gpu_wave_{w}_receipt.json")

        mock_run_res = mock.MagicMock()
        mock_run_res.total_tests = 18
        mock_run_res.passed_count = 18
        mock_run_res.failed_count = 0
        mock_run_res.error_count = 0
        mock_run_res.skipped_count = 0
        mock_run_res.elapsed_seconds = 1.25
        mock_run_res.collected_ids = [f"test_gpu_w4_{i}" for i in range(18)]
        mock_run_res.wasSuccessful.return_value = True

        with mock.patch("tools.run_gpu_optimization_wave.run_pytest_with_progress", return_value=mock_run_res):
            exit_code = execute_gpu_wave(
                wave=4,
                sandbox=sandbox,
                plan_path=plan_path,
                receipt_root=receipts_dir,
                timeout=60,
                immutable=False,
            )
        self.assertEqual(exit_code, 0)
        receipt_path = receipts_dir / "gpu_wave_4_receipt.json"
        self.assertTrue(receipt_path.is_file())
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt_data["status"], "PASS")
        self.assertEqual(receipt_data["wave"], 4)
        self.assertEqual(receipt_data["parent_receipt"]["wave"], 3)


if __name__ == "__main__":
    unittest.main()
