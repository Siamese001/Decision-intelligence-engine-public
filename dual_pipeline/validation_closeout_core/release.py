"""Release preparation and immutable receipt sealing for validation closeout."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.core import (
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from dual_pipeline.index import EmbeddingBackend, build_sidecar
from dual_pipeline.release import ReleaseManager, _seal_receipt

from .apply import apply_validation_closeout_plan
from .constants import (
    _DISPOSITION_CONFIRM_MAPPING,
    _DISPOSITION_RETAIN_EVIDENCE_ONLY,
)
from .plan import _assert_plan_digest


def prepare_validation_closeout_release(
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
    """Prepare one graph/index release for a validation closeout plan."""

    _assert_plan_digest(plan)
    active = manager.active_pair()
    if (
        active["generation_id"] != plan["base_generation_id"]
        or active["graph_sha256"] != plan["base_graph_sha256"]
        or active["embedding_backend"] != backend.backend_id
        or active["production_qualified"] != backend.production_qualified
    ):
        raise RuntimeError("validation closeout active pair or backend drifted")
    if intake_summary.get("status") != "COMPLETED" or intake_summary.get("run_id") != run_id:
        raise RuntimeError("validation closeout requires a completed control intake")
    generation_id = stable_id(
        "generation-validation-closeout",
        active["generation_id"],
        plan["plan_digest"],
        backend.backend_id,
        backend.model_revision,
        backend.model_manifest_digest,
        length=28,
    )
    generation_dir = manager.generations_dir / generation_id
    receipt_path = manager.releases_dir / f"release-{generation_id}.json"
    if receipt_path.exists():
        raise FileExistsError("validation closeout generation already exists")
    if generation_dir.exists():
        # A prior preparation can fail after cloning the graph but before a
        # receipt exists. Preserve that immutable failure artifact and create
        # a separately traceable retry generation instead of overwriting it.
        generation_id = stable_id(
            "generation-validation-closeout-retry",
            generation_id,
            utc_now(),
            length=28,
        )
        generation_dir = manager.generations_dir / generation_id
        receipt_path = manager.releases_dir / f"release-{generation_id}.json"
        if generation_dir.exists() or receipt_path.exists():
            raise FileExistsError("validation closeout retry generation already exists")
    manager._verify_backend_snapshot(backend)
    assert_safe_mutable_path(manager.runtime_root, generation_dir, label="closeout generation")
    assert_safe_mutable_path(manager.runtime_root, receipt_path, label="closeout receipt")
    generation_dir.mkdir(parents=True)
    graph_path = generation_dir / "graph.sqlite"
    sidecar_path = generation_dir / "sidecar.sqlite"
    exact_file_copy((manager.runtime_root / active["graph_path"]).resolve(), graph_path)
    graph_receipt = apply_validation_closeout_plan(
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
                "release-receipt-validation-closeout", generation_id, plan["plan_digest"], length=28
            ),
            "status": "PREPARED",
            "release_kind": "validation_closeout",
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
                "summary_type": "validation_closeout_delta",
                "validation_id": plan["validation_id"],
                "disposition": plan["disposition"],
                "validation_status_before": plan["validation_status_before"],
                "validation_status_after": (
                    "closed"
                    if plan["disposition"]
                    in {
                        _DISPOSITION_RETAIN_EVIDENCE_ONLY,
                        _DISPOSITION_CONFIRM_MAPPING,
                    }
                    else "removed"
                ),
                "linked_assertions_reclassified": plan["summary"]["linked_assertions_reclassified"],
                "linked_assertions_removed": plan["summary"]["linked_assertions_removed"],
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
