"""Planned and actual GraphDB delta builders and validators."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ..core import digest, sha256_file
from .constants import _COMPONENTS
from .counts import _graph_counts, _planned_increments, _row


def build_planned_graph_delta(
    *,
    bundle: dict[str, Any],
    change_set: dict[str, Any] | None,
    graph_path: Path,
) -> dict[str, Any]:
    """Build a source-bound, pre-mutation graph change table for X1--X3."""
    counts = _graph_counts(graph_path)
    increments = _planned_increments(change_set, graph_path)
    source = bundle.get("extensions", {}).get("com.partner_advisory.nee.source", {})
    if not isinstance(source, dict):
        raise ValueError("candidate bundle has no governed source metadata")
    table = [
        _row(
            component_key=component_key,
            graph_component=label,
            baseline=counts[component_key],
            planned_delta=increments[component_key],
        )
        for component_key, label, _table in _COMPONENTS
    ]
    value = {
        "schema_version": "1.0.0",
        "summary_type": "planned_graph_delta",
        "run_id": bundle["run_id"],
        "trace_id": bundle["trace_id"],
        "source_id": bundle["source_id"],
        "source_version_id": bundle["source_version_id"],
        "source_name": source.get("name") or bundle["source_id"],
        "graph_sha256_before": sha256_file(graph_path),
        "change_set_id": change_set["change_set_id"] if change_set else None,
        "change_set_digest": (
            change_set["canonical_digest"]["value"] if change_set else None
        ),
        "operation_count": len(change_set["operations"]) if change_set else 0,
        "operation_ids": (
            [operation["operation_id"] for operation in change_set["operations"]]
            if change_set
            else []
        ),
        "operation_counts": (
            dict(
                sorted(
                    Counter(
                        operation["operation"] for operation in change_set["operations"]
                    ).items()
                )
            )
            if change_set
            else {}
        ),
        "table": table,
        "validation": {
            "status": "PASS",
            "all_operations_accounted_for": sum(increments.values()) >= len(
                change_set["operations"] if change_set else []
            ),
            "candidate_lane_operations": increments["candidate_assertion"],
            "canonical_lane_operations": increments["canonical_assertion"],
            "unauthorized_side_effect_count": 0,
        },
    }
    if not value["validation"]["all_operations_accounted_for"]:
        raise RuntimeError("graph delta table does not account for every proposed operation")
    return value


def validate_planned_graph_delta(
    *,
    summary: dict[str, Any],
    bundle: dict[str, Any],
    change_set: dict[str, Any] | None,
    graph_path: Path,
) -> None:
    """Fail closed if an X1--X3 graph table differs from governed inputs."""
    expected = build_planned_graph_delta(
        bundle=bundle,
        change_set=change_set,
        graph_path=graph_path,
    )
    if summary != expected:
        raise RuntimeError("planned graph delta summary differs from governed intake inputs")


def build_actual_graph_delta(
    *,
    planned_summaries: list[dict[str, Any]],
    before_graph_path: Path,
    after_graph_path: Path,
) -> dict[str, Any]:
    """Verify actual W0 GraphDB counts against all sealed X1--X3 plans."""
    if not planned_summaries:
        raise ValueError("actual graph delta requires at least one planned summary")
    before = _graph_counts(before_graph_path)
    after = _graph_counts(after_graph_path)
    expected_delta = {component: 0 for component, _label, _table in _COMPONENTS}
    for summary in planned_summaries:
        if summary.get("validation", {}).get("status") != "PASS":
            raise RuntimeError("unvalidated planned graph delta cannot be released")
        if summary.get("graph_sha256_before") != sha256_file(before_graph_path):
            raise RuntimeError("planned graph delta is stale relative to the active graph")
        rows = summary.get("table")
        if not isinstance(rows, list) or len(rows) != len(_COMPONENTS):
            raise RuntimeError("planned graph delta table is incomplete")
        for component_key, _label, _table in _COMPONENTS:
            row = next(
                (
                    value
                    for value in rows
                    if value.get("component_key") == component_key
                ),
                None,
            )
            if not isinstance(row, dict) or row.get("baseline") != before[component_key]:
                raise RuntimeError("planned graph delta baseline does not match the active graph")
            expected_delta[component_key] += int(row.get("planned_delta", -1))
    table = []
    for component_key, label, _table in _COMPONENTS:
        actual_delta = after[component_key] - before[component_key]
        planned_delta = expected_delta[component_key]
        if actual_delta != planned_delta:
            raise RuntimeError(
                f"actual GraphDB {component_key} delta {actual_delta} differs from "
                f"planned {planned_delta}"
            )
        table.append(
            {
                **_row(
                    component_key=component_key,
                    graph_component=label,
                    baseline=before[component_key],
                    planned_delta=planned_delta,
                ),
                "actual_after": after[component_key],
                "actual_delta": actual_delta,
                "status": "PASS",
            }
        )
    return {
        "schema_version": "1.0.0",
        "summary_type": "actual_graph_delta",
        "status": "PASS",
        "graph_sha256_before": sha256_file(before_graph_path),
        "graph_sha256_after": sha256_file(after_graph_path),
        "run_ids": [summary["run_id"] for summary in planned_summaries],
        "planned_summary_digests": [digest(summary) for summary in planned_summaries],
        "table": table,
    }
