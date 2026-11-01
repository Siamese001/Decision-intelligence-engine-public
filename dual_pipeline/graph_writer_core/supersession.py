"""Governed edge supersession database operations.

Executes atomic edge supersession within the governed writer boundary.
"""

from __future__ import annotations

from typing import Any

from storage.database import sqlite3


def apply_edge_supersession(
    conn: sqlite3.Connection,
    target_edge_id: str,
    superseded_by_val: str,
    now_iso: str,
    resolution_note: str,
) -> None:
    """Updates edge superseded_by and marks related validation_queue rows resolved."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE edge
           SET superseded_by = ?,
               updated_at = ?
         WHERE edge_id = ?
           AND superseded_by IS NULL
        """,
        (superseded_by_val, now_iso, target_edge_id),
    )
    if cur.rowcount == 0:
        raise ValueError(
            f"Target edge '{target_edge_id}' not found or already superseded."
        )

    cur.execute(
        """
        UPDATE validation_queue
           SET status = 'resolved',
               resolution = ?,
               resolved_at = ?
         WHERE edge_id = ?
           AND status = 'open'
        """,
        (resolution_note, now_iso, target_edge_id),
    )
