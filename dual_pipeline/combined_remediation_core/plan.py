"""Plan builder for combined Sherpa/status remediation."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..core import digest, exact_file_copy, sha256_file, sqlite_integrity, stable_id, utc_now
from ..project_status_reclassification import (
    apply_project_status_reclassification_plan,
    build_project_status_reclassification_plan,
)
from ..source_retirement import (
    apply_source_retirement_plan,
    build_source_retirement_plan,
)
from .constants import COMBINED_REMEDIATION_POLICY_VERSION
from .validation import (
    _aggregate_counts,
    _assert_active_binding,
    _require_text,
    validate_combined_remediation_control,
)


def build_combined_remediation_plan(
    graph_path: Path,
    *,
    active_pair: dict[str, Any],
    runtime_root: Path,
    control: object,
    approval_id: str,
    run_id: str,
    event_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build an exact ordered multi-control plan using only a disposable copy."""
    normalized = validate_combined_remediation_control(control)
    _assert_active_binding(normalized, active_pair=active_pair, runtime_root=runtime_root)
    graph_path = Path(graph_path).resolve()
    base_hash = sha256_file(graph_path)
    if base_hash != active_pair["graph_sha256"]:
        raise RuntimeError("combined remediation plan must target the active graph hash")
    approval_id = _require_text(approval_id, label="approval_id")
    run_id = _require_text(run_id, label="run_id")
    planned_at = event_timestamp or utc_now()
    components: list[dict[str, Any]] = []
    retirement_counts: list[dict[str, int]] = []
    status_counts: list[dict[str, int]] = []
    with tempfile.TemporaryDirectory(prefix="nee-combined-remediation-") as temporary:
        staged_graph = Path(temporary) / "graph.sqlite"
        exact_file_copy(graph_path, staged_graph)
        staged_pair = dict(active_pair)
        for control_item in normalized["source_retirement_controls"]:
            staged_pair["graph_sha256"] = sha256_file(staged_graph)
            component_plan = build_source_retirement_plan(
                staged_graph, active_pair=staged_pair, control=control_item
            )
            receipt = apply_source_retirement_plan(
                staged_graph,
                plan=component_plan,
                approval_id=approval_id,
                run_id=run_id,
                event_timestamp=planned_at,
            )
            retirement_counts.append(receipt["removed"])
            components.append(
                {
                    "component_type": "source_retirement",
                    "target_source_id": control_item["target_source_id"],
                    "preflight_plan_digest": component_plan["plan_digest"],
                    "expected_plan_removed": component_plan["summary"]["removed"],
                    "expected_removed": receipt["removed"],
                    "expected_operation_count": component_plan["summary"]["graph_operation_count"],
                }
            )
        staged_pair["graph_sha256"] = sha256_file(staged_graph)
        status_plan = build_project_status_reclassification_plan(
            staged_graph,
            active_pair=staged_pair,
            control=normalized["project_status_reclassification_control"],
        )
        status_receipt = apply_project_status_reclassification_plan(
            staged_graph,
            plan=status_plan,
            approval_id=approval_id,
            run_id=run_id,
            event_timestamp=planned_at,
        )
        status_counts.append(status_receipt["updated"])
        components.append(
            {
                "component_type": "project_status_reclassification",
                "target_source_ids": [
                    target["source_id"]
                    for target in status_plan["targets"]["sources"]
                ],
                "preflight_plan_digest": status_plan["plan_digest"],
                "expected_updated": status_receipt["updated"],
                "expected_operation_count": status_plan["summary"]["graph_operation_count"],
            }
        )
        final_hash = sha256_file(staged_graph)
        integrity = sqlite_integrity(staged_graph)
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"]:
        raise RuntimeError("combined remediation temporary graph failed integrity validation")
    operation_count = sum(int(component["expected_operation_count"]) for component in components)
    plan = {
        "schema_version": "1.0.0",
        "plan_type": "combined_remediation",
        "policy_version": COMBINED_REMEDIATION_POLICY_VERSION,
        "base_generation_id": active_pair["generation_id"],
        "base_graph_sha256": base_hash,
        "approval_id": approval_id,
        "run_id": run_id,
        "event_timestamp": planned_at,
        "control": normalized,
        "control_digest": normalized["canonical_digest"],
        "components": components,
        "summary": {
            "source_retirement_component_count": len(normalized["source_retirement_controls"]),
            "project_status_component_count": 1,
            "project_status_source_count": len(status_plan["targets"]["sources"]),
            "graph_operation_count": operation_count,
            "source_retirement_removed": _aggregate_counts(retirement_counts),
            "project_status_updated": _aggregate_counts(status_counts),
            # SQLite file bytes can differ after equivalent delete/update
            # sequences because pages are reclaimed in allocation order.  The
            # semantic preflight is retained here for review, while execution
            # re-plans every component against its staged graph and validates
            # its exact source/row bindings before each write.
            "preflight_final_graph_sha256": final_hash,
        },
    }
    plan["plan_id"] = stable_id(
        "combined-remediation-plan",
        plan["base_generation_id"],
        base_hash,
        plan["control_digest"],
        approval_id,
        run_id,
        planned_at,
        [component["preflight_plan_digest"] for component in components],
        length=28,
    )
    plan["plan_digest"] = digest(plan)
    return plan
