"""Content-addressable vector carry-forward and chunk-level bulk insert utilities."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, _pack
from storage.connection import sqlite3

logger = logging.getLogger("dual_pipeline.index_core.content_differential")


def partition_reusable_records(
    records: Sequence[Dict[str, Any]],
    prior_hashes: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]], List[Dict[str, Any]]]:
    """Partitions input records into exact reusable, content reusable, and delta records."""
    reusable_records: List[Dict[str, Any]] = []
    content_reusable_pairs: List[Tuple[str, str]] = []
    delta_records: List[Dict[str, Any]] = []

    for r in records:
        rid = r["record_id"]
        th = r["retrieval_text_hash"]
        if rid in prior_hashes and prior_hashes[rid] == th:
            reusable_records.append(r)
        elif th in prior_hashes:
            content_reusable_pairs.append((rid, prior_hashes[th]))
        else:
            delta_records.append(r)
    return reusable_records, content_reusable_pairs, delta_records


def copy_vectors_by_text_hash(
    connection: sqlite3.Connection,
    prior_path: Path,
    target_records_with_prior_id: List[Tuple[str, str]],
) -> int:
    """Copies dense, sparse, and ColBERT vectors from prior sidecar mapped by text hash.

    Args:
        connection: Destination SQLite connection.
        prior_path: Path to prior sidecar SQLite file.
        target_records_with_prior_id: List of (target_record_id, prior_record_id).

    Returns:
        Number of successfully copied records.
    """
    if not target_records_with_prior_id:
        return 0

    resolved_prior = str(Path(prior_path).resolve())
    attached = False
    try:
        connection.execute("ATTACH DATABASE ? AS prior_content_db", (resolved_prior,))
        attached = True

        connection.execute("CREATE TEMP TABLE IF NOT EXISTS _content_map (target_id TEXT PRIMARY KEY, prior_id TEXT)")
        connection.execute("DELETE FROM _content_map")
        connection.executemany("INSERT INTO _content_map VALUES (?,?)", target_records_with_prior_id)
        connection.execute(
            "INSERT INTO dense_vector SELECT m.target_id, d.dimensions, d.vector FROM prior_content_db.dense_vector d JOIN _content_map m ON d.record_id = m.prior_id"
        )
        connection.execute(
            "INSERT INTO sparse_weight SELECT m.target_id, s.token, s.weight FROM prior_content_db.sparse_weight s JOIN _content_map m ON s.record_id = m.prior_id"
        )
        connection.execute(
            "INSERT INTO colbert_token SELECT m.target_id, (ROW_NUMBER() OVER (PARTITION BY m.target_id ORDER BY c.position)) - 1, c.token, c.dimensions, c.vector FROM prior_content_db.colbert_token c JOIN _content_map m ON c.record_id = m.prior_id"
        )
        connection.execute("DROP TABLE IF EXISTS _content_map")
        return len(target_records_with_prior_id)
    finally:
        if attached:
            try:
                connection.execute("DETACH DATABASE prior_content_db")
            except Exception:
                pass


def bulk_insert_chunk_records(
    connection: sqlite3.Connection,
    chunk: Sequence[Dict[str, Any]],
    encoded_chunk: Sequence[Dict[str, Any]],
    active_colbert_dim: Optional[int],
) -> int:
    """Accumulates records across an encoded chunk and executes bulk executemany transactions."""
    dense_rows: List[Tuple[str, int, bytes]] = []
    sparse_rows: List[Tuple[str, str, float]] = []
    colbert_rows: List[Tuple[str, int, str, int, bytes]] = []

    cur_colbert_dim = active_colbert_dim

    for record, encoded in zip(chunk, encoded_chunk, strict=True):
        rid = record["record_id"]
        dense = encoded["dense"]
        dense_rows.append((rid, DENSE_DIMENSIONS, _pack(dense)))

        for token, weight in sorted(encoded["sparse"].items()):
            sparse_rows.append((rid, token, float(weight)))

        colbert_list = encoded["colbert"]
        tokens = encoded["colbert_tokens"]
        if colbert_list:
            token_dim = len(colbert_list[0])
            if cur_colbert_dim is not None and token_dim != cur_colbert_dim:
                raise ValueError("ColBERT vector dimensions changed within one generation")
            cur_colbert_dim = token_dim
            for idx, vec in enumerate(colbert_list):
                colbert_rows.append((rid, idx, tokens[idx], len(vec), _pack(vec)))

    connection.executemany("INSERT INTO dense_vector VALUES (?,?,?)", dense_rows)
    connection.executemany("INSERT INTO sparse_weight VALUES (?,?,?)", sparse_rows)
    connection.executemany("INSERT INTO colbert_token VALUES (?,?,?,?,?)", colbert_rows)

    return cur_colbert_dim or 1024
