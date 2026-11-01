"""Conflict detection and change-set rebasing for concurrent releases."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..core import digest, seal_artifact, write_json
from ..graph_delta_core.delta import build_planned_graph_delta
from ..storage.governed_context import GovernedStorageContext

logger = logging.getLogger("dual_pipeline.release_core.delta_rebase")


class ConcurrentMutationConflictError(RuntimeError):
    """Raised when an intake run conflicts with mutations in the active graph generation."""


def _detect_assertion_conflicts(
    connection: sqlite3.Connection,
    operations: list[dict[str, Any]],
) -> list[str]:
    conflicts: list[str] = []
    for op in operations:
        target_id = op.get("target_id")
        if not target_id:
            continue
        cur = connection.execute(
            "SELECT 1 FROM assertion WHERE assertion_id = ?", (target_id,)
        ).fetchone()
        if cur:
            if op.get("idempotent_upsert") or op.get("allow_existing"):
                continue
            conflicts.append(
                f"Target assertion ID '{target_id}' already exists in active graph generation"
            )
    return conflicts


def _detect_entity_conflicts(
    connection: sqlite3.Connection,
    operations: list[dict[str, Any]],
    payloads: dict[str, Any] | None = None,
) -> list[str]:
    conflicts: list[str] = []
    for op in operations:
        target_id = op.get("target_id")
        if not target_id:
            continue
        cur = connection.execute(
            "SELECT node_type, canonical_name, active FROM node WHERE node_id = ?", (target_id,)
        ).fetchone()
        if cur:
            if op.get("idempotent_upsert") or op.get("allow_existing"):
                p = (payloads or {}).get(op.get("operation_id"), {})
                ent = p.get("entity") or {}
                expected_name = ent.get("canonical_name") or ent.get("reported_name")
                expected_type = "external_concept" if p.get("entity_kind") == "external" else ent.get("node_type")
                if (
                    (not expected_name or cur[1] == expected_name)
                    and (not expected_type or cur[0] == expected_type)
                    and int(cur[2] or 0) == 1
                ):
                    continue
            conflicts.append(
                f"Target entity ID '{target_id}' already exists in active graph generation"
            )
    return conflicts


def _detect_supersession_conflicts(
    connection: sqlite3.Connection,
    operations: list[dict[str, Any]],
) -> list[str]:
    conflicts: list[str] = []
    for op in operations:
        target_id = op.get("target_id")
        if not target_id:
            continue
        row = connection.execute(
            "SELECT currentness_state, superseded_at, superseded_by_assertion_id "
            "FROM assertion WHERE assertion_id = ?",
            (target_id,),
        ).fetchone()
        if not row:
            conflicts.append(
                f"Target assertion '{target_id}' for supersession not found in active graph"
            )
        else:
            state, sup_at, sup_by = row
            if state == "historical" or sup_at is not None or sup_by is not None:
                conflicts.append(
                    f"Target assertion '{target_id}' has already been superseded in active graph"
                )
    return conflicts


def _has_active_successor(connection: sqlite3.Connection, assertion_id: str) -> bool:
    cur = assertion_id
    visited: set[str] = set()
    while cur and cur not in visited:
        visited.add(cur)
        row = connection.execute(
            "SELECT currentness_state, superseded_by_assertion_id FROM assertion WHERE assertion_id = ?",
            (cur,),
        ).fetchone()
        if not row:
            return False
        if row[0] == "current":
            return True
        cur = row[1]
    return False


def _detect_evidence_conflicts(
    connection: sqlite3.Connection,
    operations: list[dict[str, Any]],
    payloads: dict[str, Any],
) -> list[str]:
    conflicts: list[str] = []
    for op in operations:
        op_id = op.get("operation_id")
        payload = payloads.get(op_id, {})
        target_id = op.get("target_id") or payload.get("assertion_id")
        if not target_id:
            continue
        row = connection.execute(
            "SELECT currentness_state FROM assertion WHERE assertion_id = ?",
            (target_id,),
        ).fetchone()
        if not row:
            conflicts.append(
                f"Target assertion '{target_id}' for evidence addition not found in active graph"
            )
        elif row[0] == "historical" and not _has_active_successor(connection, target_id):
            conflicts.append(
                f"Target assertion '{target_id}' for evidence addition is already superseded in active graph"
            )
    return conflicts


def detect_mutation_conflicts(
    change_set: dict[str, Any] | None,
    active_graph_path: Path,
) -> list[str]:
    """Deterministically check if a proposed change set collides with active graph state."""
    if change_set is None:
        return []
    operations = change_set.get("operations")
    if not isinstance(operations, list) or not operations:
        return []
    extensions = change_set.get("extensions") or {}
    payloads = extensions.get("com.partner_advisory.nee.operation-payloads") or {}

    create_assertion_ops = [
        op for op in operations
        if op.get("operation") in {
            "add_candidate_assertion",
            "add_approved_assertion",
            "add_relationship",
            "add_action_item",
        }
    ]
    create_entity_ops = [
        op for op in operations
        if op.get("operation") == "create_entity"
    ]
    supersede_ops = [
        op for op in operations
        if op.get("operation") == "supersede_assertion"
    ]
    evidence_ops = [
        op for op in operations
        if op.get("operation") == "add_evidence"
    ]

    resolved_path = Path(active_graph_path).expanduser().resolve()
    if not resolved_path.is_file():
        return [f"Active graph path does not exist: {resolved_path}"]

    connection = GovernedStorageContext.connect(resolved_path, readonly=True)
    try:
        conflicts: list[str] = []
        conflicts.extend(_detect_assertion_conflicts(connection, create_assertion_ops))
        conflicts.extend(_detect_entity_conflicts(connection, create_entity_ops, payloads))
        conflicts.extend(_detect_supersession_conflicts(connection, supersede_ops))
        conflicts.extend(_detect_evidence_conflicts(connection, evidence_ops, payloads))
        return conflicts
    finally:
        connection.close()


def _component_needs_rebase(
    component: dict[str, Any],
    summary: dict[str, Any],
    proposed: dict[str, Any] | None,
    active_hash: str | None,
    active_graph_path: Path,
) -> bool:
    if summary.get("graph_sha256_before") != active_hash:
        return True
    if proposed is not None and proposed.get("base_graph_hash") != active_hash:
        return True
    expected_delta = build_planned_graph_delta(
        bundle=component["bundle"],
        change_set=proposed,
        graph_path=active_graph_path,
    )
    return bool(component.get("planned_graph_delta") != expected_delta)


def rebase_component_run(
    component: dict[str, Any],
    active_graph_path: Path,
    active_pair: dict[str, Any],
) -> bool:
    """Rebase a non-conflicting intake component run onto the latest active graph generation.

    Returns True if rebase occurred, or False if the run was already current.
    Raises ConcurrentMutationConflictError if any true collision is detected.
    """
    summary = component.get("summary", {})
    proposed = component.get("proposed")
    active_hash = active_pair.get("graph_sha256")
    active_snapshot = active_pair.get("graph_snapshot")
    active_gen = active_pair.get("generation_id")

    if not _component_needs_rebase(
        component, summary, proposed, active_hash, active_graph_path
    ):
        return False

    conflicts = detect_mutation_conflicts(proposed, active_graph_path)
    if conflicts:
        run_id = component.get("run_id", "unknown")
        raise ConcurrentMutationConflictError(
            f"Concurrent mutation conflict on run '{run_id}': " + "; ".join(conflicts)
        )

    if proposed is not None:
        proposed["base_graph_hash"] = active_hash
        proposed["base_graph_snapshot"] = active_snapshot
        proposed = seal_artifact(proposed)
        component["proposed"] = proposed

    new_delta = build_planned_graph_delta(
        bundle=component["bundle"],
        change_set=proposed,
        graph_path=active_graph_path,
    )
    component["planned_graph_delta"] = new_delta

    summary["graph_sha256_before"] = active_hash
    summary["graph_delta_summary_digest"] = digest(new_delta)
    summary["graph_change_table"] = new_delta.get("table")
    if active_gen:
        summary["model_or_index_generation"] = active_gen
    if "intake_payload" in component and isinstance(component["intake_payload"], dict) and active_gen:
        component["intake_payload"]["model_or_index_generation"] = active_gen

    run_dir = Path(component["run_dir"]) if component.get("run_dir") else None
    if run_dir and run_dir.is_dir():
        if proposed is not None:
            write_json(run_dir / "graph_change_set.json", proposed)
        write_json(run_dir / "graph_delta_summary.json", new_delta)
        if (run_dir / "intake_summary.json").is_file():
            write_json(run_dir / "intake_summary.json", summary)

    logger.info(
        f"[DELTA-REBASE] Successfully rebased run '{component.get('run_id')}' onto active generation {active_gen} ({active_hash[:12] if active_hash else 'unknown'})."
    )
    return True
