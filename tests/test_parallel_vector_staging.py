#!/usr/bin/env python3
"""Comprehensive test suite for parallel vector staging and sidecar integration.

Validates:
1. Content-addressed SQLite vector staging store creation, inspection, and vector packing.
2. Zero-delta sidecar publication: pre-staged vectors achieve 100% hit rate with zero in-band encoding.
3. Multi-path staging: multiple document pipelines staging separate SQLite stores combined seamlessly.
4. Tri-modal inheritance: differential prior reuse + staged vectors + net-new delta encoding.
5. Adversarial defenses: corrupt staging DB, model revision mismatch, NaN/Inf vectors, norm bounds.
6. Concurrency & storage safety: parallel multi-worker staging and shared WAL concurrency without locking errors.
"""
from __future__ import annotations

import concurrent.futures
import math
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import connect_sqlite, utc_now  # noqa: E402
from dual_pipeline.index_core.backends import EmbeddingBackend  # noqa: E402
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, _pack  # noqa: E402
from dual_pipeline.index_core.differential import (  # noqa: E402
    PopulateSidecarResult,
    get_effective_embedding_telemetry,
    populate_sidecar_vectors,
)
from dual_pipeline.index_core.records import render_retrieval_records  # noqa: E402
from dual_pipeline.index_core.sidecar import build_sidecar, validate_sidecar  # noqa: E402
from dual_pipeline.index_core.vector_staging import (  # noqa: E402
    apply_staged_vectors,
    copy_staged_vectors,
    encode_and_stage_records,
    init_vector_staging_db,
    inspect_staging_store,
    stage_encoded_vectors,
)
from storage.connection import sqlite3  # noqa: E402


class DeterministicTestBackend:
    """Deterministic mock embedding backend satisfying EmbeddingBackend protocol."""

    backend_id: str = "flagembedding-bge-m3-dev"
    model_id: str = "BAAI/bge-m3"
    model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    production_qualified: bool = False
    assurance_tier: str = "development"
    model_manifest_digest: Optional[str] = "5617a9f61b028005a4858fdac845db406aefb1815617a9f61b028005a4858fda"
    colbert_dimensions: int = 1024
    retrieval_encoder: str = "test-deterministic"
    encoding_fingerprint_digest: str = "test-fingerprint-digest-123"

    def __init__(
        self,
        model_id: Optional[str] = None,
        model_revision: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ) -> None:
        if model_id:
            self.model_id = model_id
        if model_revision:
            self.model_revision = model_revision
        if fingerprint:
            self.encoding_fingerprint_digest = fingerprint
        self.encode_calls: List[str] = []

    @property
    def encoding_fingerprint(self) -> Dict[str, Any]:
        return {
            "backend": self.backend_id,
            "revision": self.model_revision,
            "digest": self.encoding_fingerprint_digest,
        }

    def encode(self, text: str) -> Dict[str, Any]:
        self.encode_calls.append(text)
        unit_val = 1.0 / math.sqrt(DENSE_DIMENSIONS)
        dense = [unit_val] * DENSE_DIMENSIONS
        sparse = {"keyword": 0.75, "entity": 0.45}
        colbert = [[unit_val] * 1024, [unit_val] * 1024]
        colbert_tokens = ["[CLS]", "token"]
        return {
            "dense": dense,
            "sparse": sparse,
            "colbert": colbert,
            "colbert_tokens": colbert_tokens,
        }

    def encode_dense_only(self, text: str) -> List[float]:
        self.encode_calls.append(text)
        unit_val = 1.0 / math.sqrt(DENSE_DIMENSIONS)
        return [unit_val] * DENSE_DIMENSIONS

    def encode_many_dense_only(
        self, texts: Any, *, batch_size: Optional[int] = None
    ) -> List[List[float]]:
        return [self.encode_dense_only(t) for t in texts]

    def verify_snapshot(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "manifest_digest": self.model_manifest_digest,
        }


class TestVectorStagingStore(unittest.TestCase):
    """Unit tests for content-addressed SQLite staging store primitives."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-staging-unit-")
        self.root = Path(self.temp_dir.name)
        self.backend = DeterministicTestBackend()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_init_vector_staging_db(self) -> None:
        store_path = self.root / "stage1.sqlite"
        init_vector_staging_db(store_path, self.backend)

        self.assertTrue(store_path.is_file())
        conn = connect_sqlite(store_path, readonly=True)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("staging_metadata", tables)
            self.assertIn("staged_dense_vector", tables)
            self.assertIn("staged_sparse_weight", tables)
            self.assertIn("staged_colbert_token", tables)

            meta = dict(conn.execute("SELECT key, value FROM staging_metadata").fetchall())
            self.assertEqual(meta["model_id"], self.backend.model_id)
            self.assertEqual(meta["model_revision"], self.backend.model_revision)
            self.assertEqual(meta["dense_dimensions"], "1024")
        finally:
            conn.close()

    def test_inspect_staging_store_valid(self) -> None:
        store_path = self.root / "stage_inspect.sqlite"
        init_vector_staging_db(store_path, self.backend)

        # Stage one record
        records = [{"retrieval_text_hash": "hash_abc_123"}]
        encoded = [self.backend.encode("dummy text")]
        stage_encoded_vectors(store_path, self.backend, records, encoded)

        eligible, hashes, colbert_dim = inspect_staging_store(store_path, self.backend)
        self.assertTrue(eligible)
        self.assertEqual(hashes, {"hash_abc_123"})
        self.assertEqual(colbert_dim, 1024)

    def test_inspect_staging_store_invalid_and_corrupt(self) -> None:
        # Non-existent
        eligible, hashes, _ = inspect_staging_store(self.root / "missing.sqlite", self.backend)
        self.assertFalse(eligible)
        self.assertEqual(hashes, set())

        # Corrupted / non-sqlite
        corrupt = self.root / "corrupt.sqlite"
        corrupt.write_bytes(b"garbage not sqlite content")
        eligible, hashes, _ = inspect_staging_store(corrupt, self.backend)
        self.assertFalse(eligible)
        self.assertEqual(hashes, set())

    def test_inspect_staging_store_mismatches(self) -> None:
        store_path = self.root / "stage_mismatch.sqlite"
        init_vector_staging_db(store_path, self.backend)

        # Model ID mismatch
        other_backend = DeterministicTestBackend(model_id="other/model")
        eligible, _, _ = inspect_staging_store(store_path, other_backend)
        self.assertFalse(eligible)

        # Model revision mismatch
        rev_backend = DeterministicTestBackend(model_revision="other_rev_xyz")
        eligible, _, _ = inspect_staging_store(store_path, rev_backend)
        self.assertFalse(eligible)

        # Fingerprint mismatch
        fp_backend = DeterministicTestBackend(fingerprint="other_fingerprint")
        eligible, _, _ = inspect_staging_store(store_path, fp_backend)
        self.assertFalse(eligible)

    def test_stage_encoded_vectors_validation_failures(self) -> None:
        store_path = self.root / "stage_fail.sqlite"
        init_vector_staging_db(store_path, self.backend)

        records = [{"retrieval_text_hash": "hash_bad"}]

        # 1. Invalid dense dimensions
        bad_dense = [0.1] * 512
        with self.assertRaises(ValueError):
            stage_encoded_vectors(
                store_path,
                self.backend,
                records,
                [{"dense": bad_dense, "sparse": {}, "colbert": [[0.1]*1024], "colbert_tokens": ["t"]}],
            )

        # 2. NaN in dense vector
        nan_dense = [0.1] * 1024
        nan_dense[5] = float("nan")
        with self.assertRaises(ValueError):
            stage_encoded_vectors(
                store_path,
                self.backend,
                records,
                [{"dense": nan_dense, "sparse": {}, "colbert": [[0.1]*1024], "colbert_tokens": ["t"]}],
            )

        # 3. Dense norm out of bounds [0.90, 1.10]
        unnorm_dense = [1.0] * 1024  # norm is sqrt(1024) = 32.0 >> 1.10
        with self.assertRaises(ValueError):
            stage_encoded_vectors(
                store_path,
                self.backend,
                records,
                [{"dense": unnorm_dense, "sparse": {}, "colbert": [[0.1]*1024], "colbert_tokens": ["t"]}],
            )

        # 4. ColBERT token/vector mismatch
        with self.assertRaises(ValueError):
            stage_encoded_vectors(
                store_path,
                self.backend,
                records,
                [{
                    "dense": [1.0 / math.sqrt(1024)] * 1024,
                    "sparse": {},
                    "colbert": [[0.1]*1024],
                    "colbert_tokens": ["t1", "t2"],
                }],
            )

    def test_encode_and_stage_records_batch(self) -> None:
        store_path = self.root / "stage_batch.sqlite"
        records = [
            {"retrieval_text": f"Document paragraph {i}", "retrieval_text_hash": f"hash_doc_{i}"}
            for i in range(12)
        ]
        count = encode_and_stage_records(
            store_path,
            self.backend,
            records,
            host_chunk_size=4,
            inference_batch_size=2,
        )
        self.assertEqual(count, 12)
        self.assertEqual(len(self.backend.encode_calls), 12)

        eligible, hashes, _ = inspect_staging_store(store_path, self.backend)
        self.assertTrue(eligible)
        self.assertEqual(len(hashes), 12)


class TestParallelVectorSidecarIntegration(unittest.TestCase):
    """Integration tests verifying build_sidecar and populate_sidecar_vectors with staging."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-sidecar-stg-")
        self.root = Path(self.temp_dir.name)
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        if not self.graph.exists():
            self.skipTest("Base graph not found at graph/nee_graph.sqlite")

        # Discover first 30 retrieval records for deterministic testing
        self.records = render_retrieval_records(self.graph, "snapshot-test")[:30]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_populate_sidecar_vectors_with_staged_paths(self) -> None:
        """Tests populate_sidecar_vectors reusing pre-staged vectors with PopulateSidecarResult."""
        staging_path = self.root / "staged.sqlite"
        backend = DeterministicTestBackend()

        # Pre-stage all 30 records
        encode_and_stage_records(staging_path, backend, self.records)
        self.assertEqual(len(backend.encode_calls), 30)

        # Clear backend call log
        backend.encode_calls.clear()

        # Connect a fresh in-memory sidecar database
        from dual_pipeline.index_core.constants import SIDECAR_SCHEMA

        conn = connect_sqlite(self.root / "target_sidecar.sqlite")
        try:
            conn.executescript(SIDECAR_SCHEMA)
            conn.executemany(
                """INSERT INTO retrieval_record (record_id, lane, record_type, namespace,
                   retrieval_text_hash, retrieval_kind, epistemic_status, provenance_kind,
                   answer_use, artifact_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r["record_id"], r["lane"], r["record_type"], r["namespace"],
                        r["retrieval_text_hash"], r["retrieval_kind"], r["epistemic_status"],
                        r["provenance_kind"], r["answer_use"], "{}"
                    )
                    for r in self.records
                ],
            )

            res = populate_sidecar_vectors(
                conn,
                self.records,
                backend,
                staged_vector_paths=[staging_path],
            )

            # Unpack 4-tuple compatibility check
            total, colbert_dim, reused, delta = res
            self.assertEqual(total, 30)
            self.assertEqual(reused, 0)
            self.assertEqual(delta, 0)
            self.assertEqual(res.staged_records, 30)

            # ZERO calls to backend.encode because all 30 were staged
            self.assertEqual(len(backend.encode_calls), 0)

            # Verify sidecar tables populated
            dense_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
            self.assertEqual(dense_count, 30)
            colbert_count = conn.execute(
                "SELECT count(DISTINCT record_id) FROM colbert_token"
            ).fetchone()[0]
            self.assertEqual(colbert_count, 30)
        finally:
            conn.close()

    def test_build_sidecar_with_full_staged_reuse(self) -> None:
        """Tests build_sidecar achieving 100% staging reuse and zero in-band encoding."""
        # Pre-stage all corpus records into staging DB
        all_records = render_retrieval_records(self.graph, "snapshot-full")
        staging_path = self.root / "staging_full.sqlite"
        backend_prep = DeterministicTestBackend()

        encode_and_stage_records(staging_path, backend_prep, all_records)

        # Now build the sidecar with a fresh tracking backend
        backend_run = DeterministicTestBackend()
        target_sidecar = self.root / "generation_staged" / "sidecar.sqlite"

        receipt = build_sidecar(
            graph_path=self.graph,
            sidecar_path=target_sidecar,
            generation_id="gen-staged-full",
            graph_snapshot="snapshot-full",
            backend=backend_run,
            staged_vector_paths=[staging_path],
        )

        self.assertTrue(target_sidecar.is_file())
        self.assertEqual(receipt["validation"]["status"], "PASS")
        self.assertEqual(receipt["delta_records"], 0)
        self.assertEqual(receipt["staged_records"], len(all_records))
        self.assertEqual(receipt["staged_vector_hits"], len(all_records))
        # Zero in-band calls during the release publication!
        self.assertEqual(len(backend_run.encode_calls), 0)

        # Verify metadata table in SQLite sidecar contains staged_vector_hits
        conn = connect_sqlite(target_sidecar, readonly=True)
        try:
            hits = conn.execute(
                "SELECT value FROM metadata WHERE key='staged_vector_hits'"
            ).fetchone()[0]
            self.assertEqual(int(hits), len(all_records))
        finally:
            conn.close()

    def test_multi_path_staging_combines_independently(self) -> None:
        """Tests multiple document pipelines staging separate SQLite stores combined seamlessly."""
        mid = len(self.records) // 2
        batch_a = self.records[:mid]
        batch_b = self.records[mid:]

        path_a = self.root / "pipeline_a_staged.sqlite"
        path_b = self.root / "pipeline_b_staged.sqlite"

        backend_a = DeterministicTestBackend()
        backend_b = DeterministicTestBackend()

        encode_and_stage_records(path_a, backend_a, batch_a)
        encode_and_stage_records(path_b, backend_b, batch_b)

        # Build sidecar referencing both staging paths
        backend_run = DeterministicTestBackend()
        from dual_pipeline.index_core.constants import SIDECAR_SCHEMA

        conn = connect_sqlite(self.root / "sidecar_multi.sqlite")
        try:
            conn.executescript(SIDECAR_SCHEMA)
            conn.executemany(
                """INSERT INTO retrieval_record (record_id, lane, record_type, namespace,
                   retrieval_text_hash, retrieval_kind, epistemic_status, provenance_kind,
                   answer_use, artifact_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r["record_id"], r["lane"], r["record_type"], r["namespace"],
                        r["retrieval_text_hash"], r["retrieval_kind"], r["epistemic_status"],
                        r["provenance_kind"], r["answer_use"], "{}"
                    )
                    for r in self.records
                ],
            )
            res = populate_sidecar_vectors(
                conn,
                self.records,
                backend_run,
                staged_vector_paths=[path_a, path_b],
            )
            total, _, reused, delta = res
            self.assertEqual(total, len(self.records))
            self.assertEqual(delta, 0)
            self.assertEqual(res.staged_records, len(self.records))
            self.assertEqual(len(backend_run.encode_calls), 0)
        finally:
            conn.close()

    def test_tri_modal_inheritance_differential_staged_delta(self) -> None:
        """Tests prior sidecar reuse + staging reuse + net-new delta encoding in one pass."""
        part1 = self.records[:10]  # in prior sidecar
        part2 = self.records[10:20]  # in staging store
        part3 = self.records[20:30]  # neither -> delta encode in-band

        # 1. Build prior sidecar with part1
        prior_path = self.root / "prior_sidecar.sqlite"
        backend_prior = DeterministicTestBackend()
        from dual_pipeline.index_core.constants import SIDECAR_SCHEMA

        conn_prior = connect_sqlite(prior_path)
        try:
            conn_prior.executescript(SIDECAR_SCHEMA)
            # Insert retrieval_record rows for part1
            conn_prior.executemany(
                """INSERT INTO retrieval_record (record_id, lane, record_type, namespace,
                   retrieval_text_hash, retrieval_kind, epistemic_status, provenance_kind,
                   answer_use, artifact_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r["record_id"],
                        r["lane"],
                        r["record_type"],
                        r["namespace"],
                        r["retrieval_text_hash"],
                        r["retrieval_kind"],
                        r["epistemic_status"],
                        r["provenance_kind"],
                        r["answer_use"],
                        "{}",
                    )
                    for r in part1
                ],
            )
            meta = {
                "embedding_backend": backend_prior.backend_id,
                "model_id": backend_prior.model_id,
                "model_revision": backend_prior.model_revision,
                "encoding_fingerprint_digest": backend_prior.encoding_fingerprint_digest,
                "dense_dimensions": "1024",
                "colbert_dimensions": "1024",
            }
            conn_prior.executemany(
                "INSERT INTO metadata VALUES (?,?)", sorted(meta.items())
            )
            populate_sidecar_vectors(conn_prior, part1, backend_prior)
            conn_prior.commit()
        finally:
            conn_prior.close()

        # 2. Stage part2 into staging DB
        staging_path = self.root / "part2_staged.sqlite"
        backend_stage = DeterministicTestBackend()
        encode_and_stage_records(staging_path, backend_stage, part2)

        # 3. Populate target with all 30 records
        backend_target = DeterministicTestBackend()
        conn_target = connect_sqlite(self.root / "tri_modal_target.sqlite")
        try:
            conn_target.executescript(SIDECAR_SCHEMA)
            conn_target.executemany(
                """INSERT INTO retrieval_record (record_id, lane, record_type, namespace,
                   retrieval_text_hash, retrieval_kind, epistemic_status, provenance_kind,
                   answer_use, artifact_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r["record_id"],
                        r["lane"],
                        r["record_type"],
                        r["namespace"],
                        r["retrieval_text_hash"],
                        r["retrieval_kind"],
                        r["epistemic_status"],
                        r["provenance_kind"],
                        r["answer_use"],
                        "{}",
                    )
                    for r in self.records
                ],
            )

            res = populate_sidecar_vectors(
                conn_target,
                self.records,
                backend_target,
                prior_sidecar_path=prior_path,
                staged_vector_paths=[staging_path],
            )
            total, _, reused, delta = res
            self.assertEqual(total, 30)
            self.assertEqual(reused, 10)  # from prior
            self.assertEqual(res.staged_records, 10)  # from staging
            self.assertEqual(delta, 10)  # net-new encoded in-band
            self.assertEqual(len(backend_target.encode_calls), 10)

            # Telemetry verification
            telem = get_effective_embedding_telemetry(
                backend_target,
                total_records=total,
                reused_records=reused,
                delta_records=delta,
                cache_hits=reused,
                staged_records=res.staged_records,
            )
            self.assertEqual(telem["total_records"], 30)
            self.assertEqual(telem["reused_records"], 10)
            self.assertEqual(telem["staged_records"], 10)
            self.assertEqual(telem["delta_records"], 10)
        finally:
            conn_target.close()


class TestParallelVectorStagingAdversarial(unittest.TestCase):
    """Adversarial and failure mode test coverage."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-staging-adv-")
        self.root = Path(self.temp_dir.name)
        self.backend = DeterministicTestBackend()
        self.records = [
            {
                "record_id": f"rec_{i}",
                "retrieval_text": f"text {i}",
                "retrieval_text_hash": f"hash_{i}",
            }
            for i in range(5)
        ]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_corrupt_staging_db_falls_back_gracefully(self) -> None:
        """Corrupted staging file is safely ignored and records are encoded via delta path."""
        corrupt_path = self.root / "corrupt_staged.sqlite"
        corrupt_path.write_bytes(b"garbage not sqlite content")

        from dual_pipeline.index_core.constants import SIDECAR_SCHEMA

        conn = connect_sqlite(self.root / "adv_target.sqlite")
        try:
            conn.executescript(SIDECAR_SCHEMA)
            conn.executemany(
                """INSERT INTO retrieval_record (record_id, lane, record_type, namespace,
                   retrieval_text_hash, retrieval_kind, epistemic_status, provenance_kind,
                   answer_use, artifact_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r["record_id"], "approved", "claim", "canonical",
                        r["retrieval_text_hash"], "assertion", "approved",
                        "source_bound", "factual_answer", "{}"
                    )
                    for r in self.records
                ],
            )
            res = populate_sidecar_vectors(
                conn,
                self.records,
                self.backend,
                staged_vector_paths=[corrupt_path],
            )
            total, _, reused, delta = res
            self.assertEqual(total, 5)
            self.assertEqual(reused, 0)
            self.assertEqual(res.staged_records, 0)
            self.assertEqual(delta, 5)  # fell back safely to in-band delta
            self.assertEqual(len(self.backend.encode_calls), 5)
        finally:
            conn.close()

    def test_tampered_vector_in_staging_fails_closed(self) -> None:
        """If a staged dense vector has norm out of bounds, copy_staged_vectors fails closed."""
        staging_path = self.root / "tampered.sqlite"
        init_vector_staging_db(staging_path, self.backend)

        # Insert a raw invalid row bypassing stage_encoded_vectors
        conn_stg = sqlite3.connect(str(staging_path))
        try:
            bad_blob = _pack([10.0] * 1024)  # extreme norm
            conn_stg.execute(
                "INSERT INTO staged_dense_vector VALUES ('hash_0', 1024, ?)",
                (bad_blob,),
            )
            conn_stg.execute("INSERT INTO staged_sparse_weight VALUES ('hash_0', 'tok', 1.0)")
            conn_stg.execute(
                "INSERT INTO staged_colbert_token VALUES ('hash_0', 0, 'tok', 1024, ?)",
                (_pack([0.1] * 1024),),
            )
            conn_stg.commit()
        finally:
            conn_stg.close()

        from dual_pipeline.index_core.constants import SIDECAR_SCHEMA

        conn_sidecar = connect_sqlite(self.root / "sidecar_tamper.sqlite")
        try:
            conn_sidecar.executescript(SIDECAR_SCHEMA)
            conn_sidecar.execute(
                """INSERT INTO retrieval_record (record_id, lane, record_type, namespace,
                   retrieval_text_hash, retrieval_kind, epistemic_status, provenance_kind,
                   answer_use, artifact_json) VALUES ('rec_0', 'approved', 'claim', 'canonical',
                   'hash_0', 'assertion', 'approved', 'source_bound', 'factual_answer', '{}')"""
            )
            with self.assertRaises(RuntimeError) as ctx:
                copy_staged_vectors(
                    conn_sidecar,
                    staging_path,
                    {"rec_0": "hash_0"},
                )
            self.assertIn("violates norm bounds", str(ctx.exception))
        finally:
            conn_sidecar.close()


class TestParallelVectorStagingConcurrency(unittest.TestCase):
    """Concurrency and thread safety under WAL mode."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-staging-conc-")
        self.root = Path(self.temp_dir.name)
        self.backend = DeterministicTestBackend()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_concurrent_multi_worker_staging_stores(self) -> None:
        """Multiple parallel document workers writing to separate staging SQLite DBs."""
        num_workers = 8
        records_per_worker = 10

        def _worker_task(worker_id: int) -> Path:
            store_path = self.root / f"worker_{worker_id}_staging.sqlite"
            records = [
                {
                    "retrieval_text": f"Worker {worker_id} text {i}",
                    "retrieval_text_hash": f"hash_w{worker_id}_{i}",
                }
                for i in range(records_per_worker)
            ]
            count = encode_and_stage_records(store_path, self.backend, records)
            assert count == records_per_worker
            return store_path

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(_worker_task, i) for i in range(num_workers)]
            staging_paths = [f.result() for f in futures]

        self.assertEqual(len(staging_paths), num_workers)
        for p in staging_paths:
            self.assertTrue(p.is_file())
            eligible, hashes, _ = inspect_staging_store(p, self.backend)
            self.assertTrue(eligible)
            self.assertEqual(len(hashes), records_per_worker)

    def test_concurrent_writes_to_shared_staging_wal(self) -> None:
        """Multiple threads writing to the same staging store in WAL mode without locking error."""
        shared_store = self.root / "shared_staging.sqlite"
        init_vector_staging_db(shared_store, self.backend)

        def _write_chunk(thread_id: int) -> int:
            records = [
                {
                    "retrieval_text": f"Thread {thread_id} item {i}",
                    "retrieval_text_hash": f"hash_th{thread_id}_{i}",
                }
                for i in range(5)
            ]
            unit_val = 1.0 / math.sqrt(DENSE_DIMENSIONS)
            encoded = [
                {
                    "dense": [unit_val] * DENSE_DIMENSIONS,
                    "sparse": {"tok": 0.5},
                    "colbert": [[unit_val] * 1024],
                    "colbert_tokens": ["tok"],
                }
                for _ in range(5)
            ]
            return stage_encoded_vectors(shared_store, self.backend, records, encoded)

        num_threads = 6
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_write_chunk, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        self.assertEqual(sum(results), num_threads * 5)
        eligible, hashes, _ = inspect_staging_store(shared_store, self.backend)
        self.assertTrue(eligible)
        self.assertEqual(len(hashes), num_threads * 5)


if __name__ == "__main__":
    unittest.main()
