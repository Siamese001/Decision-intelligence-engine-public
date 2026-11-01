"""
tools/tier2_dedup_core/search_fts.py

Search document synchronization, duplicate edge supersession, and FTS index rebuild.
"""
from __future__ import annotations

from typing import Any

from tools.tier2_common import utc_now


def execute_search_and_fts_rebuild(conn: Any) -> dict:
    """Synchronize search documents and rebuild FTS."""
    stats = {
        "search_docs_deactivated": 0,
        "search_docs_synced_historical": 0,
        "duplicate_edges_superseded": 0,
        "fts_entries_rebuilt": 0,
    }

    ts = utc_now()
    conn.execute("PRAGMA foreign_keys=OFF;")

    # 1. Deactivate search docs for retired nodes
    retired_nodes = conn.execute("SELECT node_id FROM node WHERE active = 0").fetchall()
    for r in retired_nodes:
        updated = conn.execute(
            """UPDATE search_document
               SET retrieval_eligible = 0, canonical_status = 'historical', currentness_state = 'historical'
               WHERE object_type = 'node' AND object_id = ? AND retrieval_eligible = 1""",
            (r["node_id"],),
        ).rowcount
        stats["search_docs_deactivated"] += updated

    # 2. Count search docs synced to historical
    synced_historical = conn.execute(
        """SELECT COUNT(*) FROM search_document
           WHERE canonical_status = 'historical' AND currentness_state = 'historical' AND retrieval_eligible = 0"""
    ).fetchone()[0]
    stats["search_docs_synced_historical"] = synced_historical

    # 3. Supersede duplicate edges created by merges
    dup_groups = conn.execute(
        """SELECT from_node_id, edge_type, to_node_id, COUNT(*) AS cnt, GROUP_CONCAT(edge_id) AS edge_ids
           FROM edge WHERE superseded_by IS NULL
           GROUP BY from_node_id, edge_type, to_node_id HAVING cnt > 1"""
    ).fetchall()

    for group in dup_groups:
        edge_ids = group["edge_ids"].split(",")
        survivor = edge_ids[0]
        for dup_id in edge_ids[1:]:
            conn.execute(
                "UPDATE edge SET superseded_by = ?, updated_at = ? WHERE edge_id = ?",
                (survivor, ts, dup_id),
            )
            stats["duplicate_edges_superseded"] += 1

    # 4. Rebuild FTS index
    try:
        conn.execute("INSERT INTO search_fts(search_fts) VALUES('rebuild');")
        stats["fts_entries_rebuilt"] = 1
    except Exception as e:
        print(f"  ⚠ FTS rebuild skipped: {e}")

    conn.execute("PRAGMA foreign_keys=ON;")
    return stats
