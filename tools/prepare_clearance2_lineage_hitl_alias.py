#!/usr/bin/env python
"""Prepare, but never apply, clearance-2 remediation after-states.

The plan quarantines assertions whose non-legacy extraction lineage is absent,
records the one incomplete HITL assignment for human completion, and proposes
deactivation of the one active alias whose confidence label is not registered.
All operations are draft-only and graph-mutation unauthorized.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, stable_id  # noqa: E402


def _row_state(row: sqlite3.Row) -> dict[str, Any]:
    fields = (
        "assertion_id",
        "assertion_type",
        "canonical_status",
        "canonical_eligible",
        "retrieval_eligible",
        "validation_state",
        "validation_priority",
        "validation_owner",
        "hitl_required",
        "origin_type",
        "extraction_run_id",
        "primary_source_id",
        "predicate",
        "currentness_state",
    )
    return {field: row[field] for field in fields}


def build_plan(graph: Path, strict_audit: Path | None) -> dict[str, Any]:
    graph = graph.resolve()
    audit: dict[str, Any] = {}
    if strict_audit is not None:
        audit = json.loads(strict_audit.resolve().read_text(encoding="utf-8"))

    connection = create_sqlite_conn(graph, readonly=True)
    try:
        missing_lineage_rows = connection.execute(
            """SELECT a.*
                 FROM assertion a
                WHERE a.origin_type<>'legacy_migration'
                  AND a.extraction_run_id IS NULL
                ORDER BY a.assertion_id"""
        ).fetchall()
        hitl_rows = connection.execute(
            """SELECT a.*
                 FROM assertion a
                WHERE a.hitl_required=1
                  AND (trim(coalesce(a.validation_priority,''))=''
                       OR trim(coalesce(a.validation_owner,''))='')
                ORDER BY a.assertion_id"""
        ).fetchall()
        alias_rows = connection.execute(
            """SELECT a.*, n.canonical_name
                 FROM canonical_alias a
                 LEFT JOIN node n ON n.node_id=a.entity_id
                WHERE a.active=1
                  AND NOT EXISTS (
                    SELECT 1
                      FROM controlled_value_registry c
                     WHERE c.vocabulary='confidence_label'
                       AND c.value=a.confidence_label
                       AND c.active=1
                  )
                ORDER BY a.canonical_alias_id"""
        ).fetchall()
    finally:
        connection.close()

    operations: list[dict[str, Any]] = []
    for row in missing_lineage_rows:
        before = _row_state(row)
        after = dict(before)
        if after["canonical_status"] == "canonical":
            after["canonical_status"] = "validation_pending"
        after["canonical_eligible"] = 0
        after["retrieval_eligible"] = 0
        after["validation_state"] = "needs_review"
        operations.append(
            {
                "operation_id": stable_id(
                    "clearance2-lineage-quarantine", row["assertion_id"], length=32
                ),
                "operation": "QUARANTINE_MISSING_NONLEGACY_EXTRACTION_LINEAGE",
                "record_id": row["assertion_id"],
                "reason": "Non-legacy assertion has no exact extraction_run_id.",
                "apply_ready": True,
                "before_state": before,
                "before_state_digest": digest(before),
                "proposed_after_state": after,
                "proposed_after_state_digest": digest(after),
            }
        )

    for row in hitl_rows:
        before = _row_state(row)
        after = dict(before)
        after.update(
            {
                "validation_priority": "P1",
                "validation_owner": "<HUMAN_OWNER_REQUIRED>",
                "validation_state": "needs_review",
            }
        )
        operations.append(
            {
                "operation_id": stable_id(
                    "clearance2-hitl-assignment", row["assertion_id"], length=32
                ),
                "operation": "HOLD_FOR_HUMAN_HITL_ASSIGNMENT",
                "record_id": row["assertion_id"],
                "reason": "HITL is required but priority or owner is incomplete.",
                "apply_ready": False,
                "human_decision_required": True,
                "before_state": before,
                "before_state_digest": digest(before),
                "proposed_after_state": after,
                "proposed_after_state_digest": digest(after),
            }
        )

    for row in alias_rows:
        before = {
            "canonical_alias_id": row["canonical_alias_id"],
            "normalized_alias": row["normalized_alias"],
            "display_alias": row["display_alias"],
            "entity_id": row["entity_id"],
            "canonical_name": row["canonical_name"],
            "confidence_label": row["confidence_label"],
            "active": row["active"],
            "review_state": row["review_state"],
            "preferred_flag": row["preferred_flag"],
            "ambiguity_state": row["ambiguity_state"],
        }
        after = dict(before)
        after.update({"active": 0, "review_state": "ambiguous_blocked", "preferred_flag": 0})
        operations.append(
            {
                "operation_id": stable_id(
                    "clearance2-alias-quarantine", row["canonical_alias_id"], length=32
                ),
                "operation": "QUARANTINE_UNREGISTERED_ACTIVE_ALIAS_CONFIDENCE",
                "record_id": row["canonical_alias_id"],
                "reason": "Active alias confidence label is absent from the active controlled-value registry.",
                "apply_ready": True,
                "before_state": before,
                "before_state_digest": digest(before),
                "proposed_after_state": after,
                "proposed_after_state_digest": digest(after),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_type": "clearance2_lineage_hitl_alias_remediation_draft",
        "plan_id": stable_id("clearance2-remediation", sha256_file(graph), length=32),
        "graph_path": str(graph),
        "base_graph_sha256": sha256_file(graph),
        "strict_audit_report_digest": audit.get("report_digest"),
        "strict_audit_blocking_codes": audit.get("blocking_codes", {}),
        "draft_only": True,
        "graph_mutation_authorized": False,
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "operations": operations,
        "summary": {
            "missing_lineage_quarantines": len(missing_lineage_rows),
            "hitl_assignment_holds": len(hitl_rows),
            "alias_quarantines": len(alias_rows),
            "apply_ready_operations": sum(1 for op in operations if op["apply_ready"]),
            "human_decision_required_operations": sum(
                1 for op in operations if op.get("human_decision_required")
            ),
        },
        "apply_prerequisites": [
            "Bind exact graph hash and plan digest to a graph-mutation approval.",
            "Supply a real human owner for every HOLD_FOR_HUMAN_HITL_ASSIGNMENT operation.",
            "Run the complete governed graph release lifecycle and completion gate.",
        ],
    }
    payload["canonical_digest"] = {"algorithm": "sha256", "value": digest(payload)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--strict-audit", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    plan = build_plan(args.graph, args.strict_audit)
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "DRAFT_ONLY", "summary": plan["summary"], "canonical_digest": plan["canonical_digest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
