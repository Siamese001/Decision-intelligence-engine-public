"""
dual_pipeline/graph_consolidation_core/release.py

Release preparation and immutable receipt sealing for governed graph consolidation.
Fully integrated with ReleaseManager, UWG isolated generation staging,
validate_approval_origin gating, full operation enumeration for E3 evaluation deltas,
and zero-op idempotent no-op receipt handling.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..core import (
    assert_safe_mutable_path,
    exact_file_copy,
    file_lock,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from ..core import (
    trace_id as make_trace_id,
)
from ..hitl_governance import validate_approval_origin
from ..index import EmbeddingBackend, build_sidecar
from ..release_core.helpers import _seal_receipt
from ..release_core.manager import ReleaseManager
from ..telemetry import RunRecorder
from .apply import apply_consolidation_plan
from .plan import _assert_plan_digest
from .redirect_activation import verify_redirect_activation
from .redirect_guard import verify_redirect_context
from .redirect_repair import is_redirect_plan


def _prepare_noop_release(
    *,
    manager: ReleaseManager,
    backend: EmbeddingBackend,
    plan: Dict[str, Any],
    active: Dict[str, Any],
    component: Dict[str, Any],
    run_id: str,
    trace_id: str,
    approval_id: str,
    now: str,
) -> Tuple[Dict[str, Any], Path]:
    receipt_id = stable_id(
        "release-receipt-consolidation-noop",
        active["generation_id"],
        plan["plan_digest"],
        length=28,
    )
    receipt_path = manager.releases_dir / f"{receipt_id}.json"
    assert_safe_mutable_path(manager.runtime_root, receipt_path, label="consolidation noop receipt")
    receipt = _seal_receipt(
        {
            "schema_version": "1.0.0",
            "receipt_id": receipt_id,
            "status": "IDEMPOTENT_NOOP",
            "release_kind": "graph_consolidation",
            "run_id": run_id,
            "trace_id": trace_id,
            "approval_id": approval_id,
            "change_set_id": plan["plan_id"],
            "change_set_digest": plan["plan_digest"],
            "component_runs": [component],
            "previous_pair": active,
            "next_pair": active,
            "graph_write_receipt": {
                "status": "NOOP",
                "operation_count": 0,
                "operations": [],
                "nodes_merged": 0,
                "assertions_superseded": 0,
                "graph_sha256_before": active["graph_sha256"],
                "graph_sha256_after": active["graph_sha256"],
            },
            "graph_delta_summary": {
                "schema_version": "1.0.0",
                "summary_type": "graph_consolidation_delta",
                "nodes_merged": 0,
                "assertions_superseded": 0,
                "total_operations": 0,
            },
            "embedding_generation_receipt": {
                "sidecar_path": active.get("sidecar_path"),
                "sidecar_sha256": active.get("sidecar_sha256"),
            },
            "pair_validation": {"status": "PASS"},
            "production_qualified": backend.production_qualified,
            "authorization": None,
            "prepared_at": now,
        }
    )
    write_json_exclusive(receipt_path, receipt)
    return receipt, receipt_path


def _resolve_generation_target(
    manager: ReleaseManager,
    active: Dict[str, Any],
    plan: Dict[str, Any],
    backend: EmbeddingBackend,
    now: str,
) -> Tuple[str, Path, Path]:
    generation_id = stable_id(
        "generation-graph-consolidation",
        active["generation_id"],
        plan["plan_digest"],
        backend.backend_id,
        backend.model_revision,
        backend.model_manifest_digest,
        length=28,
    )
    generation_dir = manager.generations_dir / generation_id
    receipt_path = manager.releases_dir / f"release-{generation_id}.json"

    if receipt_path.exists() and not generation_dir.exists():
        raise FileExistsError(f"consolidation release receipt has no generation: {receipt_path}")
    if generation_dir.exists():
        generation_id = stable_id(
            "generation-graph-consolidation-retry",
            generation_id,
            now,
            length=28,
        )
        generation_dir = manager.generations_dir / generation_id
        receipt_path = manager.releases_dir / f"release-{generation_id}.json"
        if generation_dir.exists() or receipt_path.exists():
            raise FileExistsError(f"consolidation retry generation already exists: {generation_dir}")
    return generation_id, generation_dir, receipt_path


def prepare_consolidation_release(
    *,
    manager: ReleaseManager,
    backend: EmbeddingBackend,
    plan: Dict[str, Any],
    run_id: str,
    trace_id: Optional[str] = None,
    approval_id: str,
    event_timestamp: Optional[str] = None,
) -> Tuple[Dict[str, Any], Path]:
    """Prepare an atomic graph/index release for an approved consolidation plan."""
    trace_id = trace_id or make_trace_id("consolidation", run_id)
    _assert_plan_digest(plan)

    # Validate approval origin to prevent synthetic IDE auto-approval bypass
    is_valid, origin_reason = validate_approval_origin(approval_id)
    if not is_valid:
        raise PermissionError(f"consolidation release approval origin rejected: {origin_reason}")

    if is_redirect_plan(plan):
        verify_redirect_context(manager.runtime_root, plan, approval_id)
    active = manager.active_pair()
    if (
        active["generation_id"] != plan["base_generation_id"]
        or active["graph_sha256"] != plan["base_graph_sha256"]
        or active["embedding_backend"] != backend.backend_id
        or active["production_qualified"] != backend.production_qualified
    ):
        raise RuntimeError("consolidation active pair or backend drifted relative to plan")

    now = event_timestamp or utc_now()
    operations = plan.get("operations") or plan.get("proposed_operations") or []
    operation_ids = [op["operation_id"] for op in operations]

    component = {
        "change_set_digest": plan["plan_digest"],
        "change_set_id": plan["plan_id"],
        "operation_ids": operation_ids,
        "run_id": run_id,
        "source_id": "graph_consolidation",
        "trace_id": trace_id,
    }

    # Zero-op safety (Defect D3): return immutable idempotent no-op receipt without mutating graph
    if not operations:
        return _prepare_noop_release(
            manager=manager,
            backend=backend,
            plan=plan,
            active=active,
            component=component,
            run_id=run_id,
            trace_id=trace_id,
            approval_id=approval_id,
            now=now,
        )

    # Mutating release preparation in isolated generation directory
    generation_id, generation_dir, receipt_path = _resolve_generation_target(
        manager, active, plan, backend, now
    )

    manager._verify_backend_snapshot(backend)
    assert_safe_mutable_path(manager.runtime_root, generation_dir, label="consolidation generation")
    assert_safe_mutable_path(manager.runtime_root, receipt_path, label="consolidation receipt")

    generation_dir.mkdir(parents=True)
    graph_path = generation_dir / "graph.sqlite"
    sidecar_path = generation_dir / "sidecar.sqlite"

    exact_file_copy((manager.runtime_root / active["graph_path"]).resolve(), graph_path)

    graph_receipt = apply_consolidation_plan(
        graph_path,
        plan=plan,
        approval_id=approval_id,
        run_id=run_id,
        event_timestamp=now,
        runtime_root=manager.runtime_root,
    )
    graph_hash = sha256_file(graph_path)
    graph_snapshot = stable_id("graph-snapshot", graph_hash, length=24)

    index_receipt = build_sidecar(
        graph_path=graph_path,
        sidecar_path=sidecar_path,
        generation_id=generation_id,
        graph_snapshot=graph_snapshot,
        backend=backend,
        prior_sidecar_path=(
            (manager.runtime_root / active["sidecar_path"]).resolve()
            if is_redirect_plan(plan) else None
        ),
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

    receipt = _seal_receipt(
        {
            "schema_version": "1.0.0",
            "receipt_id": stable_id(
                "release-receipt-graph-consolidation",
                generation_id,
                plan["plan_digest"],
                length=28,
            ),
            "status": "PREPARED",
            "release_kind": "graph_consolidation",
            "run_id": run_id,
            "trace_id": trace_id,
            "approval_id": approval_id,
            "change_set_id": plan["plan_id"],
            "change_set_digest": plan["plan_digest"],
            "component_runs": [component],
            "previous_pair": active,
            "next_pair": next_pair,
            "graph_write_receipt": graph_receipt,
            **({"redirect_repair_plan": plan} if is_redirect_plan(plan) else {}),
            "graph_delta_summary": {
                "schema_version": "1.0.0",
                "summary_type": "graph_consolidation_delta",
                "nodes_merged": graph_receipt["nodes_merged"],
                "assertions_superseded": graph_receipt["assertions_superseded"],
                "total_operations": graph_receipt["operation_count"],
                "redirects_flattened": graph_receipt.get("redirects_flattened", 0),
            },
            "embedding_generation_receipt": index_receipt,
            "pair_validation": pair_validation,
            "production_qualified": backend.production_qualified,
            "authorization": None,
            "prepared_at": now,
        }
    )
    write_json_exclusive(receipt_path, receipt)
    return receipt, receipt_path


def record_consolidation_release_events(
    *,
    runtime_root: Path,
    receipt: Dict[str, Any],
    required_stages: Tuple[str, ...] = ("W0", "E0"),
) -> Dict[str, Any]:
    """Records W0 and E0 stage telemetry for a mutating release; records N/A for idempotent noop."""
    if receipt.get("status") == "IDEMPOTENT_NOOP":
        return {
            "status": "NOT_APPLICABLE",
            "reason": "zero-op idempotent no-op run",
            "events_recorded": 0,
        }

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
    return recorder.audit(required_stages)


def activate_consolidation_release(
    *,
    manager: ReleaseManager,
    receipt_path: Path,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Activate a prepared graph consolidation release atomically under active_pair lock."""
    receipt = read_json(receipt_path)
    if receipt.get("status") not in {"PREPARED", "IDEMPOTENT_NOOP"}:
        raise ValueError(
            f"Only a PREPARED release receipt can be activated: status={receipt.get('status')}"
        )
    if receipt.get("release_kind") != "graph_consolidation":
        raise ValueError(f"Unexpected release_kind: {receipt.get('release_kind')}")

    if receipt.get("status") == "IDEMPOTENT_NOOP":
        return {
            "schema_version": "1.0.0",
            "activation_id": stable_id("activation-noop", receipt["receipt_id"], length=28),
            "status": "IDEMPOTENT_NOOP",
            "release_receipt_id": receipt["receipt_id"],
            "run_id": receipt["run_id"],
            "approval_id": approval_id or receipt.get("approval_id"),
            "release_kind": "graph_consolidation",
            "previous_pair": receipt["previous_pair"],
            "active_pair": receipt["next_pair"],
            "activated_at": utc_now(),
        }

    current = manager.active_pair()
    prev = receipt["previous_pair"]
    if current.get("generation_id") != prev.get("generation_id") or current.get("graph_sha256") != prev.get("graph_sha256"):
        raise RuntimeError("Active pair changed since release preparation")

    next_pair = dict(receipt["next_pair"])
    manager.validate_pair(next_pair)

    with file_lock(manager.lock_path):
        verify_redirect_activation(manager, receipt, approval_id or receipt.get("approval_id"))
        now = utc_now()
        next_pair["activated_at"] = now
        write_json(manager.active_path, next_pair)

        activation_path = manager.releases_dir / f"activation-{next_pair['generation_id']}.json"
        activation = {
            "schema_version": "1.0.0",
            "activation_id": f"activation-{next_pair['generation_id']}",
            "status": "ACTIVE",
            "release_receipt_id": receipt["receipt_id"],
            "run_id": receipt["run_id"],
            "approval_id": approval_id or receipt.get("approval_id"),
            "release_kind": "graph_consolidation",
            "previous_pair": prev,
            "active_pair": next_pair,
            "activated_at": now,
        }
        activation = _seal_receipt(activation)
        write_json_exclusive(activation_path, activation)

    return activation

