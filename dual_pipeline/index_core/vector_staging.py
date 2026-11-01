"""Content-addressed SQLite vector staging store.

Provides decoupled, out-of-band vector pre-computation for parallel document
ingestion pipelines, enabling background embedding generation without holding
the serialized release lock.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import numpy as np

from dual_pipeline.core import connect_sqlite, utc_now
from storage.connection import sqlite3

from .backends import EmbeddingBackend
from .constants import DENSE_DIMENSIONS, _pack
from .geometry import unpack_float32

logger = logging.getLogger("dual_pipeline.index_core.vector_staging")

STAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS staging_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS staged_dense_vector (
    retrieval_text_hash TEXT PRIMARY KEY,
    dimensions INTEGER NOT NULL CHECK (dimensions=1024),
    vector BLOB NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS staged_sparse_weight (
    retrieval_text_hash TEXT NOT NULL,
    token TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (retrieval_text_hash, token)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_staged_sparse_token
    ON staged_sparse_weight(token, retrieval_text_hash);

CREATE TABLE IF NOT EXISTS staged_colbert_token (
    retrieval_text_hash TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    token TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    vector BLOB NOT NULL,
    PRIMARY KEY (retrieval_text_hash, position)
) WITHOUT ROWID;
"""


def init_vector_staging_db(
    path: Union[str, Path],
    backend: EmbeddingBackend,
    *,
    colbert_dimensions: int = 1024,
) -> None:
    """Initializes a content-addressed SQLite vector staging store."""
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(resolved)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(STAGING_SCHEMA)

        metadata = {
            "schema_version": "1.0.0",
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "encoding_fingerprint_digest": getattr(backend, "encoding_fingerprint_digest", None) or "not-applicable",
            "dense_dimensions": str(DENSE_DIMENSIONS),
            "colbert_dimensions": str(colbert_dimensions),
            "created_at": utc_now(),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO staging_metadata (key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        conn.commit()
    finally:
        conn.close()


def inspect_staging_store(
    staging_path: Optional[Union[str, Path]],
    backend: EmbeddingBackend,
) -> Tuple[bool, Set[str], Optional[int]]:
    """Inspects a staging store to determine eligibility and discover available retrieval hashes.

    Returns:
        (is_eligible, available_text_hashes, colbert_dimensions)
    """
    if staging_path is None:
        return False, set(), None
    resolved = Path(staging_path).expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        return False, set(), None

    try:
        conn = connect_sqlite(resolved, readonly=True)
        try:
            conn.row_factory = sqlite3.Row
            tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='staging_metadata'"
            ).fetchone()
            if not tbl:
                return False, set(), None

            meta_rows = conn.execute("SELECT key, value FROM staging_metadata").fetchall()
            meta = {r["key"]: r["value"] for r in meta_rows}

            if (
                meta.get("embedding_backend") != backend.backend_id
                or meta.get("model_id") != backend.model_id
                or meta.get("model_revision") != backend.model_revision
            ):
                logger.info(
                    "[VECTOR-STAGING] Staging store model mismatch: "
                    f"stored=({meta.get('model_id')}:{meta.get('model_revision')}) vs "
                    f"current=({backend.model_id}:{backend.model_revision})."
                )
                return False, set(), None

            prior_fingerprint = meta.get("encoding_fingerprint_digest")
            expected_fingerprint = getattr(backend, "encoding_fingerprint_digest", None)
            if (
                prior_fingerprint is not None
                and expected_fingerprint is not None
                and prior_fingerprint != "not-applicable"
                and prior_fingerprint != expected_fingerprint
            ):
                logger.info(
                    f"[VECTOR-STAGING] Staging store fingerprint mismatch: "
                    f"stored={prior_fingerprint} vs current={expected_fingerprint}."
                )
                return False, set(), None

            dense_dim = int(meta.get("dense_dimensions", 1024))
            if dense_dim != DENSE_DIMENSIONS:
                return False, set(), None

            colbert_dim = int(meta.get("colbert_dimensions", 1024))
            hashes = {
                r[0]
                for r in conn.execute(
                    "SELECT retrieval_text_hash FROM staged_dense_vector"
                ).fetchall()
            }
            return True, hashes, colbert_dim
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(f"[VECTOR-STAGING] Failed to inspect staging store at '{resolved}': {exc}")
        return False, set(), None


def _validate_staged_payload(
    text_hash: str,
    encoded: Dict[str, Any],
) -> None:
    dense = encoded.get("dense")
    if not dense or len(dense) != DENSE_DIMENSIONS or any(not math.isfinite(v) for v in dense):
        raise ValueError(f"Dense vector length is invalid for {text_hash}")

    v_arr = np.array(dense, dtype=np.float32)
    norm = float(np.linalg.norm(v_arr))
    if norm < 1e-4 or norm < 0.90 or norm > 1.10:
        raise ValueError(f"Dense vector norm {norm} out of bounds [0.90, 1.10] for {text_hash}")

    colbert = encoded.get("colbert")
    colbert_tokens = encoded.get("colbert_tokens")
    if not colbert or not colbert_tokens or len(colbert_tokens) != len(colbert):
        raise ValueError(f"ColBERT tokens/vectors missing or length mismatch for {text_hash}")

    record_dimensions = {len(v) for v in colbert}
    if len(record_dimensions) != 1 or 0 in record_dimensions or any(
        not math.isfinite(v) for vec in colbert for v in vec
    ):
        raise ValueError(f"ColBERT token/vector dimensions invalid for {text_hash}")


def stage_encoded_vectors(
    staging_path: Union[str, Path],
    backend: EmbeddingBackend,
    records: Sequence[Dict[str, Any]],
    encoded_vectors: Sequence[Dict[str, Any]],
    *,
    colbert_dimensions: int = 1024,
) -> int:
    """Inserts pre-encoded dense, sparse, and ColBERT vectors into the staging store."""
    if len(records) != len(encoded_vectors):
        raise ValueError("records and encoded_vectors length mismatch")
    if not records:
        return 0

    resolved = Path(staging_path).expanduser().resolve()
    if not resolved.exists():
        init_vector_staging_db(resolved, backend, colbert_dimensions=colbert_dimensions)

    from .staging_bulk_ops import bulk_insert_staged_vectors
    return bulk_insert_staged_vectors(resolved, records, encoded_vectors, _validate_staged_payload)


def encode_and_stage_records(
    staging_path: Union[str, Path],
    backend: EmbeddingBackend,
    records: Sequence[Dict[str, Any]],
    *,
    host_chunk_size: Optional[int] = None,
    inference_batch_size: Optional[int] = None,
    colbert_dimensions: int = 1024,
) -> int:
    """Encodes records via backend and stages them into the content-addressed staging store."""
    if not records:
        return 0

    from .differential import _encode_delta_chunk, _resolve_delta_chunk_sizes

    effective_host, effective_inf = _resolve_delta_chunk_sizes(backend, host_chunk_size, inference_batch_size)
    total_staged = 0

    resolved = Path(staging_path).expanduser().resolve()
    if not resolved.exists():
        init_vector_staging_db(resolved, backend, colbert_dimensions=colbert_dimensions)

    records_list = list(records)
    for offset in range(0, len(records_list), effective_host):
        chunk = records_list[offset : offset + effective_host]
        encoded_chunk = _encode_delta_chunk(backend, chunk, effective_inf)
        staged = stage_encoded_vectors(
            resolved,
            backend,
            chunk,
            encoded_chunk,
            colbert_dimensions=colbert_dimensions,
        )
        total_staged += staged
        del encoded_chunk

    return total_staged


def copy_staged_vectors(
    connection: sqlite3.Connection,
    staging_path: Union[str, Path],
    staged_id_map: Dict[str, str],
    *,
    db_alias: str = "staging_db",
) -> int:
    """Attaches staging database and bulk-copies dense, sparse, and ColBERT vectors."""
    if not staged_id_map:
        return 0

    resolved = str(Path(staging_path).expanduser().resolve())
    attached = False
    attach_sql = f"ATTACH DATABASE ? AS {db_alias}"
    del_map_sql = "DELETE FROM _staged_vector_map"
    try:
        connection.execute(attach_sql, (resolved,))
        attached = True

        connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _staged_vector_map (record_id TEXT PRIMARY KEY, retrieval_text_hash TEXT NOT NULL)"
        )
        connection.execute(del_map_sql)
        connection.executemany(
            "INSERT INTO _staged_vector_map (record_id, retrieval_text_hash) VALUES (?, ?)",
            list(staged_id_map.items()),
        )

        connection.execute(
            f"""
            INSERT INTO dense_vector
            SELECT m.record_id, s.dimensions, s.vector
            FROM {db_alias}.staged_dense_vector s
            JOIN _staged_vector_map m ON s.retrieval_text_hash = m.retrieval_text_hash
            """
        )

        connection.execute(
            f"""
            INSERT INTO sparse_weight
            SELECT m.record_id, s.token, s.weight
            FROM {db_alias}.staged_sparse_weight s
            JOIN _staged_vector_map m ON s.retrieval_text_hash = m.retrieval_text_hash
            """
        )

        connection.execute(
            f"""
            INSERT INTO colbert_token
            SELECT m.record_id, s.position, s.token, s.dimensions, s.vector
            FROM {db_alias}.staged_colbert_token s
            JOIN _staged_vector_map m ON s.retrieval_text_hash = m.retrieval_text_hash
            """
        )

        copied_dense = connection.execute(
            """
            SELECT d.record_id, d.dimensions, d.vector
            FROM dense_vector d
            JOIN _staged_vector_map m ON d.record_id = m.record_id
            """
        ).fetchall()

        if len(copied_dense) != len(staged_id_map):
            raise RuntimeError(
                f"Staged vector copy incomplete: expected {len(staged_id_map)}, got {len(copied_dense)}"
            )

        for row in copied_dense:
            rid, dim, blob = row[0], row[1], bytes(row[2])
            if dim != DENSE_DIMENSIONS:
                raise RuntimeError(f"Staged vector dimension mismatch for {rid}: {dim} != {DENSE_DIMENSIONS}")
            v = unpack_float32(blob)
            if v is None or np.isnan(v).any() or np.isinf(v).any():
                raise RuntimeError(f"Staged vector contains NaN or Inf for {rid}")
            norm = float(np.linalg.norm(v))
            if norm < 1e-4 or norm < 0.90 or norm > 1.10:
                raise RuntimeError(f"Staged vector violates norm bounds for {rid}: norm={norm}")

        connection.execute("DROP TABLE IF EXISTS _staged_vector_map")
        return len(staged_id_map)
    finally:
        if attached:
            try:
                detach_sql = f"DETACH DATABASE {db_alias}"
                connection.execute(detach_sql)
            except Exception as detach_err:
                logger.debug(f"[VECTOR-STAGING] Detach database notice: {detach_err}")


def apply_staged_vectors(
    connection: sqlite3.Connection,
    backend: EmbeddingBackend,
    delta_records: List[Dict[str, Any]],
    staged_vector_paths: Sequence[Union[str, Path]],
    colbert_dim: Optional[int],
) -> Tuple[List[Dict[str, Any]], int, Optional[int]]:
    """Transfers pre-computed vectors from eligible staging stores for matching delta records."""
    staged_count = 0
    remaining_records = list(delta_records)
    active_colbert_dim = colbert_dim

    for idx, staging_path in enumerate(staged_vector_paths):
        if not remaining_records:
            break
        eligible, available_hashes, stg_colbert_dim = inspect_staging_store(
            staging_path, backend
        )
        if not eligible or not available_hashes:
            continue

        matched_records = [
            r for r in remaining_records if r.get("retrieval_text_hash") in available_hashes
        ]
        if not matched_records:
            continue

        staged_id_map = {
            r["record_id"]: r["retrieval_text_hash"] for r in matched_records
        }
        db_alias = f"staging_db_{idx}"
        copied = copy_staged_vectors(
            connection, staging_path, staged_id_map, db_alias=db_alias
        )
        staged_count += copied
        if active_colbert_dim is None and stg_colbert_dim is not None:
            active_colbert_dim = stg_colbert_dim
        matched_rids = {r["record_id"] for r in matched_records}
        remaining_records = [r for r in remaining_records if r["record_id"] not in matched_rids]
        logger.info(
            f"[VECTOR-STAGING] Attached and reused {copied} staged vectors from '{staging_path}'. "
            f"Remaining delta records to encode: {len(remaining_records)}"
        )

    return remaining_records, staged_count, active_colbert_dim
