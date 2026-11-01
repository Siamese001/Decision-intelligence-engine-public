"""Bulk transaction helpers for vector staging database."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, Union

from dual_pipeline.core import connect_sqlite
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, _pack


def bulk_insert_staged_vectors(
    resolved_path: Path,
    records: Sequence[Dict[str, Any]],
    encoded_vectors: Sequence[Dict[str, Any]],
    validator_func: Any,
) -> int:
    """Inserts batch of pre-encoded dense, sparse, and ColBERT vectors in single transaction."""
    if not records:
        return 0

    dense_rows: List[Tuple[str, int, bytes]] = []
    sparse_rows: List[Tuple[str, str, float]] = []
    colbert_rows: List[Tuple[str, int, str, int, bytes]] = []
    hashes_to_delete: List[Tuple[str,]] = []

    for record, encoded in zip(records, encoded_vectors, strict=True):
        text_hash = record.get("retrieval_text_hash")
        if not text_hash:
            raise ValueError("Record missing retrieval_text_hash")

        validator_func(text_hash, encoded)
        hashes_to_delete.append((text_hash,))
        dense_rows.append((text_hash, DENSE_DIMENSIONS, _pack(encoded["dense"])))

        for token, weight in sorted(encoded["sparse"].items()):
            sparse_rows.append((text_hash, token, float(weight)))

        for idx, vec in enumerate(encoded["colbert"]):
            colbert_rows.append((text_hash, idx, encoded["colbert_tokens"][idx], len(vec), _pack(vec)))

    conn = connect_sqlite(resolved_path)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")

        conn.executemany("DELETE FROM staged_sparse_weight WHERE retrieval_text_hash = ?", hashes_to_delete)
        conn.executemany("DELETE FROM staged_colbert_token WHERE retrieval_text_hash = ?", hashes_to_delete)

        conn.executemany("INSERT OR REPLACE INTO staged_dense_vector VALUES (?,?,?)", dense_rows)
        conn.executemany("INSERT INTO staged_sparse_weight VALUES (?,?,?)", sparse_rows)
        conn.executemany("INSERT INTO staged_colbert_token VALUES (?,?,?,?,?)", colbert_rows)

        conn.commit()
        return len(records)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
