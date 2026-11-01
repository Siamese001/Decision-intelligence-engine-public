"""
tools/tier2_dedup_core/merges.py

Exact-match node merges, entity redirects, and self-loop resolution.
"""
from __future__ import annotations

from typing import Any, Callable, List, Tuple

from tools.tier2_common import IntegrityError, stable_id, utc_now

SAME_TYPE_MERGES = [
    ("person_gordon_paynter", "stakeholder_gordon_paynter"),
    ("q3_charter_customer_ai", "workstream_customer_ai"),
    ("q3_charter_fcdb", "workstream_fcdb"),
    ("q3_charter_nuclear_scheduling", "workstream_nuclear_scheduling"),
    ("q3_charter_pd_conduit", "workstream_pd_conduit"),
]


def execute_node_merges(
    conn: Any,
    run_id: str,
    audit_cb: Callable[..., None],
) -> Tuple[int, List[dict]]:
    """Execute approved node merges."""
    merged_count = 0
    merge_events: List[dict] = []

    for survivor_id, retired_id in SAME_TYPE_MERGES:
        survivor = conn.execute("SELECT * FROM node WHERE node_id = ?", (survivor_id,)).fetchone()
        retired = conn.execute("SELECT * FROM node WHERE node_id = ?", (retired_id,)).fetchone()

        if not survivor or not retired:
            continue
        if retired["active"] == 0:
            continue

        try:
            conn.execute(f"SAVEPOINT merge_{retired_id};")
            event = _merge_single_node(conn, survivor_id, retired_id, run_id, audit_cb)
            conn.execute(f"RELEASE merge_{retired_id};")
            merge_events.append(event)
            merged_count += 1
        except IntegrityError as e:
            conn.execute(f"ROLLBACK TO merge_{retired_id};")
            conn.execute(f"RELEASE merge_{retired_id};")
            print(f"  ⚠ Merge skipped ({retired_id} -> {survivor_id}): {e}")

    return merged_count, merge_events


def _merge_single_node(
    conn: Any,
    survivor_id: str,
    retired_id: str,
    run_id: str,
    audit_cb: Callable[..., None],
) -> dict:
    merge_event_id = stable_id("merge", survivor_id, retired_id)
    ts = utc_now()

    conn.execute(
        """INSERT OR IGNORE INTO entity_merge_event (
            merge_event_id, canonical_node_id, event_type, status, reason, created_at, completed_at
        ) VALUES (?, ?, 'merge', 'completed', 'Tier2 concept dedup: exact/near-exact match', ?, ?)""",
        (merge_event_id, survivor_id, ts, ts),
    )

    conn.execute(
        """INSERT OR IGNORE INTO entity_redirect (
            from_node_id, to_node_id, merge_event_id, active, created_at
        ) VALUES (?, ?, ?, 1, ?)""",
        (retired_id, survivor_id, merge_event_id, ts),
    )

    # Resolve self-loops
    self_loop_assertions = conn.execute(
        "SELECT assertion_id FROM assertion WHERE (subject_node_id=? AND object_node_id=?) OR (subject_node_id=? AND object_node_id=?)",
        (survivor_id, retired_id, retired_id, survivor_id),
    ).fetchall()
    self_loop_asrt_ids = {r["assertion_id"] for r in self_loop_assertions}

    for sl_id in self_loop_asrt_ids:
        conn.execute(
            """UPDATE assertion SET currentness_state='historical', canonical_status='historical',
               superseded_at=?, updated_at=? WHERE assertion_id=? AND superseded_by_assertion_id IS NULL""",
            (ts, ts, sl_id),
        )

    # Re-point assertions
    if self_loop_asrt_ids:
        ph = ",".join("?" for _ in self_loop_asrt_ids)
        moved_subj = conn.execute(
            f"UPDATE assertion SET subject_node_id=?, updated_at=? WHERE subject_node_id=? AND assertion_id NOT IN ({ph})",
            (survivor_id, ts, retired_id, *self_loop_asrt_ids),
        ).rowcount
        moved_obj = conn.execute(
            f"UPDATE assertion SET object_node_id=?, updated_at=? WHERE object_node_id=? AND assertion_id NOT IN ({ph})",
            (survivor_id, ts, retired_id, *self_loop_asrt_ids),
        ).rowcount
    else:
        moved_subj = conn.execute("UPDATE assertion SET subject_node_id=?, updated_at=? WHERE subject_node_id=?", (survivor_id, ts, retired_id)).rowcount
        moved_obj = conn.execute("UPDATE assertion SET object_node_id=?, updated_at=? WHERE object_node_id=?", (survivor_id, ts, retired_id)).rowcount

    # Resolve self-loop edges
    self_loop_edges = conn.execute(
        "SELECT edge_id FROM edge WHERE (from_node_id=? AND to_node_id=?) OR (from_node_id=? AND to_node_id=?)",
        (survivor_id, retired_id, retired_id, survivor_id),
    ).fetchall()
    self_loop_edge_ids = {r["edge_id"] for r in self_loop_edges}

    for sl_eid in self_loop_edge_ids:
        surv_edge = conn.execute(
            "SELECT edge_id FROM edge WHERE from_node_id=? AND edge_id!=? AND superseded_by IS NULL LIMIT 1",
            (survivor_id, sl_eid),
        ).fetchone()
        if not surv_edge:
            surv_edge = conn.execute(
                "SELECT edge_id FROM edge WHERE edge_id!=? AND superseded_by IS NULL LIMIT 1",
                (sl_eid,),
            ).fetchone()
        target_edge_id = surv_edge["edge_id"] if surv_edge else None
        conn.execute("UPDATE edge SET superseded_by=?, updated_at=? WHERE edge_id=? AND superseded_by IS NULL", (target_edge_id, ts, sl_eid))

    if self_loop_edge_ids:
        ph_e = ",".join("?" for _ in self_loop_edge_ids)
        moved_from = conn.execute(
            f"UPDATE edge SET from_node_id=?, updated_at=? WHERE from_node_id=? AND edge_id NOT IN ({ph_e})",
            (survivor_id, ts, retired_id, *self_loop_edge_ids),
        ).rowcount
        moved_to = conn.execute(
            f"UPDATE edge SET to_node_id=?, updated_at=? WHERE to_node_id=? AND edge_id NOT IN ({ph_e})",
            (survivor_id, ts, retired_id, *self_loop_edge_ids),
        ).rowcount
    else:
        moved_from = conn.execute("UPDATE edge SET from_node_id=?, updated_at=? WHERE from_node_id=?", (survivor_id, ts, retired_id)).rowcount
        moved_to = conn.execute("UPDATE edge SET to_node_id=?, updated_at=? WHERE to_node_id=?", (survivor_id, ts, retired_id)).rowcount

    # Re-point claims
    moved_claims = conn.execute("UPDATE claim SET subject_node_id=?, updated_at=? WHERE subject_node_id=?", (survivor_id, ts, retired_id)).rowcount

    # Write control for governed tables
    conn.execute("UPDATE write_control SET allow_legacy_write=1, active_transaction_id=?, updated_at=? WHERE control_id=1", (run_id, ts))
    conn.execute("UPDATE canonical_route SET object_id=? WHERE object_type='node' AND object_id=?", (survivor_id, retired_id))
    conn.execute("DELETE FROM node_card_map WHERE node_id=? AND card_id IN (SELECT card_id FROM node_card_map WHERE node_id=?)", (retired_id, survivor_id))
    conn.execute("UPDATE node_card_map SET node_id=? WHERE node_id=?", (survivor_id, retired_id))
    conn.execute("UPDATE canonical_alias SET entity_id=? WHERE entity_id=?", (survivor_id, retired_id))
    conn.execute("UPDATE write_control SET allow_legacy_write=0, active_transaction_id=NULL, updated_at=? WHERE control_id=1", (ts,))

    # Deactivate retired node
    conn.execute("UPDATE node SET active=0, status='merged', updated_at=? WHERE node_id=?", (ts, retired_id))
    conn.execute("UPDATE node_state SET lifecycle_state='inactive', resolution_state='superseded', updated_at=? WHERE node_id=?", (ts, retired_id))

    audit_cb(
        conn,
        "node_merge",
        "node",
        retired_id,
        {"node_id": retired_id, "active": 1},
        {"node_id": retired_id, "active": 0, "redirected_to": survivor_id},
        f"Tier2 merge: {retired_id} -> {survivor_id}",
    )

    total_moved = moved_subj + moved_obj + moved_from + moved_to + moved_claims
    return {
        "merge_event_id": merge_event_id,
        "survivor": survivor_id,
        "retired": retired_id,
        "assertions_moved": moved_subj + moved_obj,
        "edges_moved": moved_from + moved_to,
        "claims_moved": moved_claims,
        "total_references_moved": total_moved,
    }
