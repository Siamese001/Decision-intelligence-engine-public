"""
dual_pipeline/graph_maintenance_core/pruning.py

Retrieval index and vector sidecar pruning engine.
Removes or de-indexes historical/superseded assertions from search_document and sidecars.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

SIDECAR_TABLES = [
    "dense_vector",
    "sparse_weight",
    "colbert_token",
    "retrieval_record",
]


def prune_superseded_search_documents(
    conn: Any,
    prune_sidecars: bool = False,
    purge_deleted: bool = False,
) -> Dict[str, Any]:
    """
    Prunes superseded and non-retrievable assertions from search_document and vector sidecars.

    Returns itemized pruning report.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='search_document'")
    if cursor.fetchone() is None:
        return {"search_documents_pruned": 0, "sidecars_pruned": {}}

    # Find inactive search documents
    cursor.execute(
        """SELECT search_document_id
             FROM search_document
            WHERE retrieval_eligible = 0
               OR currentness_state IN ('historical', 'superseded')"""
    )
    inactive_ids = [r[0] for r in cursor.fetchall()]

    if not inactive_ids:
        return {"search_documents_pruned": 0, "sidecars_pruned": {}}

    sidecars_pruned: Dict[str, int] = {}
    if prune_sidecars:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {r[0] for r in cursor.fetchall()}

        for table in SIDECAR_TABLES:
            if table in existing_tables:
                placeholders = ",".join("?" for _ in inactive_ids)
                del_sql = f"DELETE FROM {table} WHERE search_document_id IN ({placeholders})"
                cursor.execute(del_sql, tuple(inactive_ids))
                sidecars_pruned[table] = cursor.rowcount

    docs_pruned = len(inactive_ids)
    if purge_deleted:
        placeholders = ",".join("?" for _ in inactive_ids)
        cursor.execute(f"DELETE FROM search_document WHERE search_document_id IN ({placeholders})", tuple(inactive_ids))
    else:
        cursor.execute(
            """UPDATE search_document
                  SET retrieval_eligible = 0,
                      currentness_state = 'historical'
                WHERE retrieval_eligible != 0
                  AND (currentness_state IN ('historical', 'superseded') OR assertion_id IN (
                      SELECT assertion_id FROM assertion WHERE currentness_state = 'historical'
                  ))"""
        )

    conn.commit()
    return {
        "search_documents_pruned": docs_pruned,
        "sidecars_pruned": sidecars_pruned,
    }
