"""Evaluation suite orchestration and sealing."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable

from ...contracts import create_artifact
from ...core import (
    assert_safe_mutable_path,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json_exclusive,
)
from ...drift_guard import (
    assert_file_unchanged,
    assert_runtime_binding_unchanged,
    capture_runtime_binding,
)
from ...index import EmbeddingBackend
from ...release import ReleaseManager
from ..contracts import load_suite, write_markdown_report
from .compare import _aggregate
from .constants import (
    EvaluationFailure,
    _assert_expected_records_present,
    _assert_operator_backend,
    _evaluation_policy_digests,
    _failure_result,
    _validated_template_pair_binding,
)
from .ingestion_case import _ingestion_case
from .query_case import _query_case
from .workspace import (
    _evaluation_workspace_lifecycle,
    _finalize_evaluation_workspace,
    _remove_workspace,
    prune_expired_failed_workspaces,
)


def _dispatch(name: str, fallback: Any) -> Any:
    mod = sys.modules.get("dual_pipeline.evals.runner")
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return fallback


def _run_single_eval_case(
    case: dict[str, Any],
    ordinal: int,
    evaluation_dir: Path,
    work_root: Path,
    graph: Path,
    backend: EmbeddingBackend,
    template_runtime: Any,
) -> dict[str, Any]:
    case_dir = evaluation_dir / "cases" / case["case_id"]
    assert_safe_mutable_path(evaluation_dir, case_dir, label="evaluation case path")
    case_dir.mkdir(parents=True, exist_ok=False)
    case_runtime = work_root / f"c{ordinal:02d}-{stable_id('c', case['case_id'], length=8)}"
    assert_safe_mutable_path(work_root, case_runtime, label="evaluation case runtime")
    try:
        runner = _query_case if case["pipeline"] == "query" else _ingestion_case
        return runner(
            case,
            case_dir=case_dir,
            case_runtime=case_runtime,
            seed_graph=graph,
            backend=backend,
            template_runtime=template_runtime,
        )
    except Exception as exc:  # preserve remaining independent case results
        return _failure_result(case=case, case_dir=case_dir, error=exc)


def _collect_case_evidence(
    case_results: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    case_pair_bindings = []
    case_semantic_cache_evidence = []
    for case_result in case_results:
        artifact = read_json(Path(case_result["artifact_path"]))
        binding = artifact.get("active_pair_binding")
        if isinstance(binding, dict):
            case_pair_bindings.append({"case_id": case_result["case_id"], **binding})
        cache_evidence = artifact.get("semantic_cache")
        if isinstance(cache_evidence, dict):
            case_semantic_cache_evidence.append({
                "case_id": case_result["case_id"],
                "pipeline": case_result["pipeline"],
                "lookup_hit": cache_evidence.get("lookup_hit"),
                "lookup_reason": cache_evidence.get("lookup_reason"),
                "replayed_output": cache_evidence.get("replayed_output"),
                "lookup_hit_count": cache_evidence.get("lookup_hit_count"),
                "writeback": cache_evidence.get("writeback"),
                "writeback_count": cache_evidence.get("writeback_count"),
                "embedding_binding": cache_evidence.get("embedding_binding"),
            })
    return case_pair_bindings, case_semantic_cache_evidence


def run_suite(
    *,
    suite_path: Path,
    runtime_root: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
    evaluation_id: str | None = None,
    tags: Iterable[str] = (),
) -> tuple[dict[str, Any], Path]:
    """Run selected suite cases in isolated per-case runtimes and seal a result."""

    _dispatch("_assert_operator_backend", _assert_operator_backend)(backend)
    suite_file = Path(suite_path).expanduser().resolve()
    suite_file_sha256 = sha256_file(suite_file)
    suite = load_suite(suite_file)
    assert_file_unchanged(
        suite_file,
        suite_file_sha256,
        label="evaluation suite source",
    )
    graph = Path(seed_graph).expanduser().resolve()
    if not graph.is_file():
        raise FileNotFoundError(graph)
    graph_sha256 = sha256_file(graph)
    if graph_sha256 != suite["graph_sha256"]:
        raise EvaluationFailure("evaluation suite is not bound to the supplied graph bytes")
    selection = set(tags)
    cases = [
        case for case in suite["cases"]
        if not selection or selection.intersection(case["tags"])
    ]
    if not cases:
        raise EvaluationFailure("evaluation case selection is empty")
    _assert_expected_records_present(cases, graph)
    evaluation_policy_digests = _evaluation_policy_digests()
    evaluation_id = evaluation_id or stable_id(
        "eval", suite["suite_id"], suite["canonical_digest"]["value"], utc_now(), length=28
    )
    root = Path(runtime_root).expanduser().absolute().resolve()
    prune_expired_failed_workspaces(root)
    evaluation_dir = root / "evaluations" / evaluation_id
    assert_safe_mutable_path(root, evaluation_dir, label="evaluation output path")
    if evaluation_dir.exists():
        raise FileExistsError(f"evaluation output already exists: {evaluation_dir}")
    # Keep mutable pipeline state shallow.  Ingestion directives include package
    # hashes in receipt filenames, which otherwise exceeds legacy Windows path
    # limits when an eval root itself is deeply nested.
    work_root = root / ".eval-work" / stable_id("ew", evaluation_id, length=10)
    assert_safe_mutable_path(root, work_root, label="evaluation work path")
    work_root.mkdir(parents=True, exist_ok=False)
    template_runtime = work_root / "verified-bge-template"
    assert_safe_mutable_path(
        work_root,
        template_runtime,
        label="evaluation BGE template path",
    )
    template_manager = _dispatch("ReleaseManager", ReleaseManager)(template_runtime, graph)
    try:
        template_pair = template_manager.bootstrap(backend)
        template_pair_binding = _validated_template_pair_binding(
            template_manager,
            template_pair,
            backend,
        )
        template_runtime_binding = capture_runtime_binding(
            manager=template_manager,
            active_pair=template_pair,
            backend=backend,
            policy_digests=evaluation_policy_digests,
        )
    except Exception:
        _remove_workspace(root / ".eval-work", work_root)
        raise
    evaluation_dir.mkdir(parents=True, exist_ok=False)
    case_results: list[dict[str, Any]] = []
    from .prewarmed_backend import prewarm_suite_queries
    prewarmed_backend = prewarm_suite_queries(backend, cases)
    from dual_pipeline.l0.ingestion_timing import timed_child_span
    with timed_child_span("retrieval_repeats", {"case_count": len(cases)}):
        for ordinal, case in enumerate(cases, start=1):
            result = _run_single_eval_case(
                case,
                ordinal=ordinal,
                evaluation_dir=evaluation_dir,
                work_root=work_root,
                graph=graph,
                backend=prewarmed_backend,
                template_runtime=template_runtime,
            )
            case_results.append(result)
    aggregate = _aggregate(case_results)
    case_pair_bindings, case_semantic_cache_evidence = _collect_case_evidence(case_results)
    pair_binding_complete = len(case_pair_bindings) == len(case_results)
    semantic_cache_evidence_complete = (
        len(case_semantic_cache_evidence) == len(case_results)
    )
    pair_binding_matches_backend = pair_binding_complete and all(
        binding["embedding_backend"] == backend.backend_id
        and binding["model_id"] == backend.model_id
        and binding["model_revision"] == backend.model_revision
        and binding["model_manifest_digest"] == backend.model_manifest_digest
        for binding in case_pair_bindings
    )
    assert_file_unchanged(
        suite_file,
        suite_file_sha256,
        label="evaluation suite source",
    )
    assert_file_unchanged(graph, graph_sha256, label="evaluation seed graph")
    terminal_policy_digests = _evaluation_policy_digests()
    assert_runtime_binding_unchanged(
        manager=template_manager,
        expected_binding=template_runtime_binding,
        backend=backend,
        policy_digests=terminal_policy_digests,
        stage="evaluation-terminal",
    )
    thresholds = suite["thresholds"]
    status = "PASS" if (
        aggregate["case_pass_rate"] >= thresholds["minimum_case_pass_rate"]
        and aggregate["provider_call_count"] <= thresholds["maximum_provider_call_count"]
        and aggregate["graph_mutation_count"] <= thresholds["maximum_graph_mutation_count"]
        and aggregate["hard_gate_failure_count"] == 0
        and pair_binding_complete
        and pair_binding_matches_backend
        and semantic_cache_evidence_complete
    ) else "FAIL"
    evaluated_at = utc_now()
    workspace_lifecycle = _evaluation_workspace_lifecycle(
        work_root,
        status=status,
        evaluated_at=evaluated_at,
    )
    result = create_artifact(
        "eval-run-result",
        evaluation_id,
        evaluation_id=evaluation_id,
        suite_id=suite["suite_id"],
        suite_digest=suite["canonical_digest"]["value"],
        suite_file_sha256=suite_file_sha256,
        pipeline=suite["pipeline"],
        graph_sha256=graph_sha256,
        case_results=case_results,
        aggregate=aggregate,
        status=status,
        evaluated_at=evaluated_at,
        extensions={
            "workspace-lifecycle": workspace_lifecycle,
            "com.partner_advisory.nee.embedding-backend": {
                "backend_id": backend.backend_id,
                "model_id": backend.model_id,
                "model_revision": backend.model_revision,
                "model_manifest_digest": backend.model_manifest_digest,
                "assurance_tier": backend.assurance_tier,
                "production_qualified": backend.production_qualified,
            },
            "com.partner_advisory.nee.evaluation-retrieval-binding": {
                "template_pair_binding": template_pair_binding,
                "case_pair_bindings": case_pair_bindings,
                "case_pair_binding_complete": pair_binding_complete,
                "case_pair_binding_matches_backend": pair_binding_matches_backend,
                "semantic_cache_evidence": case_semantic_cache_evidence,
                "semantic_cache_evidence_complete": semantic_cache_evidence_complete,
                "template_runtime_binding": template_runtime_binding,
                "suite_file_sha256": suite_file_sha256,
                "graph_sha256": graph_sha256,
                "policy_digests": terminal_policy_digests,
            },
            "com.partner_advisory.nee.evaluation-boundary": {
                "role": "post_runtime_verification",
                "runtime_judge_substitution": False,
                "graph_retrieval_qualification_case_count": sum(
                    case.get("evaluation_scope") == "graph_retrieval_qualification"
                    for case in cases
                    if case["pipeline"] == "query"
                ),
                "runtime_control_verification_case_count": sum(
                    case.get("evaluation_scope") == "runtime_control_verification"
                    for case in cases
                    if case["pipeline"] == "query"
                ),
            },
        },
    )
    finalized_lifecycle = _finalize_evaluation_workspace(
        work_root,
        evaluation_id=evaluation_id,
        status=status,
        evaluated_at=evaluated_at,
    )
    if finalized_lifecycle != workspace_lifecycle:
        raise EvaluationFailure("evaluation workspace lifecycle result changed during finalization")
    result_path = evaluation_dir / "evaluation-result.json"
    write_json_exclusive(result_path, result)
    write_markdown_report(evaluation_dir / "evaluation-report.md", result)
    return result, result_path
