"""Exact, read-only redirect proposals and bounded transactional row updates."""
from __future__ import annotations

import json
from pathlib import Path

from ..core import digest, read_json, sha256_file, stable_id, utc_now
from ..storage.governed_context import GovernedStorageContext
from .constants import CONSOLIDATION_POLICY_VERSION
from .plan import _assert_plan_digest

KIND = "redirect_repair_plan"
OP_KEYS = {"operation_id", "operation_type", "from_node_id", "old_target", "new_target"}


def is_redirect_plan(plan):
    """Detect the operation even when a caller removes the proposal discriminator."""
    if plan.get("proposal_type") == KIND:
        return True
    return any(
        isinstance(op, dict) and op.get("operation_type") == "flatten_redirect"
        for key in ("operations", "proposed_operations")
        for op in (plan.get(key) or [])
    )


def redirect_operations(plan):
    _assert_plan_digest(plan)
    ops = plan.get("operations")
    if plan.get("proposal_type") != KIND or not isinstance(ops, list) or not ops:
        raise ValueError("redirect repair requires a nonempty exact proposal")
    if plan.get("proposed_operations") != ops:
        raise ValueError("redirect operation lists disagree")
    origins, identifiers = set(), set()
    for op in ops:
        if not isinstance(op, dict) or set(op) != OP_KEYS:
            raise ValueError("invalid redirect operation fields")
        if any(not isinstance(value, str) or not value for value in op.values()):
            raise ValueError("redirect operation fields must be nonempty strings")
        if op["operation_type"] != "flatten_redirect":
            raise ValueError("mixed redirect repair operations are forbidden")
        if op["from_node_id"] in origins or op["operation_id"] in identifiers:
            raise ValueError("duplicate redirect operation")
        origins.add(op["from_node_id"])
        identifiers.add(op["operation_id"])
    return ops


def _terminal(conn, origin):
    seen, current, hops = set(), origin, 0
    while True:
        if current in seen:
            raise ValueError("redirect cycle")
        seen.add(current)
        node = conn.execute("SELECT active FROM node WHERE node_id=?", (current,)).fetchone()
        if node is None:
            raise ValueError("redirect target node missing")
        row = conn.execute(
            "SELECT to_node_id FROM entity_redirect WHERE from_node_id=? AND active=1",
            (current,),
        ).fetchone()
        if row is None:
            if not node[0]:
                raise ValueError("redirect terminal node inactive")
            return current, hops
        current, hops = row[0], hops + 1


def validate_redirect_rows(conn, operations):
    """Validate every original path before updating overlapping chains."""
    originals = {}
    for op in operations:
        row = conn.execute(
            "SELECT to_node_id, merge_event_id, active, created_at FROM entity_redirect "
            "WHERE from_node_id=?", (op["from_node_id"],),
        ).fetchone()
        if row is None or row[2] != 1 or row[0] != op["old_target"]:
            raise ValueError("redirect origin or old target does not match")
        terminal, hops = _terminal(conn, op["from_node_id"])
        if hops < 2 or terminal != op["new_target"] or terminal == op["old_target"]:
            raise ValueError("repair must shorten the existing path to its terminal node")
        originals[op["operation_id"]] = {
            "to_node_id": row[0], "merge_event_id": row[1],
            "active": row[2], "created_at": row[3],
        }
    return originals


def build_redirect_repair_plan(runtime_root: Path, changes: list, backup_manifest: Path):
    """Bind a proposal to a runtime snapshot and its verified rollback manifest."""
    root = Path(runtime_root).resolve()
    pointer = root / "active_pair.json"
    active = read_json(pointer)
    ops = [{
        "operation_id": stable_id("flatten-redirect", change["from_node_id"], length=24),
        "operation_type": "flatten_redirect",
        **{key: change[key] for key in ("from_node_id", "old_target", "new_target")},
    } for change in changes]
    plan = {
        "schema_version": "1.0.0", "proposal_type": KIND,
        "plan_id": stable_id("redirect-repair", active["generation_id"], digest(ops), length=28),
        "runtime_root": str(root), "active_pair_sha256": sha256_file(pointer),
        "base_generation_id": active["generation_id"],
        "base_graph_sha256": active["graph_sha256"],
        "base_sidecar_sha256": active["sidecar_sha256"],
        "backup_manifest_path": str(Path(backup_manifest).resolve()),
        "backup_manifest_sha256": sha256_file(backup_manifest),
        "created_at_utc": utc_now(), "approval_required": True, "auto_apply": False,
        "operations": ops, "proposed_operations": ops,
    }
    plan["plan_digest"] = digest(plan)
    operations = redirect_operations(plan)
    with GovernedStorageContext.read_context(root / active["graph_path"]) as conn:
        validate_redirect_rows(conn, operations)
    return plan


def apply_redirect(conn, op, original, *, now, approval_id, run_id, plan_id):
    new = {**original, "to_node_id": op["new_target"]}
    cursor = conn.execute(
        "UPDATE entity_redirect SET to_node_id=? "
        "WHERE from_node_id=? AND to_node_id=? AND active=1 AND merge_event_id=?",
        (op["new_target"], op["from_node_id"], op["old_target"], original["merge_event_id"]),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("redirect changed during repair")
    conn.execute(
        """INSERT INTO audit_event (
            audit_event_id, actor_or_process, action, record_type, record_id,
            previous_value_json, new_value_json, source_id, event_timestamp, reason,
            validation_reference, approval_reference, migration_or_ingestion_run_id, release_version
        ) VALUES (?, 'graph_consolidation', 'flatten_redirect', 'entity_redirect',
                  ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        (stable_id("audit-flatten-redirect", plan_id, op["operation_id"], length=28),
         op["from_node_id"], json.dumps(original, sort_keys=True), json.dumps(new, sort_keys=True),
         now, "Shorten an existing redirect path; preserve the original identity decision",
         plan_id, approval_id, run_id, CONSOLIDATION_POLICY_VERSION),
    )
    return {**op, "status": "APPLIED", "merge_event_id": original["merge_event_id"]}
