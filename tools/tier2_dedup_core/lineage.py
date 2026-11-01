"""
tools/tier2_dedup_core/lineage.py

Edge lineage fixes linking unmigrated legacy edges to assertions.
"""
from __future__ import annotations

from typing import Any, Callable

from tools.tier2_common import utc_now


def execute_edge_lineage_fixes(
    conn: Any,
    audit_cb: Callable[..., None],
) -> int:
    """Link unmigrated edges to existing assertions."""
    unmigrated = conn.execute(
        """SELECT e.edge_id, e.from_node_id, e.edge_type, e.to_node_id
           FROM edge e
           WHERE e.superseded_by IS NULL
             AND e.edge_id NOT IN (SELECT edge_id FROM legacy_edge_assertion_map)"""
    ).fetchall()

    fixed_count = 0
    for edge in unmigrated:
        existing = conn.execute(
            """SELECT assertion_id FROM assertion
               WHERE subject_node_id = ? AND predicate = ? AND object_node_id = ?
               LIMIT 1""",
            (edge["from_node_id"], edge["edge_type"], edge["to_node_id"]),
        ).fetchone()

        if existing:
            conn.execute(
                """INSERT OR IGNORE INTO legacy_edge_assertion_map
                   (edge_id, assertion_id, migration_confidence, migration_state, created_at)
                   VALUES (?, ?, 0.95, 'structured', ?)""",
                (edge["edge_id"], existing["assertion_id"], utc_now()),
            )
            audit_cb(
                conn,
                "edge_lineage_fix",
                "edge",
                edge["edge_id"],
                {"migrated": False},
                {"migrated": True, "assertion_id": existing["assertion_id"]},
                "Tier2 lineage fix: linked edge to existing assertion",
            )
            fixed_count += 1

    return fixed_count
