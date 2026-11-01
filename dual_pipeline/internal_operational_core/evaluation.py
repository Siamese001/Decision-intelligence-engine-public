"""Evaluation, binding, and recovery evidence helpers for internal operational qualification."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import (
    assert_safe_mutable_path,
    exact_file_copy,
    read_json,
    sha256_file,
    sqlite_integrity,
)
from dual_pipeline.index import HybridRetriever
from dual_pipeline.release import ReleaseManager
from dual_pipeline.retrieval_release import _evaluate_case


def _verify_directive_package_bytes(directive: dict[str, Any]) -> None:
    for package in directive["packages"]:
        path = Path(package["path"]).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"directive package no longer exists: {path}")
        if sha256_file(path) != package["sha256"] or path.stat().st_size != package["bytes"]:
            raise RuntimeError(f"directive package bytes changed after approval: {path}")


def _directive_binding(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from dual_pipeline.completion_core.verification import _verify_directive  # noqa: lazy-decouple

    resolved = Path(path).expanduser().resolve()
    directive = read_json(resolved)
    _verify_directive(directive)
    _verify_directive_package_bytes(directive)
    if directive["status"] != "COMPLETE" or not directive["terminal"]:
        raise PermissionError("internal operational qualification requires a COMPLETE directive")
    approval = directive.get("approval")
    completion = directive.get("completion")
    if (
        not isinstance(approval, dict)
        or approval.get("decision") != "APPROVE"
        or not isinstance(completion, dict)
        or completion.get("status") != "PASS"
    ):
        raise PermissionError("internal operational qualification requires approved terminal completion")
    source_runtime = resolved.parent.parent
    run_bindings: list[dict[str, Any]] = []
    for run_id in completion["run_ids"]:
        summary_path = source_runtime / "runs" / run_id / "intake_summary.json"
        summary = read_json(summary_path)
        provider_calls = int(summary.get("provider_call_count", -1))
        if summary.get("status") != "COMPLETED" or provider_calls != 0:
            raise RuntimeError(
                f"internal operational qualification requires a provider-free completed intake run: {run_id}"
            )
        run_bindings.append({
            "run_id": run_id,
            "status": summary["status"],
            "package_file_sha256": summary["package_file_sha256"],
            "provider_call_count": provider_calls,
            "graph_operation_count": int(summary["graph_operation_count"]),
            "embedding_backend": summary["embedding_backend"],
            "model_id": summary["model_id"],
            "model_revision": summary["model_revision"],
            "model_manifest_digest": summary.get("model_manifest_digest"),
        })
    binding = {
        "directive_id": directive["directive_id"],
        "directive_path": str(resolved),
        "directive_digest": directive["directive_digest"],
        "package_set_digest": directive["package_set_digest"],
        "approval_id": approval["approval_id"],
        "completion_digest": completion["completion_digest"],
        "release_receipt_id": completion["receipt_id"],
        "release_receipt_digest": completion["receipt_digest"],
        "source_runtime_root": str(source_runtime),
        "run_bindings": run_bindings,
    }
    return directive, binding


def _source_evaluation_binding(
    path: Path,
    *,
    target_pair: dict[str, Any],
) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    evaluation = read_json(resolved)
    validate_artifact(evaluation)
    if (
        evaluation.get("contract_id") != "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"
        or evaluation.get("execution_tier") != "E3"
        or evaluation.get("status") != "PASS"
        or not all(evaluation.get("gates", {}).values())
    ):
        raise PermissionError("source retrieval evaluation is not a sealed passing E3 gate")
    source_pair = evaluation["candidate_pair"]
    for field in ("graph_sha256", "model_id", "model_revision", "model_manifest_digest"):
        if source_pair.get(field) != target_pair.get(field):
            raise RuntimeError(f"source retrieval evaluation does not match target {field}")
    return {
        "path": str(resolved),
        "file_sha256": sha256_file(resolved),
        "evaluation_id": evaluation["evaluation_id"],
        "evaluation_digest": evaluation["canonical_digest"]["value"],
        "execution_tier": evaluation["execution_tier"],
        "status": evaluation["status"],
        "source_pair": {
            field: source_pair[field]
            for field in (
                "generation_id",
                "graph_snapshot",
                "graph_sha256",
                "sidecar_sha256",
                "embedding_backend",
                "model_id",
                "model_revision",
                "model_manifest_digest",
                "production_qualified",
            )
        },
    }


def _evaluate_target_suite(
    suite_path: Path,
    *,
    retriever: HybridRetriever,
    graph_path: Path,
    policy: dict[str, Any],
) -> dict[str, Any]:
    resolved = Path(suite_path).expanduser().resolve()
    suite = read_json(resolved)
    validate_artifact(suite)
    if suite.get("contract_id") != "urn:nee:dual-pipeline:retrieval-release-suite:1.0.0":
        raise ValueError("internal operational qualification suite has the wrong contract")
    allowed_audiences = set(policy["allowed_audiences"])
    allowed_classifications = set(policy["allowed_classifications"])
    case_results: list[dict[str, Any]] = []
    latency_limit = float(policy["qualification_thresholds"]["max_case_latency_ms"])
    for case in suite["cases"]:
        if not set(case["audiences"]).issubset(allowed_audiences):
            raise PermissionError(f"qualification case exceeds internal audience policy: {case['case_id']}")
        if case["classification"] not in allowed_classifications:
            raise PermissionError(f"qualification case exceeds internal classification policy: {case['case_id']}")
        record_ids, _diagnostics, components, latency_ms = _evaluate_case(
            retriever, case, graph_path
        )
        expected = set(case["expected_record_ids"])
        forbidden = set(case["forbidden_record_ids"])
        missing = sorted(expected - set(record_ids))
        leakage = sorted(forbidden & set(record_ids))
        passed = (
            not missing
            and not leakage
            and all(components.values())
            and latency_ms <= latency_limit
        )
        case_results.append({
            "case_id": case["case_id"],
            "critical": bool(case["critical"]),
            "retrieved_record_ids": record_ids,
            "missing_expected_record_ids": missing,
            "forbidden_leakage_record_ids": leakage,
            "component_execution": components,
            "latency_ms": latency_ms,
            "status": "PASS" if passed else "FAIL",
        })
    failed = [item["case_id"] for item in case_results if item["status"] != "PASS"]
    critical_failed = [item["case_id"] for item in case_results if item["critical"] and item["status"] != "PASS"]
    return {
        "suite_path": str(resolved),
        "suite_file_sha256": sha256_file(resolved),
        "suite_id": suite["suite_id"],
        "suite_digest": suite["canonical_digest"]["value"],
        "case_count": len(case_results),
        "pass_count": len(case_results) - len(failed),
        "failed_case_ids": failed,
        "critical_failed_case_ids": critical_failed,
        "dense_executed": all(item["component_execution"]["dense"] for item in case_results),
        "sparse_executed": all(item["component_execution"]["sparse"] for item in case_results),
        "bm25_executed": all(item["component_execution"]["bm25"] for item in case_results),
        "colbert_executed": all(item["component_execution"]["colbert"] for item in case_results),
        "forbidden_leakage_count": sum(len(item["forbidden_leakage_record_ids"]) for item in case_results),
        "max_latency_ms": max((item["latency_ms"] for item in case_results), default=0.0),
        "case_results": case_results,
        "status": "PASS" if not failed else "FAIL",
    }


def _recovery_evidence(
    runtime_root: Path,
    pair: dict[str, Any],
    manager: ReleaseManager,
) -> dict[str, Any]:
    generation_id = pair["generation_id"]
    recovery_root = assert_safe_mutable_path(
        manager.runtime_root,
        manager.runtime_root / "internal_operational" / "recovery" / generation_id,
        label="internal operational recovery path",
    )
    recovery_root.mkdir(parents=True, exist_ok=True)
    graph = manager._resolve(pair["graph_path"])
    sidecar = manager._resolve(pair["sidecar_path"])
    graph_backup = recovery_root / "graph.sqlite"
    sidecar_backup = recovery_root / "sidecar.sqlite"
    for source, backup in ((graph, graph_backup), (sidecar, sidecar_backup)):
        if backup.exists():
            if sha256_file(backup) != sha256_file(source):
                raise RuntimeError(f"existing recovery copy differs from active pair: {backup}")
        else:
            exact_file_copy(source, backup)
    with tempfile.TemporaryDirectory(prefix="nee-internal-operational-restore-") as directory:
        restored_root = Path(directory)
        restored_graph = restored_root / "graph.sqlite"
        restored_sidecar = restored_root / "sidecar.sqlite"
        exact_file_copy(graph_backup, restored_graph)
        exact_file_copy(sidecar_backup, restored_sidecar)
        graph_restore_integrity = sqlite_integrity(restored_graph)
        sidecar_restore_integrity = sqlite_integrity(restored_sidecar)
        restored_graph_sha = sha256_file(restored_graph)
        restored_sidecar_sha = sha256_file(restored_sidecar)
    if (
        restored_graph_sha != pair["graph_sha256"]
        or restored_sidecar_sha != pair["sidecar_sha256"]
        or graph_restore_integrity["integrity_check"] != "ok"
        or graph_restore_integrity["foreign_key_violation_count"]
        or sidecar_restore_integrity["integrity_check"] != "ok"
        or sidecar_restore_integrity["foreign_key_violation_count"]
    ):
        raise RuntimeError("internal operational backup/restore verification failed")
    return {
        "backup_status": "PASS",
        "restore_status": "PASS",
        "backup_root": str(recovery_root),
        "graph_backup_path": str(graph_backup),
        "graph_backup_sha256": sha256_file(graph_backup),
        "sidecar_backup_path": str(sidecar_backup),
        "sidecar_backup_sha256": sha256_file(sidecar_backup),
        "restored_graph_sha256": restored_graph_sha,
        "restored_sidecar_sha256": restored_sidecar_sha,
        "graph_integrity": graph_restore_integrity,
        "sidecar_integrity": sidecar_restore_integrity,
    }
