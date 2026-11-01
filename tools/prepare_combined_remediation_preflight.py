#!/usr/bin/env python3
"""Build a non-mutating, approval-ready Sherpa/status remediation preflight.

The output binds the active pair, every W2 source-retirement control, and the
W5 status-source inventory.  The mutations are exercised only on a temporary
copy of the active graph, which is deleted before this command returns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

from dual_pipeline.core import (
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    read_json,
    sha256_file,
    utc_now,
    write_json_exclusive,
)
from dual_pipeline.project_status_reclassification import (
    apply_project_status_reclassification_plan,
    build_project_status_reclassification_plan,
)
from dual_pipeline.source_retirement import (
    apply_source_retirement_plan,
    build_source_retirement_plan,
)

PREVIEW_EVENT_TIMESTAMP = "2000-01-01T00:00:00Z"
CONTROL_NAME = "w7-combined-remediation-control.json"
PREFLIGHT_NAME = "w7-combined-remediation-preflight.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--wave2-root", required=True, type=Path)
    parser.add_argument("--w5-inventory", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def _status_control(*, graph_path: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    connection = create_sqlite_conn(graph_path, readonly=True)
    try:
        targets: list[dict[str, Any]] = []
        for item in inventory["unambiguous_active_status_sources"]:
            source = connection.execute(
                "SELECT source_type,source_name,source_posture,notes FROM source WHERE source_id=?",
                (item["source_id"],),
            ).fetchone()
            version = connection.execute(
                """SELECT scope_text,posture_text,content_hash FROM source_version
                   WHERE source_version_id=?""",
                (item["source_version_id"],),
            ).fetchone()
            assertion_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT assertion_id FROM assertion WHERE primary_source_id=? ORDER BY assertion_id",
                    (item["source_id"],),
                )
            ]
            assertion_digest = hashlib.sha256(
                "\n".join(assertion_ids).encode("utf-8")
            ).hexdigest()
            if (
                source is None
                or version is None
                or version["content_hash"] != item["content_sha256"]
                or len(assertion_ids) != item["assertion_count"]
                or assertion_digest != item["assertion_id_digest"]
            ):
                raise RuntimeError("W5 status-source inventory drifted: " + item["source_id"])
            targets.append(
                {
                    "source_id": item["source_id"],
                    "source_version_id": item["source_version_id"],
                    "expected_content_sha256": item["content_sha256"],
                    "expected_source_type": source["source_type"],
                    "expected_source_name": source["source_name"],
                    "expected_source_posture": source["source_posture"],
                    "expected_notes": source["notes"],
                    "expected_scope_text": version["scope_text"],
                    "expected_version_posture_text": version["posture_text"],
                    "scope_id": item["scope_id"],
                    "as_of_date": item["as_of_date"],
                    "assertion_ids": assertion_ids,
                }
            )
    finally:
        connection.close()
    return {
        "schema_version": "1.0.0",
        "targets": targets,
        "reason": (
            "Project-status sources are dated, ephemeral citation evidence and cannot "
            "remain timeless graph facts."
        ),
    }


def _ordered_retirement_controls(
    *, wave2_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    manifest = read_json(wave2_root / "control-manifest.json")
    controls_by_source: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, str]] = []
    removal_owner: dict[tuple[str, str], str] = {}
    for target in manifest["targets"]:
        path = Path(target["control"]).resolve()
        control = read_json(path)
        source_id = control.get("target_source_id")
        if source_id != target["source_id"] or source_id in controls_by_source:
            raise RuntimeError("W2 source-retirement control binding is invalid")
        controls_by_source[source_id] = control
        bindings.append(
            {
                "source_id": source_id,
                "control_path": str(path),
                "control_sha256": sha256_file(path),
            }
        )
        for object_type, ids in control["remove"].items():
            for object_id in ids:
                key = (object_type, object_id)
                if key in removal_owner:
                    raise RuntimeError("W2 controls remove the same object twice: " + str(key))
                removal_owner[key] = source_id

    # A detachment needs its upstream record to exist.  Therefore the control
    # that detaches it must precede the control that removes that record.
    predecessors: dict[str, set[str]] = {
        source_id: set() for source_id in controls_by_source
    }
    for source_id, control in controls_by_source.items():
        for detachment in control["supersession_detachments"]:
            owner = removal_owner.get(
                (detachment["object_type"], detachment["object_id"])
            )
            if owner is not None and owner != source_id:
                predecessors[owner].add(source_id)

    ordered_sources: list[str] = []
    remaining = {source_id: set(values) for source_id, values in predecessors.items()}
    while remaining:
        ready = sorted(source_id for source_id, values in remaining.items() if not values)
        if not ready:
            raise RuntimeError(
                "W2 source-retirement dependency cycle: "
                + json.dumps(
                    {source_id: sorted(values) for source_id, values in remaining.items()},
                    sort_keys=True,
                )
            )
        for source_id in ready:
            ordered_sources.append(source_id)
            remaining.pop(source_id)
        for values in remaining.values():
            values.difference_update(ready)
    bindings.sort(key=lambda value: value["source_id"])
    return [controls_by_source[source_id] for source_id in ordered_sources], bindings


def _run(args: argparse.Namespace) -> dict[str, Any]:
    runtime_root = args.runtime_root.resolve()
    wave2_root = args.wave2_root.resolve()
    inventory_path = args.w5_inventory.resolve()
    output_dir = args.output_dir.resolve()
    control_path = output_dir / CONTROL_NAME
    preflight_path = output_dir / PREFLIGHT_NAME
    if output_dir.exists():
        raise FileExistsError("W7 output directory already exists")
    active_pair = read_json(runtime_root / "active_pair.json")
    active_graph = (runtime_root / active_pair["graph_path"]).resolve()
    inventory = read_json(inventory_path)
    if (
        inventory["base_active_pair"]["graph_sha256"] != active_pair["graph_sha256"]
        or sha256_file(active_graph) != active_pair["graph_sha256"]
    ):
        raise RuntimeError("W5 inventory or active graph drifted")
    status_control = _status_control(graph_path=active_graph, inventory=inventory)
    retirement_controls, retirement_bindings = _ordered_retirement_controls(
        wave2_root=wave2_root
    )
    retired_sources = {control["target_source_id"] for control in retirement_controls}
    status_sources = {target["source_id"] for target in status_control["targets"]}
    if retired_sources & status_sources:
        raise RuntimeError("retirement and retained status source scopes overlap")

    combined_control: dict[str, Any] = {
        "schema_version": "1.0.0",
        "control_type": "combined_sherpa_retirement_and_project_status_demotion",
        "base_active_pair": {
            field: active_pair[field]
            for field in (
                "generation_id",
                "graph_sha256",
                "sidecar_sha256",
                "embedding_backend",
                "model_id",
                "model_revision",
                "model_manifest_digest",
                "production_qualified",
            )
        }
        | {"runtime_root": str(runtime_root)},
        "source_retirement_execution_order": [
            control["target_source_id"] for control in retirement_controls
        ],
        "source_retirement_controls": retirement_controls,
        "project_status_reclassification_control": status_control,
        "reason": (
            "Remove stale Sherpa source scope and demote bound EDO/NEA project-status "
            "data to dated, citation-only candidate evidence in one future approval-bound release."
        ),
    }
    combined_control["canonical_digest"] = digest(combined_control)

    components: list[dict[str, Any]] = []
    removal_totals: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix="nee-w7-combined-preflight-") as directory:
        stage_graph = Path(directory) / "graph.sqlite"
        exact_file_copy(active_graph, stage_graph)
        stage_pair = dict(active_pair)
        stage_pair["graph_sha256"] = sha256_file(stage_graph)
        for control in retirement_controls:
            plan = build_source_retirement_plan(
                stage_graph, active_pair=stage_pair, control=control
            )
            receipt = apply_source_retirement_plan(
                stage_graph,
                plan=plan,
                approval_id="preflight-only",
                run_id="preflight-only",
                event_timestamp=PREVIEW_EVENT_TIMESTAMP,
            )
            removal_totals.update(plan["summary"]["removed"])
            components.append(
                {
                    "component_type": "source_retirement",
                    "source_id": control["target_source_id"],
                    "plan_id": plan["plan_id"],
                    "plan_digest": plan["plan_digest"],
                    "graph_sha256_before": receipt["graph_sha256_before"],
                    "graph_sha256_after": receipt["graph_sha256_after"],
                    "summary": plan["summary"],
                }
            )
            stage_pair["graph_sha256"] = receipt["graph_sha256_after"]
        status_plan = build_project_status_reclassification_plan(
            stage_graph, active_pair=stage_pair, control=status_control
        )
        status_receipt = apply_project_status_reclassification_plan(
            stage_graph,
            plan=status_plan,
            approval_id="preflight-only",
            run_id="preflight-only",
            event_timestamp=PREVIEW_EVENT_TIMESTAMP,
        )
        components.append(
            {
                "component_type": "project_status_reclassification",
                "source_id": None,
                "plan_id": status_plan["plan_id"],
                "plan_digest": status_plan["plan_digest"],
                "graph_sha256_before": status_receipt["graph_sha256_before"],
                "graph_sha256_after": status_receipt["graph_sha256_after"],
                "summary": status_plan["summary"],
            }
        )
        final_graph_sha256 = status_receipt["graph_sha256_after"]
        final_integrity = status_receipt["integrity"]

    preflight: dict[str, Any] = {
        "schema_version": "1.0.0",
        "status": "PASS",
        "preflight_only": True,
        "provider_calls": 0,
        "graph_mutations_to_active_pair": 0,
        "base_active_pair": combined_control["base_active_pair"],
        "control_path": str(control_path),
        "control_digest": combined_control["canonical_digest"],
        "retirement_control_bindings": retirement_bindings,
        "components": components,
        "combined_summary": {
            "retirement_source_count": len(retirement_controls),
            "retirement_removals": dict(sorted(removal_totals.items())),
            "status_source_count": status_plan["summary"]["source_count"],
            "status_assertion_count": status_plan["summary"]["assertion_count"],
            "status_claim_count": status_plan["summary"]["claim_count"],
            "status_edge_count": status_plan["summary"]["edge_count"],
            "status_search_document_count": status_plan["summary"]["search_document_count"],
            "status_before_counts": status_plan["summary"]["before_status_counts"],
            "future_release_component_count": len(components),
        },
        "staging_simulation": {
            "event_timestamp": PREVIEW_EVENT_TIMESTAMP,
            "final_graph_sha256": final_graph_sha256,
            "integrity": final_integrity,
            "notice": (
                "Temporary graph copy only; deleted after preflight. This is not an activated "
                "generation or release receipt."
            ),
        },
        "approval_boundary": {
            "directive_id": "sherpa-status-remediation-w7-20260804",
            "required": (
                "A new human approval must bind this exact control digest and the active "
                "BGE-M3 target before release preparation or activation."
            ),
            "old_w2_approval_reusable": False,
        },
        "created_at": utc_now(),
    }
    preflight["preflight_id"] = digest(
        {
            "control_digest": preflight["control_digest"],
            "base_graph": active_pair["graph_sha256"],
            "final_graph": final_graph_sha256,
        }
    )[:28]
    preflight["canonical_digest"] = digest(preflight)
    assert_safe_mutable_path(output_dir.parent, output_dir, label="W7 output directory")
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_exclusive(control_path, combined_control)
    write_json_exclusive(preflight_path, preflight)
    return {
        "status": "PASS",
        "control_path": str(control_path),
        "control_digest": combined_control["canonical_digest"],
        "preflight_path": str(preflight_path),
        "preflight_digest": preflight["canonical_digest"],
        "execution_order": combined_control["source_retirement_execution_order"],
        "combined_summary": preflight["combined_summary"],
        "staging_final_graph_sha256": final_graph_sha256,
        "graph_mutations_to_active_pair": 0,
    }


def main() -> int:
    result = _run(_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
