"""Application of source retirement plan to target SQLite database."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..core import ZERO_HASH, sha256_file, sqlite_integrity, stable_id, utc_now
from .constants import (
    _SUPERSESSION_DETACHMENT_TARGETS,
    SOURCE_RETIREMENT_POLICY_VERSION,
    _placeholders,
    _table_exists,
)
from .plan import _assert_plan_digest
from .targets import _assert_dependencies_clear, _collect_targets


def _delete_by_ids(
    connection: sqlite3.Connection, table: str, field: str, values: list[str]
) -> int:
    if not values or not _table_exists(connection, table):
        return 0
    return connection.execute(
        f"DELETE FROM {table} WHERE {field} IN ({_placeholders(values)})", tuple(values)
    ).rowcount


def _delete_canonical_routes(connection: sqlite3.Connection, route_ids: list[str]) -> int:
    if not route_ids or not _table_exists(connection, "canonical_route"):
        return 0
    trigger = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='canonical_route_sync_ad'"
    ).fetchone()
    trigger_sql = trigger[0] if trigger is not None else None
    if trigger_sql:
        connection.execute("DROP TRIGGER canonical_route_sync_ad")
    try:
        return connection.execute(
            "DELETE FROM canonical_route WHERE route_id IN ("
            + _placeholders(route_ids)
            + ")",
            tuple(route_ids),
        ).rowcount
    finally:
        if trigger_sql:
            connection.execute(trigger_sql)


def _apply_source_retirement(
    connection: sqlite3.Connection, *, targets: dict[str, Any]
) -> dict[str, int]:
    _assert_dependencies_clear(connection, targets)
    remove = targets["remove"]
    derived = targets["derived"]
    counts = {
        "supersession_detachments": 0,
        "assertion": 0,
        "claim": 0,
        "edge": 0,
        "alias": 0,
        "canonical_alias": 0,
        "evidence_item": 0,
        "validation_queue": 0,
        "legacy_claim_assertion_map": 0,
        "legacy_edge_assertion_map": 0,
        "claim_confidence_hitl_matrix": 0,
        "legacy_alias_canonical_map": 0,
        "canonical_route": 0,
        "search_document": 0,
        "graph_quality_exception": 0,
        "source": 0,
        "source_version": 0,
    }
    for detachment in targets["supersession_detachments"]:
        table, id_field, supersession_field = _SUPERSESSION_DETACHMENT_TARGETS[
            detachment["object_type"]
        ]
        cursor = connection.execute(
            "UPDATE " + table + " SET " + supersession_field + "=NULL WHERE "
            + id_field + "=? AND " + supersession_field + "=?",
            (
                detachment["object_id"],
                detachment["expected_superseded_by_object_id"],
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                "source-retirement supersession detachment did not apply: "
                + detachment["object_id"]
            )
        counts["supersession_detachments"] += cursor.rowcount

    counts["graph_quality_exception"] = _delete_by_ids(
        connection,
        "graph_quality_exception",
        "exception_id",
        derived["graph_quality_exception_ids"],
    )
    counts["validation_queue"] = _delete_by_ids(
        connection, "validation_queue", "validation_id", derived["validation_ids"]
    )
    counts["claim_confidence_hitl_matrix"] = _delete_by_ids(
        connection,
        "claim_confidence_hitl_matrix",
        "claim_id",
        derived["claim_confidence_claim_ids"],
    )
    counts["legacy_alias_canonical_map"] = _delete_by_ids(
        connection,
        "legacy_alias_canonical_map",
        "alias_id",
        derived["legacy_alias_canonical_map_alias_ids"],
    )
    counts["legacy_claim_assertion_map"] = _delete_by_ids(
        connection,
        "legacy_claim_assertion_map",
        "claim_id",
        remove["claim_ids"],
    )
    counts["legacy_edge_assertion_map"] = _delete_by_ids(
        connection,
        "legacy_edge_assertion_map",
        "edge_id",
        remove["edge_ids"],
    )
    counts["assertion"] = _delete_by_ids(
        connection, "assertion", "assertion_id", remove["assertion_ids"]
    )
    counts["edge"] = _delete_by_ids(connection, "edge", "edge_id", remove["edge_ids"])
    counts["claim"] = _delete_by_ids(
        connection, "claim", "claim_id", remove["claim_ids"]
    )
    counts["canonical_route"] = _delete_canonical_routes(
        connection, derived["canonical_route_ids"]
    )
    counts["search_document"] = _delete_by_ids(
        connection,
        "search_document",
        "search_document_id",
        derived["search_document_ids"],
    )
    counts["alias"] = _delete_by_ids(connection, "alias", "alias_id", remove["alias_ids"])
    counts["canonical_alias"] = _delete_by_ids(
        connection,
        "canonical_alias",
        "canonical_alias_id",
        remove["canonical_alias_ids"],
    )
    counts["evidence_item"] = _delete_by_ids(
        connection, "evidence_item", "evidence_id", remove["evidence_ids"]
    )
    for update in targets["source_updates"]:
        cursor = connection.execute(
            "UPDATE source SET source_name=?,source_posture=?,notes=? WHERE source_id=? "
            "AND source_name=? AND source_posture=? AND notes IS ?",
            (
                update["source_name"],
                update["source_posture"],
                update["notes"],
                update["source_id"],
                update["expected_source_name"],
                update["expected_source_posture"],
                update["expected_notes"],
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"source-retirement source update did not apply: {update['source_id']}")
        counts["source"] += cursor.rowcount
    for update in targets["source_version_updates"]:
        cursor = connection.execute(
            "UPDATE source_version SET scope_text=?,posture_text=? WHERE source_version_id=? "
            "AND scope_text=? AND posture_text=?",
            (
                update["scope_text"],
                update["posture_text"],
                update["source_version_id"],
                update["expected_scope_text"],
                update["expected_posture_text"],
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                "source-retirement source-version update did not apply: "
                f"{update['source_version_id']}"
            )
        counts["source_version"] += cursor.rowcount
    return counts


def apply_source_retirement_plan(
    graph_path: Path,
    *,
    plan: dict[str, Any],
    approval_id: str,
    run_id: str,
    event_timestamp: str | None = None,
) -> dict[str, Any]:
    """Apply the exact plan in one transaction and retain a durable audit event."""

    _assert_plan_digest(plan)
    graph_path = Path(graph_path).resolve()
    before_hash = sha256_file(graph_path)
    if before_hash != plan["base_graph_sha256"]:
        raise RuntimeError("source-retirement plan is stale relative to its graph")
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path)
    connection.row_factory = sqlite3.Row
    now = event_timestamp or utc_now()
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        current = _collect_targets(connection, plan["control"])
        if current != plan["targets"]:
            raise RuntimeError("source-retirement target set changed before release")
        operation = plan["operations"][0]
        if operation["before_state_hash"] != current["snapshot_digest"]:
            raise RuntimeError("source-retirement target snapshot binding mismatch")
        counts = _apply_source_retirement(connection, targets=current)
        audit_id = stable_id(
            "source-retirement-audit", plan["plan_id"], operation["operation_id"], length=28
        )
        connection.execute(
            """INSERT INTO audit_event
               (audit_event_id,actor_or_process,action,record_type,record_id,
                previous_value_json,new_value_json,source_id,event_timestamp,reason,
                validation_reference,approval_reference,migration_or_ingestion_run_id,
                release_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                audit_id,
                "nee-source-retirement",
                operation["operation"],
                "source_retirement",
                operation["target_id"],
                json.dumps(
                    {
                        "target_snapshot_digest": current["snapshot_digest"],
                        "removed": current["remove"],
                        "retained": current["retain"],
                    },
                    sort_keys=True,
                ),
                json.dumps({"removed": counts}, sort_keys=True),
                current["target_source_id"],
                now,
                operation["reason"],
                plan["plan_id"],
                approval_id,
                run_id,
                SOURCE_RETIREMENT_POLICY_VERSION,
            ),
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"source-retirement foreign-key validation failed: {len(violations)} violations"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    integrity = sqlite_integrity(graph_path)
    after_hash = sha256_file(graph_path)
    if (
        integrity["integrity_check"] != "ok"
        or integrity["foreign_key_violation_count"]
        or after_hash == before_hash
    ):
        raise RuntimeError("source-retirement integrity or graph-change check failed")
    return {
        "status": "COMMITTED",
        "change_set_id": plan["plan_id"],
        "approval_id": approval_id,
        "transaction_required": True,
        "operation_count": 1,
        "operations": [
            {
                "operation_id": plan["operations"][0]["operation_id"],
                "change_set_id": plan["plan_id"],
                "run_id": run_id,
                "source_id": plan["targets"]["target_source_id"],
                "target_id": plan["targets"]["target_source_id"],
                "before_state_hash": plan["operations"][0]["before_state_hash"],
                "after_row_hash": ZERO_HASH,
            }
        ],
        "removed": counts,
        "graph_sha256_before": before_hash,
        "graph_sha256_after": after_hash,
        "integrity": integrity,
        "committed_at": now,
    }

