"""Release preparation, evaluation, and activation for semantic-unit index."""
from __future__ import annotations

from pathlib import Path
from typing import Any

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
from ..core import trace_id as make_trace_id
from ..hitl_governance import validate_approval_origin
from ..release_core.helpers import _seal_receipt
from ..release_core.manager import ReleaseManager
from ..telemetry import RunRecorder
from .backends import EmbeddingBackend
from .shadow_eval import build_index_b_sidecar, run_shadow_index_comparison
from .sidecar import validate_sidecar
from .v0_audit import verify_v0_fact_reachability


def prepare_semantic_unit_release(
    *,
    manager: ReleaseManager,
    backend: EmbeddingBackend,
    run_id: str,
    approval_id: str,
    trace_id: str | None = None,
    policy: dict[str, Any] | None = None,
    event_timestamp: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Prepare and seal one unit-only graph/index generation from active pair."""
    is_valid, origin_reason = validate_approval_origin(approval_id)
    if not is_valid:
        raise PermissionError(f"semantic unit release approval origin rejected: {origin_reason}")

    active = manager.active_pair()
    if active.get("embedding_backend") != backend.backend_id:
        raise RuntimeError("active pair backend drifted relative to requested backend")

    now = event_timestamp or utc_now()
    tid = trace_id or make_trace_id("semantic-unit-release", run_id)

    generation_id = stable_id(
        "generation-semantic-unit-index",
        active["generation_id"],
        backend.backend_id,
        backend.model_revision,
        length=28,
    )
    generation_dir = manager.generations_dir / generation_id
    receipt_path = manager.releases_dir / f"release-{generation_id}.json"

    if receipt_path.is_file():
        existing = read_json(receipt_path)
        if existing.get("status") == "PREPARED":
            manager.validate_pair(existing["next_pair"])
            return existing, receipt_path
        raise FileExistsError(f"semantic unit release receipt already exists: {receipt_path}")

    if generation_dir.exists():
        generation_id = stable_id(
            "generation-semantic-unit-retry",
            generation_id,
            now,
            length=28,
        )
        generation_dir = manager.generations_dir / generation_id
        receipt_path = manager.releases_dir / f"release-{generation_id}.json"
        if generation_dir.exists() or receipt_path.exists():
            raise FileExistsError(f"semantic unit retry generation exists: {generation_dir}")

    manager._verify_backend_snapshot(backend)
    assert_safe_mutable_path(manager.runtime_root, generation_dir, label="semantic unit generation")
    assert_safe_mutable_path(manager.runtime_root, receipt_path, label="semantic unit release receipt")
    generation_dir.mkdir(parents=True)

    graph_path = generation_dir / "graph.sqlite"
    sidecar_path = generation_dir / "sidecar.sqlite"
    active_graph_path = (manager.runtime_root / active["graph_path"]).resolve()
    exact_file_copy(active_graph_path, graph_path)

    graph_hash = sha256_file(graph_path)
    if graph_hash != active["graph_sha256"]:
        raise RuntimeError(f"graph hash mismatch after copy: {graph_hash} != {active['graph_sha256']}")
    graph_snapshot = active["graph_snapshot"]

    active_sidecar_path = (
        (manager.runtime_root / active["sidecar_path"]).resolve()
        if active.get("sidecar_path")
        else None
    )
    index_receipt = build_index_b_sidecar(
        graph_path=graph_path,
        base_sidecar_path=active_sidecar_path,
        output_dir=generation_dir,
        generation_id=generation_id,
        backend=backend,
        policy=policy,
    )
    index_receipt["sidecar_path"] = str(index_receipt["sidecar_path"])

    val_res = validate_sidecar(
        sidecar_path,
        expected_generation_id=generation_id,
        expected_graph_snapshot=graph_snapshot,
        expected_graph_sha256=graph_hash,
        enforce_unit_policy=True,
    )
    if val_res.get("status") != "PASS":
        raise RuntimeError(f"semantic unit sidecar validation failed: {val_res}")

    v0_receipt = verify_v0_fact_reachability(
        graph_path=graph_path,
        sidecar_path=sidecar_path,
        generation_id=generation_id,
        directive_id=f"directive-release-{generation_id}",
        runtime_root=manager.runtime_root,
    )
    if v0_receipt.get("unreachable_count", 0) > 0:
        raise RuntimeError(f"V0 fact reachability check failed: {v0_receipt}")

    eval_receipt = run_shadow_index_comparison(
        graph_path=graph_path,
        sidecar_a_path=active_sidecar_path,
        sidecar_b_path=sidecar_path,
        backend=backend,
        runtime_root=manager.runtime_root,
        generation_id=generation_id,
    )
    if not eval_receipt.get("all_11_gates_passed") or not eval_receipt.get("reachability_100pct_met"):
        raise RuntimeError(f"11 BGE-M3 release gates failed: {eval_receipt}")

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
                "release-receipt-semantic-unit-index",
                generation_id,
                length=28,
            ),
            "status": "PREPARED",
            "release_kind": "semantic_unit_index_release",
            "run_id": run_id,
            "trace_id": tid,
            "approval_id": approval_id,
            "component_runs": [
                {
                    "run_id": run_id,
                    "trace_id": tid,
                    "source_id": "semantic_unit_index_release",
                    "operation_ids": [],
                }
            ],
            "previous_pair": active,
            "next_pair": next_pair,
            "graph_write_receipt": {
                "status": "NOOP",
                "operation_count": 0,
                "operations": [],
                "graph_sha256_before": active["graph_sha256"],
                "graph_sha256_after": graph_hash,
            },
            "embedding_generation_receipt": index_receipt,
            "v0_fact_reachability_receipt": v0_receipt,
            "retrieval_evaluation_receipt": eval_receipt,
            "pair_validation": pair_validation,
            "production_qualified": backend.production_qualified,
            "prepared_at": now,
        }
    )
    write_json_exclusive(receipt_path, receipt)
    return receipt, receipt_path


def record_semantic_unit_release_events(
    *,
    runtime_root: Path,
    receipt: dict[str, Any],
    required_stages: tuple[str, ...] = ("W0", "E0"),
) -> dict[str, Any]:
    """Records W0 and E0 telemetry events for a semantic-unit release."""
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
        record_count=0,
    )
    recorder.record(
        stage="E0",
        event_type="embedding_generation_receipt",
        payload=receipt["embedding_generation_receipt"],
        correlation=correlation,
        graph_snapshot=receipt["next_pair"]["graph_snapshot"],
        generation_id=receipt["next_pair"]["generation_id"],
        model_id=receipt["next_pair"]["model_id"],
        record_count=receipt["embedding_generation_receipt"]["total_records"],
    )
    return recorder.audit(required_stages)


def activate_semantic_unit_release(
    *,
    manager: ReleaseManager,
    receipt_path: Path,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Atomically activate a prepared semantic-unit release under active_pair lock."""
    receipt = read_json(receipt_path)
    if receipt.get("status") != "PREPARED":
        raise ValueError(f"Only PREPARED release receipt can be activated: {receipt.get('status')}")
    if receipt.get("release_kind") != "semantic_unit_index_release":
        raise ValueError(f"Unexpected release_kind: {receipt.get('release_kind')}")

    current = manager.active_pair()
    prev = receipt["previous_pair"]
    if current.get("generation_id") != prev.get("generation_id") or current.get("graph_sha256") != prev.get("graph_sha256"):
        raise RuntimeError("Active pair changed since release preparation")

    next_pair = dict(receipt["next_pair"])
    manager.validate_pair(next_pair)

    with file_lock(manager.lock_path):
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
            "release_kind": "semantic_unit_index_release",
            "previous_pair": prev,
            "active_pair": next_pair,
            "activated_at": now,
        }
        activation = _seal_receipt(activation)
        write_json_exclusive(activation_path, activation)

    return activation


def rollback_semantic_unit_release(
    *,
    manager: ReleaseManager,
    receipt_path: Path,
) -> dict[str, Any]:
    """Roll back active pair to previous pair recorded in receipt."""
    receipt = read_json(receipt_path)
    prev = receipt["previous_pair"]
    manager.validate_pair(prev)

    with file_lock(manager.lock_path):
        write_json(manager.active_path, prev)
        rollback_path = manager.releases_dir / f"rollback-{receipt['receipt_id']}.json"
        rollback_data = _seal_receipt({
            "schema_version": "1.0.0",
            "rollback_id": f"rollback-{receipt['receipt_id']}",
            "status": "ROLLED_BACK",
            "release_receipt_id": receipt["receipt_id"],
            "restored_pair": prev,
            "rolled_back_at": utc_now(),
        })
        write_json_exclusive(rollback_path, rollback_data)

    return rollback_data
