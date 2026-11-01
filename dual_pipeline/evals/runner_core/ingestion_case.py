"""Execution and validation of ingestion evaluation cases."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...completion import IngestionCompletionGuard
from ...core import read_json, sha256_file, stable_id
from ...drift_guard import (
    DriftError,
    assert_runtime_binding_unchanged,
    capture_runtime_binding,
)
from ...index import EmbeddingBackend, HybridRetriever
from ...intake import IntakeEngine
from ...retrieval_release import RetrievalReleaseEvaluator
from ...retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
from ..contracts import resolve_repository_file
from .constants import (
    EvaluationFailure,
    _audiences,
    _cache_binding_matches_backend,
    _case_artifact_path,
    _evaluation_policy_digests,
    _pair_binding,
)
from .workspace import _audit, _bootstrap


def _ingestion_case(
    case: dict[str, Any],
    *,
    case_dir: Path,
    case_runtime: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
    template_runtime: Path | None = None,
) -> dict[str, Any]:
    manager, backend, active_graph, sidecar = _bootstrap(
        case_runtime,
        seed_graph,
        backend,
        template_runtime=template_runtime,
    )
    initial_active_pair = manager.active_pair()
    initial_policy_digests = _evaluation_policy_digests()
    initial_runtime_binding = capture_runtime_binding(
        manager=manager,
        active_pair=initial_active_pair,
        backend=backend,
        policy_digests=initial_policy_digests,
    )
    package = resolve_repository_file(case["package_path"])
    directive_id = stable_id("ed", case["case_id"], length=12)
    guard = IngestionCompletionGuard(case_runtime, seed_graph)
    guard.open(
        directive_id=directive_id,
        scope=f"Deterministic evaluation case {case['case_id']}",
        packages=[package],
    )
    intake = IntakeEngine(
        active_graph,
        case_runtime,
        retriever=HybridRetriever(sidecar, backend),
        retrieval_audiences=_audiences(case["audience"]),
        active_pair=manager.active_pair(),
    ).ingest(package)
    candidate_bundle = read_json(
        case_runtime / "runs" / intake["run_id"] / "candidate_bundle.json"
    )
    candidate_mention_count = sum(
        len(candidate.get("entity_mentions", []))
        for candidate in candidate_bundle["candidates"]
        if isinstance(candidate, dict)
    )
    classification = guard.classify(directive_id)["classification"]
    guard.record_human_decision(
        directive_id,
        decision="APPROVE",
        classification_digest=classification["classification_digest"],
        reviewer="deterministic-eval",
        rationale="Approved only for this exact isolated evaluation package.",
    )
    retrieval_evaluation: dict[str, Any] | None = None
    if intake["graph_operation_count"]:
        release = manager.prepare(
            run_id=intake["run_id"],
            approval_id=guard.status(directive_id)["approval"]["approval_id"],
            backend=backend,
        )
        receipt_path = (
            case_runtime
            / "releases"
            / f"release-{release['next_pair']['generation_id']}.json"
        )
        # eval-run is an operator-grade BGE-M3 harness.  Its graph-changing
        # ingestion cases must therefore follow the same receipt-bound
        # retrieval-release evaluation lifecycle as normal batch releases;
        # activation may never bypass that gate.
        suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
            case_runtime,
            seed_graph,
        ).resolve(receipt_path)
        evaluation_result = RetrievalReleaseEvaluator(
            case_runtime,
            seed_graph,
        ).evaluate(
            receipt_path,
            Path(suite_resolution["path"]),
            backend=backend,
        )
        retrieval_evaluation = {
            "suite_resolution": suite_resolution,
            "evaluation_id": evaluation_result["evaluation"]["evaluation_id"],
            "evaluation_digest": evaluation_result["evaluation"]["canonical_digest"][
                "value"
            ],
            "prepared_receipt_id": evaluation_result["evaluation"][
                "prepared_receipt_id"
            ],
            "prepared_receipt_digest": evaluation_result["evaluation"][
                "prepared_receipt_digest"
            ],
            "status": evaluation_result["evaluation"]["status"],
        }
        if retrieval_evaluation["status"] != "PASS":
            raise EvaluationFailure(
                "ingestion evaluation retrieval-release gate did not pass"
            )
        guard.preflight_activation(directive_id, receipt_path)
        manager.activate(receipt_path)
        completion = guard.complete(directive_id, receipt_path)["completion"]
    else:
        completion = guard.complete_noop(directive_id, run_ids=[intake["run_id"]])["completion"]
    terminal_policy_digests = _evaluation_policy_digests()
    terminal_active_pair = manager.active_pair()
    terminal_runtime_binding = capture_runtime_binding(
        manager=manager,
        active_pair=terminal_active_pair,
        backend=backend,
        policy_digests=terminal_policy_digests,
    )
    if initial_runtime_binding["backend"] != terminal_runtime_binding["backend"]:
        raise DriftError(
            "BACKEND_BINDING_DRIFT",
            "ingestion backend changed during evaluation",
            expected=initial_runtime_binding["backend"],
            observed=terminal_runtime_binding["backend"],
        )
    if initial_runtime_binding["policy_digests"] != terminal_runtime_binding["policy_digests"]:
        raise DriftError(
            "POLICY_DRIFT",
            "governing policy changed during ingestion evaluation",
            expected=initial_runtime_binding["policy_digests"],
            observed=terminal_runtime_binding["policy_digests"],
        )
    if not intake["graph_operation_count"]:
        assert_runtime_binding_unchanged(
            manager=manager,
            expected_binding=initial_runtime_binding,
            backend=backend,
            policy_digests=terminal_policy_digests,
            stage="ingestion-noop-terminal",
        )
    expected = case["expected"]
    audit = _audit(
        case_runtime,
        run_id=intake["run_id"],
        trace_id=intake["trace_id"],
        use_case="intake",
        graph_snapshot=None,
        generation_id=intake.get("model_or_index_generation"),
        required_stages=expected["required_stages"],
    )
    mutation_outcome = completion["mutation_outcome"]
    graph_mutation_count = completion["graph_operation_count"]
    active_pair_binding = _pair_binding(manager.active_pair())
    gates = {
        "intake_status_matches": intake["status"] == expected["run_status"],
        "directive_is_terminal": (
            guard.status(directive_id)["status"] == expected["directive_status"]
        ),
        "mutation_outcome_matches": mutation_outcome == expected["mutation_outcome"],
        "provider_calls_zero": (
            intake["provider_call_count"] == expected["provider_call_count"] == 0
        ),
        "planning_graph_unchanged": intake["graph_sha256_before"] == intake["graph_sha256_after"],
        "completion_receipt_present": (
            bool(completion["receipt_digest"]) == expected["completion_receipt"]
        ),
        "ledger_audit_passes": audit["status"] == "PASS" and not audit["missing_stages"],
        "active_pair_validates": manager.validate_pair(manager.active_pair())["status"] == "PASS",
        "graph_change_posture": (
            completion["graph_sha256_before"] != completion["graph_sha256_after"]
            if mutation_outcome == "GRAPH_CHANGED"
            else completion["graph_sha256_before"] == completion["graph_sha256_after"]
        ),
        "package_hash_bound": intake["package_file_sha256"] == sha256_file(package),
        "graph_delta_lineage_present": bool(intake["graph_delta_summary_digest"]),
        "candidate_resolution_reconciles": (
            candidate_mention_count == intake["entity_resolution_count"]
        ),
        "active_pair_graph_matches_completion": (
            active_pair_binding["graph_sha256"] == completion["graph_sha256_after"]
        ),
        "retrieval_release_evaluation_posture": (
            retrieval_evaluation is None
            if mutation_outcome == "IDEMPOTENT_NOOP"
            else (
                retrieval_evaluation is not None
                and retrieval_evaluation["status"] == "PASS"
                and retrieval_evaluation["prepared_receipt_id"] == release["receipt_id"]
                and retrieval_evaluation["prepared_receipt_digest"]
                == release["receipt_digest"]
            )
        ),
        "semantic_cache_binding_matches_backend": _cache_binding_matches_backend(
            intake.get("semantic_cache"),
            backend,
        ),
    }
    status = "PASS" if all(gates.values()) else "FAIL"
    payload = {
        "case_id": case["case_id"],
        "pipeline": "ingestion",
        "status": status,
        "failure_category": None if status == "PASS" else "GOVERNANCE_GATE_FAILED",
        "cause_code": None if status == "PASS" else "INGESTION_EXPECTATION_MISMATCH",
        "stage": "E0" if mutation_outcome == "GRAPH_CHANGED" else "L4",
        "provider_initialization_count": 0,
        "provider_call_count": intake["provider_call_count"],
        "answer_present": False,
        "graph_mutation_count": graph_mutation_count,
        "directive_status": guard.status(directive_id)["status"],
        "terminal_receipt_digest": completion["receipt_digest"],
        "hard_gates": gates,
        "intake_run_id": intake["run_id"],
        "directive_id": directive_id,
        "mutation_outcome": mutation_outcome,
        "audit": audit,
        "ingestion_evidence": {
            "package_file_sha256": intake["package_file_sha256"],
            "candidate_count": intake["candidate_count"],
            "candidate_mention_count": candidate_mention_count,
            "entity_resolution_count": intake["entity_resolution_count"],
            "graph_delta_summary_digest": intake["graph_delta_summary_digest"],
            "graph_operation_count": intake["graph_operation_count"],
        },
        "retrieval_release_evaluation": retrieval_evaluation,
        "semantic_cache": intake.get("semantic_cache"),
        "runtime_binding": terminal_runtime_binding,
        "initial_runtime_binding": initial_runtime_binding,
        "active_pair_binding": active_pair_binding,
    }
    path = _case_artifact_path(case_dir, payload)
    return {key: payload[key] for key in (
        "case_id", "pipeline", "status", "failure_category", "cause_code", "stage",
        "provider_initialization_count", "provider_call_count", "answer_present",
        "graph_mutation_count", "directive_status", "terminal_receipt_digest", "hard_gates"
    )} | {"artifact_path": str(path)}
