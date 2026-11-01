"""Graph garbage collection utility for the NEE knowledge graph.

Scans the active graph for expired stale risks, decisions, proposals, and unreferenced
orphan nodes, moving them to archived partitions without deleting data.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from storage.database import create_sqlite_conn, sqlite3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

# Tables that carry stale_after and are eligible for archival
ARCHIVABLE_TABLES = {
    "decisions": "decision_id",
    "risks": "risk_id",
    "proposals": "proposal_id",
}


def _ensure_archive_tables(conn: sqlite3.Connection) -> None:
    """Create archived_* shadow tables if they don't exist."""
    cursor = conn.cursor()
    for table in ARCHIVABLE_TABLES:
        archive_table = f"archived_{table}"
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (archive_table,),
        )
        if cursor.fetchone() is None:
            # Create the archive table with the same schema + archived_at column
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
            row = cursor.fetchone()
            if row is None:
                logger.warning("Table %s does not exist in graph — skipping archive table creation", table)
                continue
            create_sql = row[0]
            # Replace table name in CREATE statement
            archive_sql = create_sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {archive_table}", 1)
            # Some schemas use quoted identifiers
            archive_sql = archive_sql.replace(f'CREATE TABLE "{table}"', f'CREATE TABLE "{archive_table}"', 1)
            cursor.execute(archive_sql)
            # Add archived_at column
            with contextlib.suppress(sqlite3.OperationalError):
                cursor.execute(f"ALTER TABLE {archive_table} ADD COLUMN archived_at TEXT")
            logger.info("Created archive table: %s", archive_table)

    cursor.execute(
        """CREATE TABLE IF NOT EXISTS archived_node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            node_type TEXT,
            archived_at TEXT,
            reason TEXT
        )"""
    )
    conn.commit()


def _find_orphan_nodes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Find unreferenced non-intentional orphan nodes in the active graph."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node'")
    if cursor.fetchone() is None:
        return []

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_state'")
    has_node_state = cursor.fetchone() is not None

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion'")
    has_assertion = cursor.fetchone() is not None

    if not has_assertion:
        return []

    cursor.execute("PRAGMA table_info(node)")
    node_cols = {row[1] for row in cursor.fetchall()}
    active_filter = "n.active = 1" if "active" in node_cols else "1=1"

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = {r[0] for r in cursor.fetchall()}
    extra_filters = []
    if "node_type_registry" in all_tables:
        extra_filters.append("AND n.node_id NOT IN (SELECT DISTINCT example_node_id FROM node_type_registry WHERE example_node_id IS NOT NULL)")
    if "edge" in all_tables:
        extra_filters.append("AND n.node_id NOT IN (SELECT DISTINCT from_node_id FROM edge WHERE from_node_id IS NOT NULL)")
        extra_filters.append("AND n.node_id NOT IN (SELECT DISTINCT to_node_id FROM edge WHERE to_node_id IS NOT NULL)")
    if "claim" in all_tables:
        extra_filters.append("AND n.node_id NOT IN (SELECT DISTINCT subject_node_id FROM claim WHERE subject_node_id IS NOT NULL)")
    extra_filter_sql = "\n              ".join(extra_filters)

    if has_node_state:
        query = f"""
            SELECT n.node_id, n.canonical_name, n.node_type
            FROM node n
            LEFT JOIN node_state ns ON n.node_id = ns.node_id
            WHERE {active_filter}
              AND coalesce(ns.intentional_orphan, 0) = 0
              AND n.node_id NOT IN (SELECT DISTINCT subject_node_id FROM assertion WHERE subject_node_id IS NOT NULL)
              AND n.node_id NOT IN (SELECT DISTINCT object_node_id FROM assertion WHERE object_node_id IS NOT NULL)
              {extra_filter_sql}
        """
    else:
        query = f"""
            SELECT n.node_id, n.canonical_name, n.node_type
            FROM node n
            WHERE {active_filter}
              AND n.node_id NOT IN (SELECT DISTINCT subject_node_id FROM assertion WHERE subject_node_id IS NOT NULL)
              AND n.node_id NOT IN (SELECT DISTINCT object_node_id FROM assertion WHERE object_node_id IS NOT NULL)
              {extra_filter_sql}
        """

    cursor.execute(query)
    col_names = [desc[0] for desc in cursor.description]
    return [dict(zip(col_names, row, strict=False)) for row in cursor.fetchall()]


def _archive_orphan_nodes(
    conn: sqlite3.Connection,
    orphan_nodes: list[dict[str, Any]],
) -> int:
    """Move unreferenced orphan nodes to archived_node shadow table."""
    if not orphan_nodes:
        return 0
    archived_at = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "write_control" in tables:
        cursor.execute("UPDATE write_control SET allow_legacy_write=1, active_transaction_id='graph_gc', updated_at=? WHERE control_id=1", (archived_at,))

    for node in orphan_nodes:
        nid = node["node_id"]
        cname = node.get("canonical_name") or nid
        ntype = node.get("node_type") or "entity"
        cursor.execute(
            """INSERT OR REPLACE INTO archived_node (node_id, canonical_name, node_type, archived_at, reason)
               VALUES (?, ?, ?, ?, 'unreferenced_orphan')""",
            (nid, cname, ntype, archived_at),
        )
        if "canonical_route" in tables:
            cursor.execute("DELETE FROM canonical_route WHERE object_type = 'node' AND object_id = ?", (nid,))
        if "node_card_map" in tables:
            cursor.execute("DELETE FROM node_card_map WHERE node_id = ?", (nid,))
        if "node_state" in tables:
            cursor.execute("DELETE FROM node_state WHERE node_id = ?", (nid,))
        cursor.execute("DELETE FROM node WHERE node_id = ?", (nid,))
        logger.info("Archived orphan node:%s (%s)", nid, ntype)

    if "write_control" in tables:
        cursor.execute("UPDATE write_control SET allow_legacy_write=0, active_transaction_id=NULL, updated_at=? WHERE control_id=1", (archived_at,))
    conn.commit()
    return len(orphan_nodes)


def _find_stale_entities(
    conn: sqlite3.Connection,
    as_of: date,
) -> dict[str, list[dict[str, Any]]]:
    """Find entities with stale_after < as_of_date that are not still open/active without supersession."""
    results: dict[str, list[dict[str, Any]]] = {}
    cursor = conn.cursor()

    for table in ARCHIVABLE_TABLES:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if cursor.fetchone() is None:
            continue

        # Check if stale_after column exists
        cursor.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cursor.fetchall()}
        if "stale_after" not in columns:
            logger.debug("Table %s has no stale_after column — skipping", table)
            continue

        as_of_str = as_of.isoformat()
        where_clauses = ["stale_after IS NOT NULL", "stale_after < ?"]
        if "status" in columns:
            if "superseded_by_id" in columns:
                where_clauses.append(
                    "(lower(coalesce(status, '')) NOT IN ('open', 'active') "
                    "OR (superseded_by_id IS NOT NULL AND superseded_by_id != ''))"
                )
            else:
                where_clauses.append("lower(coalesce(status, '')) NOT IN ('open', 'active')")

        cursor.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(where_clauses)}",
            (as_of_str,),
        )
        col_names = [desc[0] for desc in cursor.description]
        stale_rows = [dict(zip(col_names, row, strict=False)) for row in cursor.fetchall()]

        if stale_rows:
            results[table] = stale_rows
            logger.info(
                "Found %d stale entities in %s (as_of=%s)",
                len(stale_rows), table, as_of_str,
            )

    return results


def _archive_entities(
    conn: sqlite3.Connection,
    stale: dict[str, list[dict[str, Any]]],
    as_of: date,
) -> dict[str, int]:
    """Move stale entities to archive tables."""
    archived_at = datetime.now(timezone.utc).isoformat()
    counts: dict[str, int] = {}
    cursor = conn.cursor()

    for table, rows in stale.items():
        id_col = ARCHIVABLE_TABLES[table]
        archive_table = f"archived_{table}"

        for row in rows:
            entity_id = row.get(id_col, "unknown")
            columns = list(row.keys())
            placeholders = ", ".join(["?"] * (len(columns) + 1))
            col_list = ", ".join(columns + ["archived_at"])
            values = [row[c] for c in columns] + [archived_at]

            cursor.execute(
                f"INSERT OR REPLACE INTO {archive_table} ({col_list}) VALUES ({placeholders})",
                values,
            )
            cursor.execute(
                f"DELETE FROM {table} WHERE {id_col} = ?",
                (row[id_col],),
            )
            logger.info("Archived %s:%s (stale_after=%s)", table, entity_id, row.get("stale_after"))

        counts[table] = len(rows)

    conn.commit()
    return counts


def run_gc(
    graph_path: Path,
    *,
    as_of: date | None = None,
    prune_orphans: bool = True,
    ledger_path: Path | None = None,
    runtime_root: Path | None = None,
    interactive_transport: Any = None,
) -> dict[str, Any]:
    """Run garbage collection on the graph, strictly gated behind authentic operator approval.

    Returns a receipt with archived entity counts and approval state.
    """
    if as_of is None:
        as_of = date.today()

    if not graph_path.exists():
        logger.error("Graph file not found: %s", graph_path)
        return {"error": "graph_not_found", "path": str(graph_path)}

    conn = create_sqlite_conn(graph_path)
    try:
        _ensure_archive_tables(conn)
        stale = _find_stale_entities(conn, as_of)
        orphan_nodes = _find_orphan_nodes(conn) if prune_orphans else []

        total_stale = sum(len(rows) for rows in stale.values())
        total_items = total_stale + len(orphan_nodes)
        if total_items == 0:
            logger.info("No stale entities or orphan nodes found (as_of=%s)", as_of.isoformat())
            return {
                "status": "clean", "as_of": as_of.isoformat(),
                "stale_count": 0, "orphan_nodes_count": 0, "mutated": False,
            }

        table_deltas: dict[str, int] = {}
        for table, rows in stale.items():
            if rows:
                table_deltas[f"archived_{table}"] = len(rows)
        if orphan_nodes and prune_orphans:
            table_deltas["archived_node"] = len(orphan_nodes)

        from dual_pipeline.hitl_governance_core.mutation_approval import request_mutation_approval

        approval = request_mutation_approval(
            action_type="GRAPH_GC",
            target_resource=graph_path,
            table_deltas=table_deltas,
            description=f"Archive {total_items} items ({total_stale} stale entities, {len(orphan_nodes)} orphan nodes)",
            ledger_path=ledger_path,
            storage_root=runtime_root,
            interactive_transport=interactive_transport,
        )

        if not approval.get("cleared"):
            summary = {}
            for table, rows in stale.items():
                id_col = ARCHIVABLE_TABLES[table]
                summary[table] = [row.get(id_col, "unknown") for row in rows]
            if orphan_nodes:
                summary["orphan_nodes"] = [row["node_id"] for row in orphan_nodes]
            return {
                "status": approval.get("status", "AWAITING_APPROVAL"), "as_of": as_of.isoformat(),
                "stale_count": total_stale, "orphan_nodes_count": len(orphan_nodes),
                "total_items": total_items, "would_archive": summary,
                "table_deltas": table_deltas, "approval_request_id": approval.get("request_id"),
                "delta_digest": approval.get("delta_digest"), "mutated": False,
            }

        counts = _archive_entities(conn, stale, as_of)
        archived_orphans = _archive_orphan_nodes(conn, orphan_nodes) if prune_orphans else 0
        if archived_orphans > 0:
            counts["orphan_nodes"] = archived_orphans
        logger.info("Archived %d items total (%d stale entities, %d orphan nodes)", total_items, total_stale, archived_orphans)
        return {
            "status": "archived", "as_of": as_of.isoformat(),
            "archived_counts": counts, "total_archived": total_items,
            "stale_count": total_stale, "orphan_nodes_count": archived_orphans, "mutated": True,
        }
    finally:
        conn.close()


def _find_active_graph() -> Path | None:
    """Find the active graph path from active_pair.json."""
    candidates = [
        ROOT / "runtime" / "active_pair.json",
        ROOT / "active_pair.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                graph_path = data.get("graph_path") or data.get("graph")
                if graph_path:
                    p = Path(graph_path)
                    if not p.is_absolute():
                        p = candidate.parent / p
                    return p.resolve()
            except (json.JSONDecodeError, KeyError):
                continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Graph garbage collection — archive stale entities"
    )
    parser.add_argument("--graph", type=Path, default=None, help="Path to graph SQLite file. Auto-detected from active_pair.json if not specified.")
    parser.add_argument("--as-of", type=str, default=None, help="Date to use as staleness cutoff (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--prune-orphans", action="store_true", default=True, help="Prune unreferenced orphan nodes into archived_node.")
    parser.add_argument("--no-prune-orphans", dest="prune_orphans", action="store_false", help="Do not prune orphan nodes.")
    parser.add_argument("--ledger-path", type=Path, default=None, help="Path to HITL decision ledger SQLite file")
    parser.add_argument("--runtime-root", type=Path, default=None, help="Path to runtime root directory")
    args = parser.parse_args()

    graph_path = args.graph
    if graph_path is None:
        graph_path = _find_active_graph()
        if graph_path is None:
            logger.error("Cannot find active graph. Specify --graph path.")
            return

    as_of = None
    if args.as_of:
        as_of = date.fromisoformat(args.as_of)

    result = run_gc(
        graph_path,
        as_of=as_of,
        prune_orphans=args.prune_orphans,
        ledger_path=args.ledger_path,
        runtime_root=args.runtime_root,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
