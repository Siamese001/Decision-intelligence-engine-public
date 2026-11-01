"""
dual_pipeline/graph_consolidation_core/apply.py

Governed transactional actuation of consolidation plans.
Enforces foreign keys ON at all times, strictly zero write_control bypasses,
atomic rollback on failure, full audit_event persistence, and zero-op safety.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from storage.connection import sqlite3

from ..core import sha256_file, sqlite_integrity, stable_id, utc_now
from ..storage.governed_context import GovernedStorageContext
from .constants import CONSOLIDATION_POLICY_VERSION
from .plan import _assert_plan_digest
from .redirect_guard import verify_redirect_context
from .redirect_repair import apply_redirect, is_redirect_plan, redirect_operations, validate_redirect_rows
from .repoint import (
    repoint_node_assertions,
    repoint_node_edges,
    repoint_node_entities_and_routes,
)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone() is not None


def _apply_node_merge(
    conn: sqlite3.Connection,
    op: Dict[str, Any],
    now: str,
    approval_id: str,
    run_id: str,
    plan_id: str,
) -> Dict[str, Any]:
    survivor_id = op["survivor_node_id"]
    retired_id = op["retired_node_id"]

    surv = conn.execute("SELECT node_id, node_type, active FROM node WHERE node_id=?", (survivor_id,)).fetchone()
    ret = conn.execute("SELECT node_id, node_type, active, status FROM node WHERE node_id=?", (retired_id,)).fetchone()
    if not surv:
        raise ValueError(f"Survivor node not found: {survivor_id}")
    if not ret:
        raise ValueError(f"Retired node not found: {retired_id}")
    if ret["active"] == 0 or ret["status"] == "merged":
        return {
            "operation_id": op["operation_id"],
            "operation_type": "merge_node",
            "retired_id": retired_id,
            "survivor_id": survivor_id,
            "status": "ALREADY_MERGED",
        }
    merge_event_id = stable_id("merge", survivor_id, retired_id, length=24)
    if _table_exists(conn, "entity_merge_event"):
        conn.execute(
            "INSERT OR IGNORE INTO entity_merge_event (merge_event_id, canonical_node_id, event_type, status, reason, created_at, completed_at) VALUES (?, ?, 'merge', 'completed', 'Governed graph consolidation: node merge', ?, ?)",
            (merge_event_id, survivor_id, now, now),
        )
    if _table_exists(conn, "entity_redirect"):
        conn.execute(
            "INSERT OR IGNORE INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (retired_id, survivor_id, merge_event_id, now),
        )

    s_type = surv[1]
    repoint_node_assertions(conn, retired_id, survivor_id, s_type, now)
    repoint_node_edges(conn, retired_id, survivor_id, s_type, now)
    repoint_node_entities_and_routes(conn, retired_id, survivor_id, now)

    # Record audit event
    audit_id = stable_id("audit-consolidation-merge", plan_id, op["operation_id"], length=28)
    conn.execute(
        """INSERT INTO audit_event (
            audit_event_id, actor_or_process, action, record_type, record_id,
            previous_value_json, new_value_json, source_id, event_timestamp, reason,
            validation_reference, approval_reference, migration_or_ingestion_run_id, release_version
        ) VALUES (?, 'graph_consolidation', 'merge_node', 'node', ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        (
            audit_id, retired_id,
            json.dumps({"node_id": retired_id, "active": 1}),
            json.dumps({"node_id": retired_id, "active": 0, "merged_into": survivor_id}),
            now, op.get("selection_reason", f"Consolidation merge: {retired_id} -> {survivor_id}"),
            plan_id, approval_id, run_id, CONSOLIDATION_POLICY_VERSION,
        ),
    )
    return {
        "operation_id": op["operation_id"],
        "operation_type": "merge_node",
        "retired_id": retired_id,
        "survivor_id": survivor_id,
    }


def _apply_assertion_supersession(
    conn: sqlite3.Connection,
    op: Dict[str, Any],
    now: str,
    approval_id: str,
    run_id: str,
    plan_id: str,
) -> Dict[str, Any]:
    survivor_id = op["survivor_assertion_id"]
    retired_id = op["retired_assertion_id"]

    surv = conn.execute("SELECT assertion_id FROM assertion WHERE assertion_id=?", (survivor_id,)).fetchone()
    ret = conn.execute("SELECT assertion_id, currentness_state FROM assertion WHERE assertion_id=?", (retired_id,)).fetchone()
    if not surv:
        raise ValueError(f"Survivor assertion not found: {survivor_id}")
    if not ret:
        raise ValueError(f"Retired assertion not found: {retired_id}")

    conn.execute(
        "UPDATE assertion SET currentness_state='historical', retrieval_eligible=0, "
        "superseded_at=?, superseded_by_assertion_id=?, updated_at=? WHERE assertion_id=?",
        (now, survivor_id, now, retired_id),
    )
    if _table_exists(conn, "search_document"):
        conn.execute(
            "UPDATE search_document SET currentness_state='historical', retrieval_eligible=0, updated_at=? WHERE assertion_id=?",
            (now, retired_id),
        )

    # Consolidate evidence items onto survivor assertion
    if _table_exists(conn, "assertion_evidence"):
        conn.execute(
            """INSERT OR IGNORE INTO assertion_evidence (
                assertion_id, evidence_id, evidence_relationship, created_at
            ) SELECT ?, evidence_id, 'supports', ?
                FROM assertion_evidence WHERE assertion_id=?""",
            (survivor_id, now, retired_id),
        )

    # Record audit event
    audit_id = stable_id("audit-consolidation-supersede", plan_id, op["operation_id"], length=28)
    conn.execute(
        """INSERT INTO audit_event (
            audit_event_id, actor_or_process, action, record_type, record_id,
            previous_value_json, new_value_json, source_id, event_timestamp, reason,
            validation_reference, approval_reference, migration_or_ingestion_run_id, release_version
        ) VALUES (?, ?, ?, 'assertion', ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        (
            audit_id,
            "graph_consolidation",
            op["operation_type"],
            retired_id,
            json.dumps({"assertion_id": retired_id, "currentness_state": ret["currentness_state"]}),
            json.dumps({"assertion_id": retired_id, "currentness_state": "historical", "superseded_by": survivor_id}),
            now,
            op.get("selection_reason", f"Consolidation supersession: {retired_id} -> {survivor_id}"),
            plan_id,
            approval_id,
            run_id,
            CONSOLIDATION_POLICY_VERSION,
        ),
    )
    return {
        "operation_id": op["operation_id"],
        "operation_type": op["operation_type"],
        "retired_id": retired_id,
        "survivor_id": survivor_id,
    }


def _dispatch_operation(
    conn: sqlite3.Connection,
    op: Dict[str, Any],
    *,
    now: str,
    approval_id: str,
    run_id: str,
    plan_id: str,
    plan: Dict[str, Any],
    runtime_root: Optional[Path],
    graph_path: Path,
    originals: Dict[str, Any],
) -> Tuple[Dict[str, Any], int, int]:
    op_type = op["operation_type"]
    if op_type == "flatten_redirect":
        verify_redirect_context(runtime_root, plan, approval_id, graph_path)
        receipt = apply_redirect(
            conn,
            op,
            originals[op["operation_id"]],
            now=now,
            approval_id=approval_id,
            run_id=run_id,
            plan_id=plan_id,
        )
        return receipt, 0, 0
    if op_type == "merge_node":
        receipt = _apply_node_merge(conn, op, now, approval_id, run_id, plan_id)
        return receipt, 1, 0
    if op_type in {"supersede_assertion", "supersede_literal_assertion"}:
        receipt = _apply_assertion_supersession(conn, op, now, approval_id, run_id, plan_id)
        return receipt, 0, 1
    raise ValueError(f"Unsupported consolidation operation type: {op_type}")


def _execute_plan_transaction(
    conn: sqlite3.Connection,
    operations: List[Dict[str, Any]],
    *,
    now: str,
    approval_id: str,
    run_id: str,
    plan_id: str,
    plan: Dict[str, Any],
    runtime_root: Optional[Path],
    graph_path: Path,
    redirect: bool,
) -> Tuple[List[Dict[str, Any]], int, int]:
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")

    originals = {}
    starting_changes = conn.total_changes
    if redirect:
        operations = redirect_operations(plan)
        originals = validate_redirect_rows(conn, operations)
        verify_redirect_context(runtime_root, plan, approval_id, graph_path)

    applied_receipts: List[Dict[str, Any]] = []
    nodes_merged = 0
    assertions_superseded = 0

    for op in operations:
        receipt, n_merged, a_superseded = _dispatch_operation(
            conn,
            op,
            now=now,
            approval_id=approval_id,
            run_id=run_id,
            plan_id=plan_id,
            plan=plan,
            runtime_root=runtime_root,
            graph_path=graph_path,
            originals=originals,
        )
        nodes_merged += n_merged
        assertions_superseded += a_superseded
        applied_receipts.append(receipt)

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"graph consolidation foreign-key violation check failed: {len(violations)} violations"
        )
    if redirect:
        if conn.total_changes - starting_changes != 2 * len(operations):
            raise RuntimeError("redirect repair changed rows outside its approved scope")
        verify_redirect_context(runtime_root, plan, approval_id, graph_path)
    conn.commit()
    return applied_receipts, nodes_merged, assertions_superseded


def apply_consolidation_plan(
    graph_path: Path,
    *,
    plan: Dict[str, Any],
    approval_id: str,
    run_id: str,
    event_timestamp: Optional[str] = None,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Applies consolidation plan atomically through GovernedStorageContext with foreign keys ON."""
    _assert_plan_digest(plan)
    graph_path = Path(graph_path).resolve()
    redirect = is_redirect_plan(plan)
    if redirect:
        verify_redirect_context(runtime_root, plan, approval_id, graph_path)
    before_hash = sha256_file(graph_path)

    if before_hash != plan["base_graph_sha256"]:
        raise RuntimeError("graph consolidation plan is stale relative to target graph file")

    now = event_timestamp or utc_now()
    operations = plan.get("operations") or plan.get("proposed_operations") or []

    # Zero-op safety: do not modify graph if no operations exist
    if not operations:
        return {
            "status": "NOOP",
            "change_set_id": plan.get("plan_id", "plan-empty"),
            "approval_id": approval_id,
            "transaction_required": False,
            "operation_count": 0,
            "operations": [],
            "nodes_merged": 0,
            "assertions_superseded": 0,
            "graph_sha256_before": before_hash,
            "graph_sha256_after": before_hash,
            "integrity": sqlite_integrity(graph_path),
            "committed_at": now,
        }

    plan_id = plan["plan_id"]
    conn = GovernedStorageContext.connect(graph_path)
    conn.row_factory = sqlite3.Row

    try:
        applied_receipts, nodes_merged, assertions_superseded = _execute_plan_transaction(
            conn,
            operations,
            now=now,
            approval_id=approval_id,
            run_id=run_id,
            plan_id=plan_id,
            plan=plan,
            runtime_root=runtime_root,
            graph_path=graph_path,
            redirect=redirect,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    integrity = sqlite_integrity(graph_path)
    after_hash = sha256_file(graph_path)

    if integrity.get("integrity_check") != "ok" or integrity.get("foreign_key_violation_count", 0) > 0:
        raise RuntimeError(f"Post-consolidation SQLite integrity check failed: {integrity}")

    if after_hash == before_hash:
        raise RuntimeError("Mutating consolidation operations produced zero graph SHA256 change")

    return {
        "status": "COMMITTED",
        "change_set_id": plan_id,
        "approval_id": approval_id,
        "transaction_required": True,
        "operation_count": len(applied_receipts),
        "operations": applied_receipts,
        "redirects_flattened": len(applied_receipts) if redirect else 0,
        "nodes_merged": nodes_merged,
        "assertions_superseded": assertions_superseded,
        "graph_sha256_before": before_hash,
        "graph_sha256_after": after_hash,
        "integrity": integrity,
        "committed_at": now,
    }
