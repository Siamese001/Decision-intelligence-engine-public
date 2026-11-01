"""Plan building and verification for source retirement."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..core import digest, sha256_file, stable_id
from .constants import SOURCE_RETIREMENT_POLICY_VERSION
from .control import validate_source_retirement_control
from .targets import _collect_targets


def build_source_retirement_plan(
    graph_path: Path, *, active_pair: dict[str, Any], control: object
) -> dict[str, Any]:
    """Build an exact active-graph plan without mutating the graph."""

    graph_path = Path(graph_path).resolve()
    graph_sha256 = sha256_file(graph_path)
    if graph_sha256 != active_pair["graph_sha256"]:
        raise RuntimeError("source-retirement plan must target the active graph hash")
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    connection.row_factory = sqlite3.Row
    try:
        normalized_control = validate_source_retirement_control(control)
        targets = _collect_targets(connection, normalized_control)
    finally:
        connection.close()
    removed = {key: len(value) for key, value in targets["remove"].items()}
    retained = {key: len(value) for key, value in targets["retain"].items()}
    plan = {
        "schema_version": "1.0.0",
        "plan_type": "source_retirement",
        "policy_version": SOURCE_RETIREMENT_POLICY_VERSION,
        "base_generation_id": active_pair["generation_id"],
        "base_graph_sha256": graph_sha256,
        "control": normalized_control,
        "control_digest": digest(normalized_control),
        "targets": targets,
        "operations": [
            {
                "operation_id": stable_id(
                    "source-retirement",
                    active_pair["generation_id"],
                    graph_sha256,
                    targets["target_source_id"],
                    targets["snapshot_digest"],
                    length=28,
                ),
                "operation": "retire_source_scope",
                "target_id": targets["target_source_id"],
                "before_state_hash": targets["snapshot_digest"],
                "reason": normalized_control["reason"],
            }
        ],
        "summary": {
            "target_source_id": targets["target_source_id"],
            "removed": removed,
            "retained": retained,
            "source_update_count": len(targets["source_updates"]),
            "source_version_update_count": len(targets["source_version_updates"]),
            "validation_items_removed": len(targets["derived"]["validation_ids"]),
            "graph_operation_count": 1,
        },
    }
    plan["plan_id"] = stable_id(
        "source-retirement-plan",
        plan["base_generation_id"],
        plan["base_graph_sha256"],
        plan["control_digest"],
        plan["targets"]["snapshot_digest"],
        length=28,
    )
    plan["plan_digest"] = digest(plan)
    return plan


def _assert_plan_digest(plan: dict[str, Any]) -> None:
    expected = dict(plan)
    supplied = expected.pop("plan_digest", None)
    if supplied != digest(expected):
        raise RuntimeError("source-retirement plan digest mismatch")
    if plan.get("plan_type") != "source_retirement":
        raise ValueError("unsupported source-retirement plan")
    if plan.get("policy_version") != SOURCE_RETIREMENT_POLICY_VERSION:
        raise ValueError("unsupported source-retirement policy version")
    if not isinstance(plan.get("control"), dict) or plan.get("control_digest") != digest(
        plan["control"]
    ):
        raise ValueError("source-retirement plan has an invalid control binding")
    if not isinstance(plan.get("operations"), list) or len(plan["operations"]) != 1:
        raise ValueError("source-retirement plan must contain one bounded operation")


