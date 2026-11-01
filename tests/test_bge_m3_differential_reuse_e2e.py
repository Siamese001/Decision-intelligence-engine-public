"""End-to-end tests for content-addressable differential vector reuse and bulk SQLite staging."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, List

import numpy as np
import pytest

from dual_pipeline.core import connect_sqlite
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, SIDECAR_SCHEMA, _pack
from dual_pipeline.index_core.differential import populate_sidecar_vectors
from dual_pipeline.index_core.vector_staging import (
    copy_staged_vectors,
    init_vector_staging_db,
    stage_encoded_vectors,
)


class MockDiffBackend:
    """Mock backend tracking encode calls."""

    def __init__(self) -> None:
        self.encode_count = 0
        self.backend_id = "bge-m3-dev"
        self.model_id = "BAAI/bge-m3"
        self.model_revision = "0000000000000000000000000000000000000000"
        self.model_manifest_digest = "test-digest"
        self.production_qualified = True
        self.supports_batch_encoding = True

    def encode_many(self, texts: List[str], *, batch_size: int | None = None) -> List[dict[str, Any]]:
        self.encode_count += len(texts)
        results = []
        for t in texts:
            # Generate unit-norm vector so it passes norm checks [0.90, 1.10]
            raw = np.ones(1024, dtype=np.float32)
            norm = float(np.linalg.norm(raw))
            unit_vec = (raw / norm).tolist()
            results.append({
                "dense": unit_vec,
                "sparse": {"token1": 0.5},
                "colbert_tokens": ["token-0"],
                "colbert": [unit_vec],
            })
        return results

    def encode(self, text: str) -> dict[str, Any]:
        return self.encode_many([text])[0]


def _setup_sidecar_db(path: Path) -> None:
    conn = connect_sqlite(path)
    conn.executescript(SIDECAR_SCHEMA)
    conn.execute(
        "INSERT INTO metadata VALUES (?,?)",
        ("colbert_dimensions", "1024"),
    )
    conn.execute(
        "INSERT INTO metadata VALUES (?,?)",
        ("dense_dimensions", "1024"),
    )
    conn.execute(
        "INSERT INTO metadata VALUES (?,?)",
        ("model_id", "BAAI/bge-m3"),
    )
    conn.execute(
        "INSERT INTO metadata VALUES (?,?)",
        ("model_revision", "0000000000000000000000000000000000000000"),
    )
    conn.execute(
        "INSERT INTO metadata VALUES (?,?)",
        ("embedding_backend", "bge-m3-dev"),
    )
    conn.commit()
    conn.close()


def test_content_addressable_vector_reuse_across_new_record_ids() -> None:
    """Verify that records with brand new record_ids but identical retrieval_text_hash are reused."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        prior_sidecar = Path(tmp_dir) / "prior_sidecar.sqlite"
        new_sidecar = Path(tmp_dir) / "new_sidecar.sqlite"
        _setup_sidecar_db(prior_sidecar)
        _setup_sidecar_db(new_sidecar)

        # Seed prior sidecar with record_id="orig-rec-1", text_hash="hash-abc"
        p_conn = connect_sqlite(prior_sidecar)
        p_conn.execute(
            """INSERT INTO retrieval_record VALUES (
                'orig-rec-1', 'approved', 'assertion', 'default', 'hash-abc',
                'assertion', 'approved', 'source_bound', 'factual_answer', '{}'
            )"""
        )
        sample_vec = (np.ones(1024, dtype=np.float32) / 32.0).astype(np.float32)
        p_conn.execute("INSERT INTO dense_vector VALUES (?,?,?)", ("orig-rec-1", 1024, _pack(sample_vec.tolist())))
        p_conn.execute("INSERT INTO sparse_weight VALUES (?,?,?)", ("orig-rec-1", "tok", 0.75))
        p_conn.execute("INSERT INTO colbert_token VALUES (?,?,?,?,?)", ("orig-rec-1", 0, "token-0", 1024, _pack(sample_vec.tolist())))
        p_conn.commit()
        p_conn.close()

        backend = MockDiffBackend()

        # In new sidecar, record arrives with brand new record_id="new-rec-999" but SAME text_hash="hash-abc"
        records_to_populate = [
            {
                "record_id": "new-rec-999",
                "lane": "approved",
                "record_type": "assertion",
                "namespace": "default",
                "retrieval_text": "text 1",
                "retrieval_text_hash": "hash-abc",
                "retrieval_kind": "assertion",
                "epistemic_status": "approved",
                "provenance_kind": "source_bound",
                "answer_use": "factual_answer",
                "artifact_json": "{}",
            }
        ]

        n_conn = connect_sqlite(new_sidecar)
        # Pre-insert retrieval_record as build_sidecar does before calling populate_sidecar_vectors
        n_conn.execute(
            """INSERT INTO retrieval_record VALUES (
                'new-rec-999', 'approved', 'assertion', 'default', 'hash-abc',
                'assertion', 'approved', 'source_bound', 'factual_answer', '{}'
            )"""
        )
        res = populate_sidecar_vectors(
            n_conn,
            records_to_populate,
            backend,
            prior_sidecar_path=prior_sidecar,
        )
        n_conn.commit()

        # Reused should be 1, delta should be 0, backend encode_count should be 0!
        assert res[2] == 1, f"Expected 1 reused record, got {res[2]}"
        assert res[3] == 0, f"Expected 0 delta records, got {res[3]}"
        assert backend.encode_count == 0, f"Backend encoded {backend.encode_count} items instead of reusing prior vector!"

        # Verify dense_vector was populated under "new-rec-999"
        row = n_conn.execute("SELECT record_id, dimensions FROM dense_vector WHERE record_id = 'new-rec-999'").fetchone()
        assert row is not None
        assert row[0] == "new-rec-999"
        assert row[1] == 1024
        n_conn.close()


def test_bulk_vector_staging_and_copy() -> None:
    """Verify chunk-level bulk staging and subsequent copy."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        staging_db = Path(tmp_dir) / "staging.sqlite"
        dest_sidecar = Path(tmp_dir) / "dest.sqlite"
        backend = MockDiffBackend()

        init_vector_staging_db(staging_db, backend)
        _setup_sidecar_db(dest_sidecar)

        records = [
            {"record_id": f"rec-{i}", "retrieval_text": f"text {i}", "retrieval_text_hash": f"hash-{i}"}
            for i in range(10)
        ]
        encoded = backend.encode_many([r["retrieval_text"] for r in records])

        # Stage vectors
        staged_count = stage_encoded_vectors(staging_db, backend, records, encoded)
        assert staged_count == 10

        # Copy into destination
        dest_conn = connect_sqlite(dest_sidecar)
        # Seed destination retrieval_record table to satisfy foreign keys
        for i in range(10):
            dest_conn.execute(
                f"""INSERT INTO retrieval_record VALUES (
                    'dest-rec-{i}', 'approved', 'assertion', 'default', 'hash-{i}',
                    'assertion', 'approved', 'source_bound', 'factual_answer', '{{}}'
                )"""
            )
        dest_conn.commit()

        id_map = {f"dest-rec-{i}": f"hash-{i}" for i in range(10)}
        copied = copy_staged_vectors(dest_conn, staging_db, id_map)
        assert copied == 10

        dest_rows = dest_conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
        assert dest_rows == 10
        dest_conn.close()
