"""Execution, release preparation, and telemetry recording for candidate assertion cleanup."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

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
from ..release import ReleaseManager, _seal_receipt
from ..storage.governed_context import GovernedStorageContext
from ..telemetry import RunRecorder
from .constants import CLEANUP_POLICY_VERSION, PRESERVED_FUTURE_STATE_SOURCE_IDS
from .helpers import (
    _assert_plan_digest,
    _assertion_state,
    _candidate_retrieval_count,
    _clean_qualifiers,
    _correlation,
    _row_hash,
)


def _apply_register_controlled_value(
    connection: Any, operation: dict[str, Any]
) -> tuple[str, str, None, Any, dict[str, Any], Any]:
    value = operation["value"]
    row = connection.execute(
        "SELECT vocabulary,value,definition,active,sort_order FROM controlled_value_registry WHERE vocabulary='canonical_status' AND value=?",
        (value,),
    ).fetchone()
    if _row_hash(row) != operation["before_state_hash"]:
        raise RuntimeError(f"cleanup controlled value changed: canonical_status:{value}")
    if row is None:
        connection.execute(
            "INSERT INTO controlled_value_registry (vocabulary,value,definition,active,sort_order) VALUES ('canonical_status',?,?,1,?)",
            (value, operation["definition"], operation["sort_order"]),
        )
    else:
        connection.execute(
            "UPDATE controlled_value_registry SET definition=?,active=1,sort_order=? WHERE vocabulary='canonical_status' AND value=?",
            (operation["definition"], operation["sort_order"], value),
        )
    after = connection.execute(
        "SELECT vocabulary,value,definition,active,sort_order FROM controlled_value_registry WHERE vocabulary='canonical_status' AND value=?",
        (value,),
    ).fetchone()
    return "controlled_value_registry", operation["target_id"], None, dict(row) if row is not None else None, dict(after), after


def _apply_reclassify_candidate_assertion(
    connection: Any, operation: dict[str, Any], now: str
) -> tuple[str, str, str, dict[str, Any], dict[str, Any], Any]:
    assertion_id = operation["target_id"]
    row = connection.execute(
        "SELECT assertion_id,canonical_status,currentness_state,canonical_eligible,retrieval_eligible,validation_priority,validation_state,primary_source_id,qualifiers_json FROM assertion WHERE assertion_id=?",
        (assertion_id,),
    ).fetchone()
    if row is None or digest(_assertion_state(row)) != operation["before_state_hash"]:
        raise RuntimeError(f"cleanup assertion changed: {assertion_id}")
    if (
        row["canonical_status"] != "candidate"
        or row["currentness_state"] not in {"current", "unknown"}
        or row["primary_source_id"] in PRESERVED_FUTURE_STATE_SOURCE_IDS
    ):
        raise RuntimeError(f"cleanup assertion no longer matches policy: {assertion_id}")
    cleaned = _clean_qualifiers(
        row["qualifiers_json"],
        disposition=operation["target_canonical_status"],
        reason=operation["reason"],
        cleaned_at=now,
    )
    connection.execute(
        "UPDATE assertion SET canonical_status=?,validation_state=?,canonical_eligible=0,retrieval_eligible=0,qualifiers_json=?,updated_at=? WHERE assertion_id=?",
        (operation["target_canonical_status"], operation["target_validation_state"], cleaned, now, assertion_id),
    )
    has_sd = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_document'"
    ).fetchone()
    if has_sd:
        connection.execute(
            "UPDATE search_document SET canonical_status=?,retrieval_eligible=0,updated_at=? WHERE assertion_id=?",
            (operation["target_canonical_status"], now, assertion_id),
        )
    after = connection.execute(
        "SELECT assertion_id,canonical_status,currentness_state,canonical_eligible,retrieval_eligible,validation_priority,validation_state,primary_source_id,qualifiers_json FROM assertion WHERE assertion_id=?",
        (assertion_id,),
    ).fetchone()
    return "assertion", assertion_id, row["primary_source_id"], _assertion_state(row), _assertion_state(after), after


def apply_candidate_cleanup_plan(
    graph_path: Path,
    *,
    plan: dict[str, Any],
    approval_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Apply a baseline-bound cleanup plan in one audited SQLite transaction."""
    _assert_plan_digest(plan)
    graph_path = Path(graph_path).resolve()
    before_hash = sha256_file(graph_path)
    if before_hash != plan["base_graph_sha256"]:
        raise RuntimeError("candidate cleanup plan is stale relative to its graph")
    connection = GovernedStorageContext.connect(graph_path)
    connection.row_factory = sqlite3.Row
    now = utc_now()
    receipts: list[dict[str, Any]] = []
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for operation in plan["operations"]:
            action = operation["operation"]
            if action == "register_controlled_value":
                rec_type, rec_id, src_id, prev_val, new_val, after = _apply_register_controlled_value(connection, operation)
            elif action == "reclassify_candidate_assertion":
                rec_type, rec_id, src_id, prev_val, new_val, after = _apply_reclassify_candidate_assertion(connection, operation, now)
            else:
                raise ValueError(f"unsupported candidate cleanup operation: {action}")

            audit_id = stable_id("candidate-backlog-cleanup-audit", plan["plan_id"], operation["operation_id"], length=28)
            connection.execute(
                """INSERT INTO audit_event
                   (audit_event_id,actor_or_process,action,record_type,record_id,
                    previous_value_json,new_value_json,source_id,event_timestamp,reason,
                    validation_reference,approval_reference,migration_or_ingestion_run_id,
                    release_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    audit_id, "nee-candidate-backlog-cleanup", action, rec_type, rec_id,
                    json.dumps(prev_val, sort_keys=True) if prev_val is not None else None,
                    json.dumps(new_val, sort_keys=True), src_id, now,
                    operation.get("reason", "Register controlled cleanup disposition."),
                    plan["plan_id"], approval_id, run_id, CLEANUP_POLICY_VERSION,
                ),
            )
            receipts.append({
                "operation_id": operation["operation_id"],
                "change_set_id": plan["plan_id"],
                "run_id": run_id,
                "source_id": src_id,
                "target_id": rec_id,
                "before_state_hash": operation["before_state_hash"],
                "after_row_hash": _row_hash(after),
            })
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign-key validation failed: {len(violations)} violations")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    integrity = sqlite_integrity(graph_path)
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"]:
        raise RuntimeError("candidate cleanup graph integrity validation failed")
    after_hash = sha256_file(graph_path)
    if receipts and after_hash == before_hash:
        raise RuntimeError("candidate cleanup operations did not change the graph hash")
    return {
        "status": "COMMITTED",
        "change_set_id": plan["plan_id"],
        "approval_id": approval_id,
        "transaction_required": True,
        "operation_count": len(receipts),
        "operations": receipts,
        "graph_sha256_before": before_hash,
        "graph_sha256_after": after_hash,
        "integrity": integrity,
        "committed_at": now,
    }


def prepare_candidate_cleanup_release(
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
    """Build one graph/index generation from a cleanup plan and intake run."""
    _assert_plan_digest(plan)
    active = manager.active_pair()
    if (
        active["generation_id"] != plan["base_generation_id"]
        or active["graph_sha256"] != plan["base_graph_sha256"]
    ):
        raise RuntimeError("active pair drifted after the candidate cleanup plan was built")
    if active["embedding_backend"] != backend.backend_id:
        raise RuntimeError("candidate cleanup backend must match the active pair")
    if active["production_qualified"] != backend.production_qualified:
        raise RuntimeError("candidate cleanup backend qualification must match the active pair")
    if intake_summary.get("status") != "COMPLETED" or intake_summary.get("run_id") != run_id:
        raise RuntimeError("candidate cleanup requires a completed control intake run")
    if intake_summary.get("source_id") != source_id or intake_summary.get("trace_id") != trace_id:
        raise RuntimeError("candidate cleanup run is not bound to its control source")

    generation_id = stable_id(
        "generation-candidate-backlog-cleanup",
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
        raise FileExistsError("candidate cleanup generation already exists")
    manager._verify_backend_snapshot(backend)
    assert_safe_mutable_path(manager.runtime_root, generation_dir, label="cleanup generation")
    assert_safe_mutable_path(manager.runtime_root, receipt_path, label="cleanup receipt")
    generation_dir.mkdir(parents=True)
    graph_path = generation_dir / "graph.sqlite"
    sidecar_path = generation_dir / "sidecar.sqlite"
    active_graph_path = (manager.runtime_root / active["graph_path"]).resolve()
    exact_file_copy(active_graph_path, graph_path)
    graph_receipt = apply_candidate_cleanup_plan(
        graph_path,
        plan=plan,
        approval_id=approval_id,
        run_id=run_id,
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
    cleanup_delta = {
        "schema_version": "1.0.0",
        "summary_type": "candidate_backlog_cleanup_delta",
        "plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"],
        "candidate_retrieval_assertions_before": plan["summary"][
            "candidate_retrieval_assertions_before"
        ],
        "candidate_retrieval_assertions_after": _candidate_retrieval_count(graph_path),
        "retained_future_state_candidates": plan["summary"]["future_state_candidates_retained"],
        "disposition_counts": {
            "validation_pending": plan["summary"]["validation_pending"],
            "evidence_only": plan["summary"]["evidence_only"],
        },
    }
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
                "release-receipt-candidate-backlog-cleanup",
                generation_id,
                plan["plan_digest"],
                length=28,
            ),
            "status": "PREPARED",
            "release_kind": "candidate_backlog_cleanup",
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
            "graph_delta_summary": cleanup_delta,
            "embedding_generation_receipt": index_receipt,
            "pair_validation": pair_validation,
            "production_qualified": backend.production_qualified,
            "authorization": None,
            "prepared_at": utc_now(),
        }
    )
    write_json_exclusive(receipt_path, receipt)
    return receipt, receipt_path


def record_candidate_cleanup_release_events(
    *,
    runtime_root: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Record W0/E0 preparation evidence; activation records the final E0 receipt."""
    component = receipt["component_runs"][0]
    recorder = RunRecorder(
        runtime_root,
        run_id=component["run_id"],
        trace_id=component["trace_id"],
        use_case="intake",
        graph_snapshot=receipt["next_pair"]["graph_snapshot"],
        model_or_index_generation=receipt["next_pair"]["generation_id"],
    )
    correlation = _correlation(component["source_id"])
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
