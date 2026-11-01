"""
tools/tier2_dedup_core/predicates.py

Predicate consolidation and domain/range schema migration.
"""
from __future__ import annotations

from typing import Any, Callable

from tools.tier2_common import IntegrityError, utc_now

REMAP_PLAN = [
    ("ALIGNED_TO", "TRANSITIONS_TO"),
    ("BRIDGES_TO", "TRANSITIONS_TO"),
    ("BUILDS", "DELIVERS"),
    ("CIO_ANCHOR_FOR", "ROUTES_TO"),
    ("GOVERNED_BY", "GOVERNS"),
    ("GOVERNS_USE_OF", "GOVERNS"),
]


def execute_predicate_consolidation(
    conn: Any,
    audit_cb: Callable[..., None],
) -> dict:
    """Consolidate one-off predicates into canonical families."""
    stats = {
        "domain_range_entries_added": 0,
        "predicates_remapped": 0,
        "edges_remapped": 0,
        "predicate_skips": [],
    }

    conn.execute("PRAGMA foreign_keys=OFF;")
    ts = utc_now()

    for old_pred, new_pred in REMAP_PLAN:
        conn.execute(
            """INSERT OR IGNORE INTO edge_type_registry (
                edge_type_id, canonical_predicate, definition, relationship_family,
                direction, symmetric_flag, transitive_flag, cardinality, temporal_requirement,
                evidence_required, qualifier_rules_json, registry_state, allow_self_loop, created_at
            ) VALUES (?, ?, ?, ?, 'directed', 0, 0, 'many_to_many', 'recommended', 1, '{}', 'active', 0, ?)""",
            (
                new_pred,
                new_pred,
                f"Canonical predicate {new_pred}; normalized relationship family.",
                new_pred,
                ts,
            ),
        )

        existing_dr = conn.execute(
            "SELECT source_node_type, target_node_type FROM edge_type_domain_range WHERE edge_type_id = ?",
            (old_pred,),
        ).fetchall()

        dr_added = 0
        for dr in existing_dr:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO edge_type_domain_range
                       (edge_type_id, source_node_type, target_node_type, active)
                       VALUES (?, ?, ?, 1)""",
                    (new_pred, dr["source_node_type"], dr["target_node_type"]),
                )
                dr_added += 1
            except IntegrityError:
                pass

        stats["domain_range_entries_added"] += dr_added

        try:
            conn.execute(f"SAVEPOINT pred_{old_pred};")
            edge_count = conn.execute(
                "UPDATE edge SET edge_type = ?, updated_at = ? WHERE edge_type = ?",
                (new_pred, ts, old_pred),
            ).rowcount

            conn.execute(
                """UPDATE edge_type_registry
                   SET registry_state = 'deprecated', replacement_predicate = ?
                   WHERE edge_type_id = ? AND registry_state = 'active'""",
                (new_pred, old_pred),
            )

            audit_cb(
                conn,
                "predicate_remap",
                "edge_type_registry",
                old_pred,
                {"predicate": old_pred, "state": "active"},
                {"predicate": old_pred, "state": "deprecated", "replacement": new_pred},
                f"Tier2 predicate consolidation: {old_pred} -> {new_pred}",
            )

            conn.execute(f"RELEASE pred_{old_pred};")
            stats["predicates_remapped"] += 1
            stats["edges_remapped"] += edge_count
        except IntegrityError as e:
            conn.execute(f"ROLLBACK TO pred_{old_pred};")
            conn.execute(f"RELEASE pred_{old_pred};")
            stats["predicate_skips"].append({"old": old_pred, "new": new_pred, "error": str(e)})

    conn.execute("PRAGMA foreign_keys=ON;")
    return stats
