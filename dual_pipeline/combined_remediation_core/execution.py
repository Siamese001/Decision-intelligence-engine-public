"""Execution, release preparation, and telemetry recording for combined remediation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import (
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from ..index import EmbeddingBackend, build_sidecar
from ..project_status_reclassification import (
    apply_project_status_reclassification_plan,
    build_project_status_reclassification_plan,
)
from ..release import ReleaseManager, _seal_receipt
from ..source_retirement import (
    apply_source_retirement_plan,
    build_source_retirement_plan,
)
from ..telemetry import RunRecorder
from .validation import _assert_active_binding, _assert_plan_digest


def _apply_source_retirement_component(
    graph_path: Path,
    staged_pair: dict[str, Any],
    component: dict[str, Any],
    controls_by_source: dict[str, Any],
    plan: dict[str, Any],
    approval_id: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_id = component["target_source_id"]
    component_plan = build_source_retirement_plan(
        graph_path,
        active_pair=staged_pair,
        control=controls_by_source[source_id],
    )
    if (
        component_plan["summary"]["removed"] != component["expected_plan_removed"]
        or component_plan["summary"]["graph_operation_count"] != component["expected_operation_count"]
    ):
        raise RuntimeError("combined remediation source-retirement preflight drifted")
    receipt = apply_source_retirement_plan(
        graph_path,
        plan=component_plan,
        approval_id=approval_id,
        run_id=run_id,
        event_timestamp=plan["event_timestamp"],
    )
    if receipt["removed"] != component["expected_removed"]:
        raise RuntimeError("combined remediation source-retirement outcome drifted")
    return component_plan, receipt


def _apply_status_reclassification_component(
    graph_path: Path,
    staged_pair: dict[str, Any],
    component: dict[str, Any],
    plan: dict[str, Any],
    approval_id: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    component_plan = build_project_status_reclassification_plan(
        graph_path,
        active_pair=staged_pair,
        control=plan["control"]["project_status_reclassification_control"],
    )
    actual_sources = [target["source_id"] for target in component_plan["targets"]["sources"]]
    if (
        actual_sources != component["target_source_ids"]
        or component_plan["summary"]["graph_operation_count"] != component["expected_operation_count"]
    ):
        raise RuntimeError("combined remediation status-reclassification preflight drifted")
    receipt = apply_project_status_reclassification_plan(
        graph_path,
        plan=component_plan,
        approval_id=approval_id,
        run_id=run_id,
        event_timestamp=plan["event_timestamp"],
    )
    if receipt["updated"] != component["expected_updated"]:
        raise RuntimeError("combined remediation status-reclassification outcome drifted")
    return component_plan, receipt


def apply_combined_remediation_plan(
    graph_path: Path, *, plan: dict[str, Any], approval_id: str, run_id: str
) -> dict[str, Any]:
    """Apply a preflighted plan only to a staged generation graph."""
    _assert_plan_digest(plan)
    if approval_id != plan["approval_id"] or run_id != plan["run_id"]:
        raise RuntimeError("combined remediation approval or run binding does not match the plan")
    graph_path = Path(graph_path).resolve()
    before_hash = sha256_file(graph_path)
    if before_hash != plan["base_graph_sha256"]:
        raise RuntimeError("combined remediation plan is stale relative to its graph")
    receipts: list[dict[str, Any]] = []
    controls_by_source = {
        control["target_source_id"]: control
        for control in plan["control"]["source_retirement_controls"]
    }
    for component in plan["components"]:
        staged_pair = {
            "generation_id": plan["base_generation_id"],
            "graph_sha256": sha256_file(graph_path),
        }
        ctype = component["component_type"]
        if ctype == "source_retirement":
            c_plan, receipt = _apply_source_retirement_component(
                graph_path, staged_pair, component, controls_by_source, plan, approval_id, run_id
            )
        elif ctype == "project_status_reclassification":
            c_plan, receipt = _apply_status_reclassification_component(
                graph_path, staged_pair, component, plan, approval_id, run_id
            )
        else:
            raise ValueError("combined remediation has an unsupported component type")

        receipts.append(
            {
                "component_type": ctype,
                "preflight_plan_digest": component["preflight_plan_digest"],
                "applied_plan_id": c_plan["plan_id"],
                "applied_plan_digest": c_plan["plan_digest"],
                "receipt": receipt,
            }
        )
    integrity = sqlite_integrity(graph_path)
    after_hash = sha256_file(graph_path)
    if (
        integrity["integrity_check"] != "ok"
        or integrity["foreign_key_violation_count"]
        or after_hash == before_hash
    ):
        raise RuntimeError("combined remediation integrity or graph-change check failed")
    operations = [
        operation
        for component in receipts
        for operation in component["receipt"].get("operations", [])
    ]
    if len(operations) != plan["summary"]["graph_operation_count"]:
        raise RuntimeError("combined remediation operation count drifted")
    return {
        "status": "COMMITTED",
        "change_set_id": plan["plan_id"],
        "approval_id": approval_id,
        "transaction_required": True,
        "operation_count": len(operations),
        "operations": operations,
        "component_receipts": receipts,
        "graph_sha256_before": before_hash,
        "graph_sha256_after": after_hash,
        "integrity": integrity,
        "committed_at": plan["event_timestamp"],
    }


def prepare_combined_remediation_release(
    *,
    manager: ReleaseManager,
    backend: EmbeddingBackend,
    plan: dict[str, Any],
    run_id: str,
    trace_id: str,
    source_id: str,
    source_version_id: str,
    intake_summary: dict[str, Any],
    approval_id: str,
) -> tuple[dict[str, Any], Path]:
    """Build and seal one new graph/index generation from the combined plan."""
    _assert_plan_digest(plan)
    if approval_id != plan["approval_id"] or run_id != plan["run_id"]:
        raise RuntimeError("combined remediation release does not match its approved plan")
    active = manager.active_pair()
    _assert_active_binding(plan["control"], active_pair=active, runtime_root=manager.runtime_root)
    if active["embedding_backend"] != backend.backend_id or (
        active["production_qualified"] != backend.production_qualified
    ):
        raise RuntimeError("combined remediation backend drifted from the active pair")
    if intake_summary.get("status") != "COMPLETED" or intake_summary.get("run_id") != run_id:
        raise RuntimeError("combined remediation requires a completed control intake")
    if intake_summary.get("source_id") != source_id or intake_summary.get("trace_id") != trace_id:
        raise RuntimeError("combined remediation intake run is not bound to its control source")

    generation_id = stable_id(
        "generation-combined-remediation",
        active["generation_id"],
        plan["plan_digest"],
        backend.backend_id,
        backend.model_revision,
        backend.model_manifest_digest,
        length=28,
    )
    generation_dir = manager.generations_dir / generation_id
    receipt_path = manager.releases_dir / f"release-{generation_id}.json"
    if generation_dir.exists() or receipt_path.exists():
        raise FileExistsError("combined remediation generation already exists")
    manager._verify_backend_snapshot(backend)
    assert_safe_mutable_path(manager.runtime_root, generation_dir, label="combined remediation generation")
    assert_safe_mutable_path(manager.runtime_root, receipt_path, label="combined remediation receipt")
    generation_dir.mkdir(parents=True)
    graph_path = generation_dir / "graph.sqlite"
    sidecar_path = generation_dir / "sidecar.sqlite"
    exact_file_copy((manager.runtime_root / active["graph_path"]).resolve(), graph_path)
    graph_receipt = apply_combined_remediation_plan(
        graph_path, plan=plan, approval_id=approval_id, run_id=run_id
    )
    graph_hash = sha256_file(graph_path)
    graph_snapshot = stable_id("graph-snapshot", graph_hash, length=24)
    active_sidecar_path = (
        (manager.runtime_root / active["sidecar_path"]).resolve() if active.get("sidecar_path") else None
    )
    index_receipt = build_sidecar(
        graph_path=graph_path,
        sidecar_path=sidecar_path,
        generation_id=generation_id,
        graph_snapshot=graph_snapshot,
        backend=backend,
        prior_sidecar_path=active_sidecar_path,
    )
    manager._verify_backend_snapshot(backend)
    next_pair = {
        "schema_version": "1.0.0",
        "generation_id": generation_id,
        "graph_snapshot": graph_snapshot,
        "graph_sha256": graph_hash,
        "sidecar_sha256": index_receipt["sidecar_sha256"],
        "graph_path": manager._relative(graph_path),
        "sidecar_path": manager._relative(sidecar_path),
        "embedding_backend": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest,
        "production_qualified": backend.production_qualified,
        "previous_generation_id": active["generation_id"],
        "activated_at": None,
    }
    pair_validation = manager.validate_pair(next_pair)
    write_json(generation_dir / "pair_manifest.json", next_pair)
    operation_ids = [
        operation["operation_id"] for operation in graph_receipt["operations"]
    ]
    component = {
        "change_set_digest": plan["plan_digest"],
        "change_set_id": plan["plan_id"],
        "intake_summary_digest": digest(intake_summary),
        "operation_ids": operation_ids,
        "run_id": run_id,
        "source_id": source_id,
        "source_version_id": source_version_id,
        "trace_id": trace_id,
    }
    receipt = _seal_receipt(
        {
            "schema_version": "1.0.0",
            "receipt_id": stable_id(
                "release-receipt-combined-remediation",
                generation_id,
                plan["plan_digest"],
                length=28,
            ),
            "status": "PREPARED",
            "release_kind": "combined_sherpa_status_remediation",
            "run_id": run_id,
            "trace_id": trace_id,
            "source_id": source_id,
            "approval_id": approval_id,
            "change_set_id": plan["plan_id"],
            "change_set_digest": plan["plan_digest"],
            "component_runs": [component],
            "previous_pair": active,
            "next_pair": next_pair,
            "graph_write_receipt": graph_receipt,
            "graph_delta_summary": {
                "schema_version": "1.0.0",
                "summary_type": "combined_sherpa_status_remediation_delta",
                "source_retirement_execution_order": plan["control"][
                    "source_retirement_execution_order"
                ],
                "project_status_source_ids": [
                    target["source_id"]
                    for target in plan["control"]["project_status_reclassification_control"][
                        "targets"
                    ]
                ],
                "source_retirement_removed": plan["summary"]["source_retirement_removed"],
                "project_status_updated": plan["summary"]["project_status_updated"],
            },
            "embedding_generation_receipt": index_receipt,
            "pair_validation": pair_validation,
            "production_qualified": backend.production_qualified,
            "authorization": None,
            "prepared_at": utc_now(),
        }
    )
    write_json_exclusive(receipt_path, receipt)
    return receipt, receipt_path


def record_combined_remediation_release_events(
    *, runtime_root: Path, receipt: dict[str, Any]
) -> dict[str, Any]:
    """Record the exact W0/E0 receipts before activation."""
    component = receipt["component_runs"][0]
    recorder = RunRecorder(
        runtime_root,
        run_id=component["run_id"],
        trace_id=component["trace_id"],
        use_case="intake",
        graph_snapshot=receipt["next_pair"]["graph_snapshot"],
        model_or_index_generation=receipt["next_pair"]["generation_id"],
    )
    correlation = {
        "source_id": component["source_id"],
        "candidate_id": None,
        "query_id": None,
        "graph_operation_id": None,
        "hitl_item_id": None,
    }
    recorder.record(
        stage="W0",
        event_type="graph_write_receipt",
        payload=receipt["graph_write_receipt"],
        correlation=correlation,
        graph_snapshot=receipt["next_pair"]["graph_snapshot"],
        generation_id=receipt["next_pair"]["generation_id"],
        model_id=receipt["next_pair"]["model_id"],
        record_count=receipt["graph_write_receipt"]["operation_count"],
    )
    recorder.record(
        stage="E0",
        event_type="embedding_generation_receipt",
        payload=receipt["embedding_generation_receipt"],
        correlation=correlation,
        graph_snapshot=receipt["next_pair"]["graph_snapshot"],
        generation_id=receipt["next_pair"]["generation_id"],
        model_id=receipt["next_pair"]["model_id"],
        record_count=receipt["embedding_generation_receipt"]["record_count"],
    )
    return recorder.audit(
        ("U0", "L1", "L0", "C0.1", "C0.2", "C0.3", "X1", "X2", "X3", "L4", "W0", "E0")
    )
