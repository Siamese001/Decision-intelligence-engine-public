"""ColBERT token vector codec validation and decoding.

Provides unambiguous decoding and strict validation for ColBERT token vectors:
- Legacy float32 uncompressed vectors (dim * 4 bytes)
- Supported quantized int8 vectors with float32 scale (4 + dim bytes)
- Explicit rejection of ambiguous formats, truncated blobs, dimension mismatches,
  and non-finite (NaN / Inf) values.
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from storage.connection import sqlite3


def _check_dimensions(
    record_id: str, position: int, token_dim: Optional[int], meta_dim: Optional[int], strict: bool
) -> Optional[int]:
    dim = meta_dim if meta_dim is not None else token_dim
    if dim is None or dim <= 0:
        if strict:
            raise ValueError(
                f"Ambiguous or invalid ColBERT dimension ({dim}) for record '{record_id}' at pos {position}: "
                f"no valid dimension specified in token row or sidecar metadata"
            )
        return None
    if token_dim is not None and meta_dim is not None and token_dim != meta_dim:
        if strict:
            raise ValueError(
                f"ColBERT record '{record_id}' dimension mismatch at pos {position}: "
                f"token row declares {token_dim}, sidecar metadata requires {meta_dim}"
            )
        return None
    return dim


def _decode_vector_blob(
    record_id: str, position: int, dim: int, blob: bytes, strict: bool
) -> Optional[np.ndarray]:
    blob_len = len(blob)
    expected_float32_len = dim * 4
    expected_quantized_len = 4 + dim
    if blob_len == expected_float32_len:
        vec = np.frombuffer(blob, dtype="<f4").astype(np.float32)
    elif blob_len == expected_quantized_len:
        scale = struct.unpack("<f", blob[:4])[0]
        if not np.isfinite(scale):
            if strict:
                raise ValueError(
                    f"Non-finite quantization scale ({scale}) in ColBERT token for record '{record_id}' at pos {position}"
                )
            return None
        quantized = np.frombuffer(blob[4:], dtype=np.int8)
        vec = quantized.astype(np.float32) * scale
    else:
        if strict:
            raise ValueError(
                f"Ambiguous or invalid ColBERT vector codec for record '{record_id}' at pos {position}: "
                f"blob length {blob_len} matches neither float32 ({expected_float32_len} bytes) "
                f"nor quantized ({expected_quantized_len} bytes) for dimension {dim}"
            )
        return None

    if vec.shape != (dim,) or not np.all(np.isfinite(vec)):
        if strict:
            raise ValueError(
                f"Invalid decoded ColBERT token vector for record '{record_id}' at pos {position}: "
                f"shape={vec.shape} (expected ({dim},)), finite={np.all(np.isfinite(vec))}"
            )
        return None
    return vec


def validate_and_decode_colbert_token(
    record_id: str,
    position: int,
    token_dim: Optional[int],
    blob: bytes,
    meta_dim: Optional[int] = None,
    strict: bool = True,
) -> Optional[np.ndarray]:
    """Decodes and validates a single ColBERT token vector blob."""
    dim = _check_dimensions(record_id, position, token_dim, meta_dim, strict)
    if dim is None:
        return None
    return _decode_vector_blob(record_id, position, dim, blob, strict)


def _inspect_colbert_schema(conn: sqlite3.Connection) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    meta_colbert_dim: Optional[int] = None
    try:
        cur = conn.execute("SELECT value FROM metadata WHERE key = 'colbert_dimensions'")
        row = cur.fetchone()
        if row and row[0]:
            meta_colbert_dim = int(row[0])
    except Exception:
        pass

    dim_col, pos_col = "dimensions", "position"
    try:
        col_rows = conn.execute("PRAGMA table_info(colbert_token)").fetchall()
        col_names = {r[1] for r in col_rows}
        dim_col = "dimensions" if "dimensions" in col_names else ("dims" if "dims" in col_names else None)
        pos_col = "position" if "position" in col_names else ("pos" if "pos" in col_names else None)
    except Exception:
        pass
    return meta_colbert_dim, dim_col, pos_col


def _query_missing_tokens(
    conn: sqlite3.Connection, missing: List[str], dim_col: Optional[str], pos_col: Optional[str]
) -> Dict[str, List[Tuple[int, Optional[int], bytes]]]:
    placeholders = ",".join("?" for _ in missing)
    if dim_col and pos_col:
        query = f"SELECT record_id, {pos_col}, {dim_col}, vector FROM colbert_token WHERE record_id IN ({placeholders}) ORDER BY record_id, {pos_col}"
    elif dim_col:
        query = f"SELECT record_id, 0, {dim_col}, vector FROM colbert_token WHERE record_id IN ({placeholders}) ORDER BY record_id"
    else:
        query = f"SELECT record_id, 0, NULL, vector FROM colbert_token WHERE record_id IN ({placeholders}) ORDER BY record_id"

    rows_by_id: Dict[str, List[Tuple[int, Optional[int], bytes]]] = {rid: [] for rid in missing}
    for row in conn.execute(query, missing):
        r_id = row[0]
        pos = int(row[1]) if row[1] is not None else 0
        r_dim = int(row[2]) if row[2] is not None else None
        r_vec = row[3]
        if r_id in rows_by_id:
            rows_by_id[r_id].append((pos, r_dim, r_vec))
    return rows_by_id


def _decode_doc_matrix(
    rid: str,
    raw_rows: List[Tuple[int, Optional[int], bytes]],
    meta_colbert_dim: Optional[int],
    strict: bool,
) -> np.ndarray:
    if not raw_rows:
        return np.empty((0, meta_colbert_dim or 32), dtype=np.float32)

    token_vecs: List[np.ndarray] = []
    for pos, r_dim, r_vec in raw_rows:
        vec = validate_and_decode_colbert_token(
            record_id=rid,
            position=pos,
            token_dim=r_dim,
            blob=r_vec,
            meta_dim=meta_colbert_dim,
            strict=strict,
        )
        if vec is not None:
            token_vecs.append(vec)

    if not token_vecs:
        return np.empty((0, meta_colbert_dim or 32), dtype=np.float32)

    doc_mat = np.stack(token_vecs, axis=0).astype(np.float32)
    if not np.all(np.isfinite(doc_mat)):
        if strict:
            raise ValueError(f"Non-finite values detected in ColBERT matrix for record '{rid}'")
        return np.empty((0, meta_colbert_dim or 32), dtype=np.float32)
    return doc_mat


def fetch_and_validate_colbert_tokens(
    conn: sqlite3.Connection,
    target_ids: List[str],
    cache: Dict[str, np.ndarray],
    strict: bool = True,
) -> None:
    """Fetches, validates, and caches ColBERT token matrices for missing records."""
    missing = [rid for rid in target_ids if rid not in cache]
    if not missing:
        return

    meta_colbert_dim, dim_col, pos_col = _inspect_colbert_schema(conn)
    rows_by_id = _query_missing_tokens(conn, missing, dim_col, pos_col)
    for rid in missing:
        cache[rid] = _decode_doc_matrix(rid, rows_by_id.get(rid, []), meta_colbert_dim, strict)
