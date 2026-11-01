"""Adversarial and boundary tests for differential reuse and staging."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, List

import pytest

from dual_pipeline.core import connect_sqlite
from dual_pipeline.index_core.constants import SIDECAR_SCHEMA
from dual_pipeline.index_core.content_differential import (
    bulk_insert_chunk_records,
    copy_vectors_by_text_hash,
    partition_reusable_records,
)
from dual_pipeline.index_core.staging_bulk_ops import bulk_insert_staged_vectors


def test_partition_reusable_records_boundary_conditions() -> None:
    """Boundary test: empty records, empty prior hashes, exact match vs content match."""
    # Empty inputs
    reusable, content_pairs, deltas = partition_reusable_records([], {})
    assert reusable == []
    assert content_pairs == []
    assert deltas == []

    records = [
        {"record_id": "r1", "retrieval_text_hash": "h1"},
        {"record_id": "r2_new", "retrieval_text_hash": "h2"},
        {"record_id": "r3_fresh", "retrieval_text_hash": "h3_unseen"},
    ]
    prior_hashes = {
        "r1": "h1",          # Exact record_id + text_hash match
        "h2": "r2_old",       # Reverse hash match (new record_id, known content)
    }

    reusable, content_pairs, deltas = partition_reusable_records(records, prior_hashes)

    assert len(reusable) == 1
    assert reusable[0]["record_id"] == "r1"

    assert len(content_pairs) == 1
    assert content_pairs[0] == ("r2_new", "r2_old")

    assert len(deltas) == 1
    assert deltas[0]["record_id"] == "r3_fresh"


def test_bulk_insert_staged_vectors_missing_hash_fails_closed() -> None:
    """Adversarial test: malformed payload missing retrieval_text_hash raises ValueError."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "staging.sqlite"
        conn = connect_sqlite(db_path)
        conn.execute("CREATE TABLE staged_dense_vector (retrieval_text_hash TEXT PRIMARY KEY, dimensions INT, vector BLOB)")
        conn.execute("CREATE TABLE staged_sparse_weight (retrieval_text_hash TEXT, token TEXT, weight REAL)")
        conn.execute("CREATE TABLE staged_colbert_token (retrieval_text_hash TEXT, position INT, token TEXT, dimensions INT, vector BLOB)")
        conn.commit()
        conn.close()

        records = [{"record_id": "r1", "retrieval_text_hash": ""}]
        encoded = [{"dense": [0.1] * 1024, "sparse": {}, "colbert": [], "colbert_tokens": []}]

        with pytest.raises(ValueError, match="retrieval_text_hash"):
            bulk_insert_staged_vectors(db_path, records, encoded, lambda h, e: None)
