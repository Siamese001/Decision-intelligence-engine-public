"""Plan builder for candidate assertion cleanup."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..core import digest, sha256_file, stable_id
from .constants import (
    _CONTROLLED_VALUES,
    CLEANUP_POLICY_VERSION,
    PRESERVED_FUTURE_STATE_SOURCE_IDS,
)
from .helpers import _assertion_state, _disposition, _row_hash


def build_candidate_cleanup_plan(
    graph_path: Path, *, active_pair: dict[str, Any]
) -> dict[str, Any]:
    """Return an exact, baseline-bound cleanup plan without mutating GraphDB."""
    graph_path = Path(graph_path).resolve()
    graph_sha256 = sha256_file(graph_path)
    if graph_sha256 != active_pair["graph_sha256"]:
        raise RuntimeError("candidate cleanup plan must target the active graph hash")
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT assertion_id,canonical_status,currentness_state,canonical_eligible,
                      retrieval_eligible,validation_priority,validation_state,
                      primary_source_id,qualifiers_json
                 FROM assertion
                WHERE canonical_status='candidate'
                  AND currentness_state IN ('current','unknown')
                ORDER BY assertion_id"""
        ).fetchall()
        status_rows = {
            row["value"]: dict(row)
            for row in connection.execute(
                """SELECT vocabulary,value,definition,active,sort_order
                     FROM controlled_value_registry
                    WHERE vocabulary='canonical_status'
                      AND value IN ('validation_pending','evidence_only')"""
            )
        }
        max_sort_row = connection.execute(
            """SELECT coalesce(max(sort_order), 0) AS max_sort
                 FROM controlled_value_registry
                WHERE vocabulary='canonical_status'"""
        ).fetchone()
    finally:
        connection.close()

    operations: list[dict[str, Any]] = []
    for offset, value in enumerate(sorted(_CONTROLLED_VALUES), start=1):
        existing = status_rows.get(value)
        if existing is not None and int(existing["active"]) == 1:
            continue
        operations.append(
            {
                "operation_id": stable_id(
                    "candidate-cleanup-controlled-value",
                    graph_sha256,
                    value,
                    length=28,
                ),
                "operation": "register_controlled_value",
                "target_id": f"canonical_status:{value}",
                "before_state_hash": _row_hash(existing),
                "value": value,
                "definition": _CONTROLLED_VALUES[value],
                "sort_order": int(max_sort_row["max_sort"]) + offset,
            }
        )

    retained = 0
    disposition_counts: Counter[str] = Counter()
    for row in rows:
        target_status, target_validation_state, reason = _disposition(row)
        if target_status == "candidate":
            retained += 1
            continue
        disposition_counts[target_status] += 1
        before = _assertion_state(row)
        operations.append(
            {
                "operation_id": stable_id(
                    "candidate-backlog-cleanup",
                    graph_sha256,
                    row["assertion_id"],
                    target_status,
                    length=28,
                ),
                "operation": "reclassify_candidate_assertion",
                "target_id": row["assertion_id"],
                "before_state_hash": digest(before),
                "target_canonical_status": target_status,
                "target_validation_state": target_validation_state,
                "reason": reason,
            }
        )
    plan = {
        "schema_version": "1.0.0",
        "plan_type": "candidate_backlog_cleanup",
        "policy_version": CLEANUP_POLICY_VERSION,
        "base_generation_id": active_pair["generation_id"],
        "base_graph_sha256": graph_sha256,
        "preserved_future_state_source_ids": sorted(PRESERVED_FUTURE_STATE_SOURCE_IDS),
        "operations": operations,
        "summary": {
            "candidate_retrieval_assertions_before": len(rows),
            "future_state_candidates_retained": retained,
            "validation_pending": disposition_counts["validation_pending"],
            "evidence_only": disposition_counts["evidence_only"],
            "graph_operation_count": len(operations),
        },
    }
    plan["plan_id"] = stable_id(
        "candidate-backlog-cleanup-plan",
        plan["base_generation_id"],
        plan["base_graph_sha256"],
        operations,
        length=28,
    )
    plan["plan_digest"] = digest(plan)
    return plan
