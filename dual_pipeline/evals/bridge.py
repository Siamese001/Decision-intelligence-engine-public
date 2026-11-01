"""Same-runtime ingestion-to-query release evaluation.

The ordinary evaluation runner deliberately isolates each case.  This evaluator
tests the seam that isolation cannot prove: source-bound intake changes must be
activated and then retrievable by a later query from the *same* runtime.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..completion import IngestionCompletionGuard
from ..contracts import create_artifact, validate_file
from ..core import (
    ROOT,
    assert_safe_mutable_path,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json_exclusive,
)
from ..index import EmbeddingBackend, HybridRetriever
from ..intake import IntakeEngine
from ..query import QueryEngine
from ..release import ReleaseManager
from ..retrieval_release import RetrievalReleaseEvaluator
from ..retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
from .contracts import resolve_repository_file
from .runner import EvaluationFailure, _assert_operator_backend


class BridgeEvaluationFailure(EvaluationFailure):
    """The source-to-retrieval bridge could not be evaluated safely."""


def _safe_runtime_root(runtime_root: Path) -> Path:
    """Only mutable, non-source-checkout roots may host bridge state.

    This is intentionally stricter than normal runtime callers.  A bridge
    evaluator creates an intake release, so a source checkout is never an
    acceptable output target.
    """

    root = Path(runtime_root).expanduser().absolute().resolve()
    repository_root = ROOT.resolve()
    try:
        root.relative_to(repository_root)
    except ValueError:
        return root
    raise BridgeEvaluationFailure(
        "eval-bridge-run runtime root must be outside the source checkout"
    )


def _load_suite(path: Path) -> tuple[dict[str, Any], Path]:
    suite_path = Path(path).expanduser().absolute().resolve()
    suite = validate_file(suite_path)
    if suite["contract_id"] != "urn:nee:dual-pipeline:eval-bridge-suite:1.0.0":
        raise BridgeEvaluationFailure("bridge evaluator requires an eval-bridge-suite")
    for case in suite["cases"]:
        package = resolve_repository_file(case["package_path"])
        if sha256_file(package) != case["package_sha256"]:
            raise BridgeEvaluationFailure(
                f"bridge package hash mismatch for {case['case_id']}"
            )
    return suite, suite_path


def _packet_text(runtime_root: Path, run_id: str) -> str:
    packet = read_json(runtime_root / "runs" / run_id / "evidence_packet.json")
    return json.dumps(packet, sort_keys=True).casefold()


def _query(
    *,
    runtime_root: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
    case: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    return QueryEngine(runtime_root, seed_graph, backend).ask(
        case["question"],
        query_type=case["query_type"],
        output_dir=output_dir,
        classification=case["classification"],
        audience=",".join(case["audiences"]),
        provider_egress_allowed=False,
    )


def _case(
    *,
    case: dict[str, Any],
    case_dir: Path,
    runtime_root: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
) -> dict[str, Any]:
    manager = ReleaseManager(runtime_root, seed_graph)
    manager.bootstrap(backend)
    pair_before = manager.active_pair()
    pre_query = _query(
        runtime_root=runtime_root,
        seed_graph=seed_graph,
        backend=backend,
        case=case,
        output_dir=case_dir / "before-query",
    )
    pre_text = _packet_text(runtime_root, pre_query["run_id"])

    package = resolve_repository_file(case["package_path"])
    directive_id = stable_id("bridge-directive", case["case_id"], length=20)
    guard = IngestionCompletionGuard(runtime_root, seed_graph)
    guard.open(
        directive_id=directive_id,
        scope=f"Same-runtime bridge evaluation {case['case_id']}",
        packages=[package],
    )
    active_before_intake = manager.active_pair()
    graph_before = manager._resolve(active_before_intake["graph_path"])
    sidecar_before = manager._resolve(active_before_intake["sidecar_path"])
    intake = IntakeEngine(
        graph_before,
        runtime_root,
        retriever=HybridRetriever(sidecar_before, backend),
        retrieval_audiences=list(case["intake_audiences"]),
    ).ingest(package)
    classification = guard.classify(directive_id)["classification"]
    guard.record_human_decision(
        directive_id,
        decision="APPROVE",
        classification_digest=classification["classification_digest"],
        reviewer="eval-bridge",
        rationale="Approval is limited to the exact sealed bridge-evaluation package.",
    )
    if not intake["graph_operation_count"]:
        raise BridgeEvaluationFailure(
            "bridge evaluation intake proposed no graph operations; choose a positive fixture"
        )
    release = manager.prepare(
        run_id=intake["run_id"],
        approval_id=guard.status(directive_id)["approval"]["approval_id"],
        backend=backend,
    )
    receipt_path = runtime_root / "releases" / (
        f"release-{release['next_pair']['generation_id']}.json"
    )
    retrieval_release_evaluation: dict[str, Any] | None = None
    if backend.backend_id.startswith("flagembedding-bge-m3"):
        suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
            runtime_root, seed_graph
        ).resolve(receipt_path)
        retrieval_release_evaluation = RetrievalReleaseEvaluator(
            runtime_root, seed_graph
        ).evaluate(
            receipt_path,
            Path(suite_resolution["path"]),
            backend=backend,
        )["evaluation"]
        if retrieval_release_evaluation["status"] != "PASS":
            raise BridgeEvaluationFailure(
                "prepared BGE-M3 bridge release failed retrieval-release evaluation"
            )
    manager.activate(receipt_path)
    completion = guard.complete(directive_id, receipt_path)["completion"]
    pair_after = manager.active_pair()

    post_query = _query(
        runtime_root=runtime_root,
        seed_graph=seed_graph,
        backend=backend,
        case=case,
        output_dir=case_dir / "after-query",
    )
    post_text = _packet_text(runtime_root, post_query["run_id"])
    source_terms = [term.casefold() for term in case["expected_source_terms"]]
    gates = {
        "package_hash_bound": sha256_file(package) == case["package_sha256"],
        "intake_completed": intake["status"] == "COMPLETED",
        "intake_has_graph_operations": intake["graph_operation_count"] > 0,
        "completion_receipt_bound": bool(completion["receipt_digest"]),
        "active_generation_changed": (
            pair_before["generation_id"] != pair_after["generation_id"]
        ),
        "active_pair_valid": manager.validate_pair(pair_after)["status"] == "PASS",
        "bge_retrieval_release_evaluation_passed": (
            retrieval_release_evaluation is None
            or retrieval_release_evaluation["status"] == "PASS"
        ),
        "new_source_terms_absent_before_ingestion": all(
            term not in pre_text for term in source_terms
        ),
        "new_source_terms_retrieved_after_activation": all(
            term in post_text for term in source_terms
        ),
        # Candidate-review requests may correctly finish at a governed BLOCKED
        # outcome when no external reasoning provider is authorized.  The
        # bridge proves retrieval and terminal control flow, not answer release.
        "post_query_reached_terminal_state": post_query["status"]
        in {"COMPLETED", "BLOCKED"},
        "post_query_did_not_mutate_graph": post_query["graph_mutation_count"] == 0,
        "seed_graph_unchanged": sha256_file(seed_graph) == case["seed_graph_sha256"],
    }
    status = "PASS" if all(gates.values()) else "FAIL"
    return {
        "case_id": case["case_id"],
        "status": status,
        "cause_code": None if status == "PASS" else "BRIDGE_EXPECTATION_MISMATCH",
        "pre_query_run_id": pre_query["run_id"],
        "ingestion_run_id": intake["run_id"],
        "post_query_run_id": post_query["run_id"],
        "provider_call_count": (
            pre_query["total_external_model_call_count"]
            + intake["provider_call_count"]
            + post_query["total_external_model_call_count"]
        ),
        "graph_mutation_count": intake["graph_operation_count"],
        "hard_gates": gates,
        "artifact_path": str(case_dir / "case-result.json"),
    }


def run_bridge_suite(
    *,
    suite_path: Path,
    runtime_root: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
    evaluation_id: str | None = None,
    tags: Iterable[str] = (),
) -> tuple[dict[str, Any], Path]:
    """Run a source-bound intake and follow-on query within one runtime."""

    _assert_operator_backend(backend)
    verify_snapshot = getattr(backend, "verify_snapshot", None)
    if callable(verify_snapshot):
        verify_snapshot()
    suite, suite_file = _load_suite(suite_path)
    graph = Path(seed_graph).expanduser().absolute().resolve()
    if sha256_file(graph) != suite["graph_sha256"]:
        raise BridgeEvaluationFailure("bridge suite graph binding mismatch")
    root = _safe_runtime_root(runtime_root)
    selected = [
        case
        for case in suite["cases"]
        if not tags or set(tags).intersection(case["tags"])
    ]
    if not selected:
        raise BridgeEvaluationFailure("bridge case selection is empty")
    evaluation_id = evaluation_id or stable_id(
        "eval-bridge", suite["suite_id"], suite["canonical_digest"]["value"], utc_now(),
        length=28,
    )
    evaluation_dir = root / "bridge-evaluations" / evaluation_id
    assert_safe_mutable_path(root, evaluation_dir, label="bridge evaluation output")
    if evaluation_dir.exists():
        raise FileExistsError(evaluation_dir)
    evaluation_dir.mkdir(parents=True, exist_ok=False)

    results: list[dict[str, Any]] = []
    for case in selected:
        case_dir = evaluation_dir / "cases" / case["case_id"]
        assert_safe_mutable_path(evaluation_dir, case_dir, label="bridge case output")
        case_dir.mkdir(parents=True, exist_ok=False)
        case = dict(case) | {"seed_graph_sha256": suite["graph_sha256"]}
        try:
            row = _case(
                case=case,
                case_dir=case_dir,
                runtime_root=root,
                seed_graph=graph,
                backend=backend,
            )
        except Exception as exc:
            row = {
                "case_id": case["case_id"],
                "status": "FAIL",
                "cause_code": type(exc).__name__,
                "pre_query_run_id": None,
                "ingestion_run_id": None,
                "post_query_run_id": None,
                "provider_call_count": 0,
                "graph_mutation_count": 0,
                "hard_gates": {"execution_completed": False},
                "artifact_path": str(case_dir / "case-result.json"),
                "error": str(exc),
            }
        write_json_exclusive(case_dir / "case-result.json", row)
        results.append({key: row[key] for key in (
            "case_id", "status", "cause_code", "pre_query_run_id",
            "ingestion_run_id", "post_query_run_id", "provider_call_count",
            "graph_mutation_count", "hard_gates", "artifact_path",
        )})
    aggregate = {
        "case_count": len(results),
        "passed_case_count": sum(row["status"] == "PASS" for row in results),
        "failed_case_count": sum(row["status"] != "PASS" for row in results),
        "provider_call_count": sum(row["provider_call_count"] for row in results),
        "graph_mutation_count": sum(row["graph_mutation_count"] for row in results),
        "hard_gate_failure_count": sum(
            not passed for row in results for passed in row["hard_gates"].values()
        ),
    }
    thresholds = suite["thresholds"]
    status = "PASS" if (
        aggregate["failed_case_count"] == 0
        and aggregate["provider_call_count"] <= thresholds["maximum_provider_call_count"]
        and aggregate["graph_mutation_count"] <= thresholds["maximum_graph_mutation_count"]
        and aggregate["hard_gate_failure_count"] == 0
    ) else "FAIL"
    result = create_artifact(
        "eval-bridge-result",
        evaluation_id,
        evaluation_id=evaluation_id,
        suite_id=suite["suite_id"],
        suite_digest=suite["canonical_digest"]["value"],
        suite_file_sha256=sha256_file(suite_file),
        graph_sha256=sha256_file(graph),
        embedding_backend={
            "backend_id": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "assurance_tier": backend.assurance_tier,
            "production_qualified": backend.production_qualified,
        },
        case_results=results,
        aggregate=aggregate,
        status=status,
        evaluated_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.evaluation-boundary": {
                "role": "post_runtime_verification",
                "runtime_judge_substitution": False,
                "same_runtime_bridge": True,
                "source_checkout_mutation": False,
            }
        },
    )
    result_path = evaluation_dir / "bridge-result.json"
    write_json_exclusive(result_path, result)
    return result, result_path


__all__ = ["BridgeEvaluationFailure", "run_bridge_suite"]
