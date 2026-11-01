"""
dual_pipeline/uwg_core/universal_write_gateway_sidecar.py

Governed Universal Write Gateway helper for vector sidecar metadata synchronization.
"""
from __future__ import annotations

import logging
from pathlib import Path
from storage.connection import get_db_connection

logger = logging.getLogger(__name__)


def sync_sidecar_metadata_graph_hash(sidecar_path: Path, actual_hash: str) -> None:
    """Updates graph_sha256 and bound_graph_sha256 in vector sidecar metadata tables."""
    conn = get_db_connection(sidecar_path)
    try:
        tbl_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('metadata', 'sidecar_meta')"
        ).fetchall()
        tbl_names = {r[0] for r in tbl_rows}
        if "metadata" in tbl_names:
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('graph_sha256', ?)",
                (actual_hash,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('bound_graph_sha256', ?)",
                (actual_hash,),
            )
        if "sidecar_meta" in tbl_names:
            conn.execute(
                "INSERT OR REPLACE INTO sidecar_meta (key, value) VALUES ('graph_sha256', ?)",
                (actual_hash,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO sidecar_meta (key, value) VALUES ('bound_graph_sha256', ?)",
                (actual_hash,),
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
