"""Release preparation and telemetry recording for source retirement."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import (
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from ..index import EmbeddingBackend, build_sidecar
from ..release import ReleaseManager, _seal_receipt
from ..telemetry import RunRecorder
from .apply import apply_source_retirement_plan
from .plan import _assert_plan_digest


def prepare_source_retirement_release(
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
    """Build and seal one graph/index generation from a source-retirement plan."""

    _assert_plan_digest(plan)
    active = manager.active_pair()
    if (
        active["generation_id"] != plan["base_generation_id"]
        or active["graph_sha256"] != plan["base_graph_sha256"]
        or active["embedding_backend"] != backend.backend_id
        or active["production_qualified"] != backend.production_qualified
    ):
        raise RuntimeError("source-retirement active pair or backend drifted")
    if intake_summary.get("status") != "COMPLETED" or intake_summary.get("run_id") != run_id:
        raise RuntimeError("source-retirement requires a completed control intake")
    if intake_summary.get("source_id") != source_id or intake_summary.get("trace_id") != trace_id:
        raise RuntimeError("source-retirement intake run is not bound to its control source")

    generation_id = stable_id(
        "generation-source-retirement",
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
        raise FileExistsError("source-retirement generation already exists")
    manager._verify_backend_snapshot(backend)
    assert_safe_mutable_path(manager.runtime_root, generation_dir, label="source-retirement generation")
    assert_safe_mutable_path(manager.runtime_root, receipt_path, label="source-retirement receipt")
    generation_dir.mkdir(parents=True)
    graph_path = generation_dir / "graph.sqlite"
    sidecar_path = generation_dir / "sidecar.sqlite"
    exact_file_copy((manager.runtime_root / active["graph_path"]).resolve(), graph_path)
    graph_receipt = apply_source_retirement_plan(
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
    component = {
        "change_set_digest": plan["plan_digest"],
        "change_set_id": plan["plan_id"],
        "intake_summary_digest": digest(intake_summary),
        "operation_ids": [item["operation_id"] for item in plan["operations"]],
        "run_id": run_id,
        "source_id": source_id,
        "source_version_id": source_version_id,
        "trace_id": trace_id,
    }
    receipt = _seal_receipt(
        {
            "schema_version": "1.0.0",
            "receipt_id": stable_id(
                "release-receipt-source-retirement",
                generation_id,
                plan["plan_digest"],
                length=28,
            ),
            "status": "PREPARED",
            "release_kind": "source_retirement",
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
                "summary_type": "source_retirement_delta",
                "target_source_id": plan["summary"]["target_source_id"],
                "removed": plan["summary"]["removed"],
                "retained": plan["summary"]["retained"],
                "source_update_count": plan["summary"]["source_update_count"],
                "source_version_update_count": plan["summary"]["source_version_update_count"],
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


def record_source_retirement_release_events(
    *, runtime_root: Path, receipt: dict[str, Any]
) -> dict[str, Any]:
    """Record W0/E0 evidence for the exact retirement generation."""

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
