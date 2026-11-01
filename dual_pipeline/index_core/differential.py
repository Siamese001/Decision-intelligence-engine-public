"""Differential sidecar inheritance and incremental vector population.

Transfers verified dense, sparse, and ColBERT vectors directly from the prior
active sidecar for unchanged retrieval records via zero-copy SQLite attachment,
restricting deep learning inference strictly to net-new or modified records.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from config.credentials import CredentialManager
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import connect_sqlite
from storage.connection import sqlite3

from .backends import EmbeddingBackend
from .constants import DENSE_DIMENSIONS, _pack

logger = logging.getLogger("dual_pipeline.index_core.differential")


def _is_compatible_encoding_fingerprint(prior_fingerprint: Optional[str], backend: EmbeddingBackend) -> bool:
    expected_fingerprint = getattr(backend, "encoding_fingerprint_digest", None)
    if not prior_fingerprint or not expected_fingerprint or prior_fingerprint == "not-applicable":
        return True
    if prior_fingerprint == expected_fingerprint:
        return True
    base_fp = getattr(backend, "encoding_fingerprint", None)
    if not isinstance(base_fp, dict):
        return False
    from dual_pipeline.core import digest

    return any(
        digest({**base_fp, "device": dev, "precision": prec}) == prior_fingerprint
        for dev in ("mps", "cpu", "cuda")
        for prec in ("float16", "float32")
    )


def inspect_prior_sidecar(
    prior_path: Optional[Path],
    backend: EmbeddingBackend,
) -> Tuple[bool, Dict[str, str], Optional[int]]:
    """Inspects a prior sidecar to determine eligibility for vector inheritance.

    Returns:
        (is_eligible, prior_record_hashes, colbert_dimensions)
    """
    if prior_path is None:
        return False, {}, None

    resolved = Path(prior_path).expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        return False, {}, None

    try:
        conn = connect_sqlite(resolved, readonly=True)
        try:
            conn.row_factory = sqlite3.Row
            # 1. Verify metadata compatibility
            meta_rows = conn.execute("SELECT key, value FROM metadata").fetchall()
            meta = {r["key"]: r["value"] for r in meta_rows}

            expected_backend = backend.backend_id
            expected_model = backend.model_id
            expected_rev = backend.model_revision

            if (
                meta.get("embedding_backend") != expected_backend
                or meta.get("model_id") != expected_model
                or meta.get("model_revision") != expected_rev
            ):
                logger.info(
                    "[DIFFERENTIAL-SIDECAR] Prior sidecar model mismatch: "
                    f"prior=({meta.get('model_id')}:{meta.get('model_revision')}) vs "
                    f"current=({expected_model}:{expected_rev}). Falling back to full encoding."
                )
                return False, {}, None

            prior_fingerprint = meta.get("encoding_fingerprint_digest")
            expected_fingerprint = getattr(backend, "encoding_fingerprint_digest", None)
            if not _is_compatible_encoding_fingerprint(prior_fingerprint, backend):
                logger.info(
                    "[DIFFERENTIAL-SIDECAR] Prior sidecar encoding fingerprint mismatch: "
                    f"prior={prior_fingerprint} vs current={expected_fingerprint}. "
                    "Falling back to full encoding."
                )
                return False, {}, None

            colbert_dim = int(meta.get("colbert_dimensions", 1024))
            dense_dim = int(meta.get("dense_dimensions", 1024))
            if dense_dim != DENSE_DIMENSIONS:
                return False, {}, None

            # 2. Extract record_id -> retrieval_text_hash mapping
            records = conn.execute(
                "SELECT record_id, retrieval_text_hash FROM retrieval_record"
            ).fetchall()
            prior_hashes = {r["record_id"]: r["retrieval_text_hash"] for r in records}
            # Also store reverse mapping: retrieval_text_hash -> first prior record_id
            for r in records:
                th = r["retrieval_text_hash"]
                if th and th not in prior_hashes:
                    prior_hashes[th] = r["record_id"]
            return True, prior_hashes, colbert_dim
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            f"[DIFFERENTIAL-SIDECAR] Failed to inspect prior sidecar at '{resolved}': {exc}. "
            "Falling back to full encoding."
        )
        return False, {}, None


def _verify_copied_dense_vectors(
    connection: sqlite3.Connection,
    *,
    skip_reused_norm_recheck: bool = True,
) -> None:
    if skip_reused_norm_recheck:
        bad_shape = connection.execute(
            """
            SELECT d.record_id, d.dimensions, length(d.vector)
            FROM dense_vector d
            JOIN _reusable_sidecar_ids s ON d.record_id = s.record_id
            WHERE d.dimensions != ? OR length(d.vector) != ?
            LIMIT 1
            """,
            (DENSE_DIMENSIONS, DENSE_DIMENSIONS * 4),
        ).fetchone()
        if bad_shape is not None:
            rid, dim, _ = bad_shape[0], bad_shape[1], bad_shape[2]
            raise RuntimeError(
                f"Differential vector reused dimension mismatch for {rid}: {dim} != {DENSE_DIMENSIONS}"
            )
        return

    copied_dense = connection.execute(
        """
        SELECT d.record_id, d.dimensions, d.vector
        FROM dense_vector d
        JOIN _reusable_sidecar_ids s ON d.record_id = s.record_id
        """
    ).fetchall()
    import numpy as np

    from .geometry import unpack_float32

    for row in copied_dense:
        rid, dim, blob = row[0], row[1], bytes(row[2])
        if dim != DENSE_DIMENSIONS:
            raise RuntimeError(
                f"Differential vector reused dimension mismatch for {rid}: {dim} != {DENSE_DIMENSIONS}"
            )
        v = unpack_float32(blob)
        if v is None or np.isnan(v).any() or np.isinf(v).any():
            raise RuntimeError(f"Differential vector reused vector contains NaN or Inf for {rid}")
        norm = float(np.linalg.norm(v))
        if norm < 1e-4 or norm < 0.90 or norm > 1.10:
            raise RuntimeError(
                f"Differential vector reused vector violates norm bounds for {rid}: norm={norm}"
            )


def copy_reusable_vectors(
    connection: sqlite3.Connection,
    prior_path: Path,
    reusable_record_ids: List[str],
    *,
    skip_reused_norm_recheck: bool = True,
) -> int:
    """Attaches prior sidecar database and bulk copies dense, sparse, and ColBERT vectors."""
    if not reusable_record_ids:
        return 0
    from dual_pipeline.l0.ingestion_timing import timed_child_span
    resolved_prior = str(Path(prior_path).resolve())
    attached = False
    with timed_child_span("sidecar_copy", {"reused_count": len(reusable_record_ids)}):
        try:
            attach_stmt = "ATTACH DATABASE ? AS prior_sidecar_db"
            connection.execute(attach_stmt, (resolved_prior,))
            attached = True
            create_temp_stmt = "CREATE TEMP TABLE IF NOT EXISTS _reusable_sidecar_ids (record_id TEXT PRIMARY KEY)"
            connection.execute(create_temp_stmt)
            clear_temp_stmt = "DELETE FROM _reusable_sidecar_ids"
            connection.execute(clear_temp_stmt)
            insert_ids_stmt = "INSERT INTO _reusable_sidecar_ids VALUES (?)"
            connection.executemany(insert_ids_stmt, [(rid,) for rid in reusable_record_ids])
            dense_stmt = "INSERT INTO dense_vector SELECT d.record_id, d.dimensions, d.vector FROM prior_sidecar_db.dense_vector d JOIN _reusable_sidecar_ids r ON d.record_id = r.record_id"
            connection.execute(dense_stmt)
            sparse_stmt = "INSERT INTO sparse_weight SELECT s.record_id, s.token, s.weight FROM prior_sidecar_db.sparse_weight s JOIN _reusable_sidecar_ids r ON s.record_id = r.record_id"
            connection.execute(sparse_stmt)
            colbert_stmt = "INSERT INTO colbert_token SELECT c.record_id, (ROW_NUMBER() OVER (PARTITION BY c.record_id ORDER BY c.position)) - 1, c.token, c.dimensions, c.vector FROM prior_sidecar_db.colbert_token c JOIN _reusable_sidecar_ids r ON c.record_id = r.record_id"
            connection.execute(colbert_stmt)
            mismatched_hashes = connection.execute(
                "SELECT r.record_id FROM retrieval_record r JOIN prior_sidecar_db.retrieval_record p ON r.record_id = p.record_id JOIN _reusable_sidecar_ids s ON r.record_id = s.record_id WHERE r.retrieval_text_hash != p.retrieval_text_hash"
            ).fetchall()
            if mismatched_hashes:
                raise RuntimeError(f"Differential vector text hash mismatch for reused records: {[row[0] for row in mismatched_hashes]}")
            _verify_copied_dense_vectors(connection, skip_reused_norm_recheck=skip_reused_norm_recheck)
            connection.execute("DROP TABLE IF EXISTS _reusable_sidecar_ids")
            return len(reusable_record_ids)
        finally:
            if attached:
                try:
                    connection.execute("DETACH DATABASE prior_sidecar_db")
                except Exception as detach_err:
                    logger.debug(f"[DIFFERENTIAL-SIDECAR] Detach database notice: {detach_err}")


def _resolve_delta_chunk_sizes(
    backend: EmbeddingBackend, host_chunk_size: Optional[int], inference_batch_size: Optional[int]
) -> Tuple[int, int]:
    default_host = 64
    effective_host = max(1, host_chunk_size) if host_chunk_size is not None else max(
        1, int(CredentialManager.get_credential("NEE_HOST_CHUNK_SIZE", str(default_host)))
    )
    default_inf = 32 if getattr(backend, "execution_device", "cpu") in ("mps", "cuda") else 16
    effective_inf = max(1, inference_batch_size) if inference_batch_size is not None else max(
        1,
        int(
            CredentialManager.get_credential(
                "NEE_EMBEDDING_INFERENCE_BATCH_SIZE",
                CredentialManager.get_credential("NEE_EMBEDDING_BATCH_SIZE", str(default_inf)),
            )
        ),
    )
    return effective_host, effective_inf


def _encode_delta_chunk(
    backend: EmbeddingBackend, chunk: List[Dict[str, Any]], effective_inference_batch: int
) -> List[Dict[str, Any]]:
    batch_encoder = getattr(backend, "encode_many", None)
    if callable(batch_encoder) and getattr(backend, "supports_batch_encoding", False):
        try:
            res = batch_encoder([r["retrieval_text"] for r in chunk], batch_size=effective_inference_batch)
        except TypeError:
            res = batch_encoder([r["retrieval_text"] for r in chunk])
        if len(res) != len(chunk):
            raise RuntimeError("Embedding backend returned inconsistent batch size")
        return res
    return [backend.encode(r["retrieval_text"]) for r in chunk]


def _validate_and_insert_encoded_record(
    connection: sqlite3.Connection,
    record: Dict[str, Any],
    encoded: Dict[str, Any],
    active_colbert_dim: Optional[int],
) -> int:
    dense = encoded["dense"]
    if len(dense) != DENSE_DIMENSIONS or any(not math.isfinite(value) for value in dense):
        raise ValueError(f"Dense vector length is {len(dense)}, expected {DENSE_DIMENSIONS} finite values")

    record_dimensions = {len(v) for v in encoded["colbert"]}
    if (
        not encoded["colbert"]
        or len(encoded["colbert_tokens"]) != len(encoded["colbert"])
        or len(record_dimensions) != 1
        or 0 in record_dimensions
        or any(not math.isfinite(v) for vec in encoded["colbert"] for v in vec)
    ):
        raise ValueError("ColBERT token/vector cardinality or dimensions are invalid")

    cur_dim = next(iter(record_dimensions))
    if active_colbert_dim is not None and cur_dim != active_colbert_dim:
        raise ValueError("ColBERT vector dimensions changed within one generation")

    rid = record["record_id"]
    connection.executemany("INSERT INTO dense_vector VALUES (?,?,?)", [(rid, DENSE_DIMENSIONS, _pack(dense))])
    connection.executemany(
        "INSERT INTO sparse_weight VALUES (?,?,?)",
        [(rid, token, float(weight)) for token, weight in sorted(encoded["sparse"].items())],
    )
    connection.executemany(
        "INSERT INTO colbert_token VALUES (?,?,?,?,?)",
        [(rid, idx, encoded["colbert_tokens"][idx], len(vec), _pack(vec)) for idx, vec in enumerate(encoded["colbert"])],
    )
    return cur_dim


def encode_and_insert_delta_records(
    connection: sqlite3.Connection,
    delta_records: List[Dict[str, Any]],
    backend: EmbeddingBackend,
    colbert_dimensions: Optional[int],
    *,
    host_chunk_size: Optional[int] = None,
    inference_batch_size: Optional[int] = None,
) -> Tuple[int, Optional[int]]:
    """Streams validated output windows into SQLite transactions without accumulating full corpora."""
    if not delta_records:
        return 0, colbert_dimensions

    effective_host, effective_inf = _resolve_delta_chunk_sizes(backend, host_chunk_size, inference_batch_size)
    active_colbert_dim = colbert_dimensions

    from dual_pipeline.l0.ingestion_timing import timed_child_span
    from .content_differential import bulk_insert_chunk_records
    with timed_child_span("changed_record_encoding", {"delta_count": len(delta_records)}):
        for offset in range(0, len(delta_records), effective_host):
            chunk = delta_records[offset : offset + effective_host]
            encoded_chunk = _encode_delta_chunk(backend, chunk, effective_inf)
            active_colbert_dim = bulk_insert_chunk_records(
                connection, chunk, encoded_chunk, active_colbert_dim
            )
            del encoded_chunk

    return len(delta_records), active_colbert_dim


class PopulateSidecarResult(tuple):
    """4-tuple subclass providing backward compatibility with staged vector telemetry."""
    staged_records: int
    reused_record_ids: frozenset[str]

    def __new__(
        cls,
        total: int,
        colbert_dim: Optional[int],
        reused: int,
        delta: int,
        staged: int = 0,
        reused_record_ids: Optional[Sequence[str]] = None,
    ) -> PopulateSidecarResult:
        instance = super().__new__(cls, (total, colbert_dim, reused, delta))
        instance.staged_records = staged
        instance.reused_record_ids = frozenset(reused_record_ids or ())
        return instance


def validate_record_artifact_if_needed(
    artifact: Dict[str, Any],
    record_id: str,
    reused_record_ids: frozenset[str] = frozenset(),
) -> None:
    if record_id not in reused_record_ids:
        validate_artifact(artifact)


def populate_sidecar_vectors(
    connection: sqlite3.Connection,
    records: List[Dict[str, Any]],
    backend: EmbeddingBackend,
    prior_sidecar_path: Optional[Path] = None,
    *,
    staged_vector_paths: Optional[Sequence[Union[str, Path]]] = None,
    host_chunk_size: Optional[int] = None,
    inference_batch_size: Optional[int] = None,
    skip_reused_norm_recheck: bool = True,
) -> PopulateSidecarResult:
    """Orchestrates differential vector population for sidecar SQLite database."""
    eligible, prior_hashes, prior_colbert_dim = inspect_prior_sidecar(prior_sidecar_path, backend)
    from .content_differential import copy_vectors_by_text_hash, partition_reusable_records

    if eligible and prior_sidecar_path is not None:
        reusable_records, content_reusable_pairs, delta_records = partition_reusable_records(records, prior_hashes)
    else:
        reusable_records, content_reusable_pairs, delta_records = [], [], list(records)
    colbert_dim, reused_count, reused_ids_list = prior_colbert_dim, 0, []
    if reusable_records and prior_sidecar_path is not None:
        reusable_ids = [r["record_id"] for r in reusable_records]
        reused_count = copy_reusable_vectors(
            connection, prior_sidecar_path, reusable_ids, skip_reused_norm_recheck=skip_reused_norm_recheck
        )
        reused_ids_list.extend(reusable_ids)
    if content_reusable_pairs and prior_sidecar_path is not None:
        reused_count += copy_vectors_by_text_hash(connection, prior_sidecar_path, content_reusable_pairs)
        reused_ids_list.extend(r["record_id"] if isinstance(r, dict) else r for r, _ in content_reusable_pairs)
    if reused_count > 0:
        logger.info(
            f"[DIFFERENTIAL-SIDECAR] Reused {reused_count} vectors from prior sidecar. "
            f"Net-new delta records to encode: {len(delta_records)}"
        )
    staged_count = 0
    if staged_vector_paths and delta_records:
        from .vector_staging import apply_staged_vectors

        delta_records, staged_count, colbert_dim = apply_staged_vectors(
            connection, backend, delta_records, staged_vector_paths, colbert_dim
        )
    delta_count, colbert_dim = encode_and_insert_delta_records(
        connection, delta_records, backend, colbert_dim,
        host_chunk_size=host_chunk_size, inference_batch_size=inference_batch_size,
    )
    return PopulateSidecarResult(
        len(records), colbert_dim, reused_count, delta_count,
        staged=staged_count, reused_record_ids=reused_ids_list,
    )


def get_effective_embedding_telemetry(
    backend: EmbeddingBackend,
    total_records: int,
    reused_records: int,
    delta_records: int,
    cache_hits: int,
    host_chunk_size: Optional[int] = None,
    inference_batch_size: Optional[int] = None,
    *,
    staged_records: int = 0,
) -> Dict[str, Any]:
    effective_host, effective_inf = _resolve_delta_chunk_sizes(backend, host_chunk_size, inference_batch_size)
    effective_device = getattr(backend, "execution_device", "cpu")
    effective_precision = "float16" if getattr(backend, "use_fp16", False) else "float32"
    return {
        "effective_device": str(effective_device),
        "effective_precision": effective_precision,
        "effective_inference_batch": effective_inf,
        "effective_host_chunk": effective_host,
        "total_records": total_records,
        "reused_records": reused_records,
        "delta_records": delta_records,
        "staged_records": staged_records,
        "cache_hits": cache_hits,
        "inferred_from_defaults": False,
    }


