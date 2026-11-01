"""Repeated sealed evaluations that reject behavioral drift."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..contracts import create_artifact
from ..core import (
    ROOT,
    assert_safe_mutable_path,
    digest,
    sha256_file,
    stable_id,
    utc_now,
    write_json_exclusive,
)
from ..drift_guard import assert_file_unchanged
from ..index import EmbeddingBackend
from .contracts import load_suite
from .runner import EvaluationFailure, _assert_operator_backend, run_suite


class StressEvaluationFailure(EvaluationFailure):
    """A stress evaluation cannot safely produce a release signal."""


def _safe_runtime_root(runtime_root: Path) -> Path:
    root = Path(runtime_root).expanduser().absolute().resolve()
    try:
        root.relative_to(ROOT.resolve())
    except ValueError:
        return root
    raise StressEvaluationFailure(
        "eval-stress-run runtime root must be outside the source checkout"
    )


def _behavior_signature(result: dict[str, Any]) -> str:
    """Ignore runtime IDs and timestamps but retain all outcome semantics."""

    return digest(
        [
            {
                "case_id": row["case_id"],
                "pipeline": row["pipeline"],
                "status": row["status"],
                "cause_code": row["cause_code"],
                "provider_call_count": row["provider_call_count"],
                "graph_mutation_count": row["graph_mutation_count"],
                "hard_gates": row["hard_gates"],
            }
            for row in result["case_results"]
        ]
    )


def run_stress_suite(
    *,
    suite_path: Path,
    runtime_root: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
    repetitions: int,
    stress_id: str | None = None,
    tags: Iterable[str] = (),
) -> tuple[dict[str, Any], Path]:
    """Repeat a suite with fresh workspaces and seal repeatability evidence."""

    if not 2 <= repetitions <= 20:
        raise StressEvaluationFailure("repetitions must be between 2 and 20")
    _assert_operator_backend(backend)
    suite_file = Path(suite_path).expanduser().absolute().resolve()
    suite_file_sha256 = sha256_file(suite_file)
    suite = load_suite(suite_file)
    assert_file_unchanged(
        suite_file,
        suite_file_sha256,
        label="stress evaluation suite source",
    )
    graph = Path(seed_graph).expanduser().absolute().resolve()
    graph_hash_before = sha256_file(graph)
    if graph_hash_before != suite["graph_sha256"]:
        raise StressEvaluationFailure("stress suite graph binding mismatch")
    root = _safe_runtime_root(runtime_root)
    stress_id = stress_id or stable_id(
        "eval-stress",
        suite["suite_id"],
        suite["canonical_digest"]["value"],
        repetitions,
        utc_now(),
        length=28,
    )
    output_dir = root / "stress-evaluations" / stress_id
    assert_safe_mutable_path(root, output_dir, label="stress evaluation output")
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    iterations: list[dict[str, Any]] = []
    for ordinal in range(1, repetitions + 1):
        evaluation_id = stable_id("stress-run", stress_id, ordinal, length=28)
        result, result_path = run_suite(
            suite_path=suite_file,
            runtime_root=root,
            seed_graph=graph,
            backend=backend,
            evaluation_id=evaluation_id,
            tags=tags,
        )
        aggregate = result["aggregate"]
        iterations.append(
            {
                "ordinal": ordinal,
                "evaluation_id": result["evaluation_id"],
                "result_path": str(result_path),
                "result_digest": result["canonical_digest"]["value"],
                "status": result["status"],
                "case_count": aggregate["case_count"],
                "provider_call_count": aggregate["provider_call_count"],
                "graph_mutation_count": aggregate["graph_mutation_count"],
                "hard_gate_failure_count": aggregate["hard_gate_failure_count"],
                "behavior_signature": _behavior_signature(result),
            }
        )
    signatures = {row["behavior_signature"] for row in iterations}
    provider_counts = {row["provider_call_count"] for row in iterations}
    case_counts = {row["case_count"] for row in iterations}
    hard_gate_failures = sum(row["hard_gate_failure_count"] for row in iterations)
    aggregate = {
        "all_iterations_passed": all(row["status"] == "PASS" for row in iterations),
        "behavior_stable": len(signatures) == 1,
        "provider_call_count_stable": len(provider_counts) == 1,
        "case_count_stable": len(case_counts) == 1,
        "seed_graph_unchanged": sha256_file(graph) == graph_hash_before,
        "pass_workspaces_cleaned": all(
            row["status"] != "PASS"
            or not (
                root / ".eval-work" / stable_id("ew", row["evaluation_id"], length=10)
            ).exists()
            for row in iterations
        ),
        "hard_gate_failure_count": hard_gate_failures,
    }
    assert_file_unchanged(
        suite_file,
        suite_file_sha256,
        label="stress evaluation suite source",
    )
    assert_file_unchanged(graph, graph_hash_before, label="stress seed graph")
    status = "PASS" if (
        all(
            value
            for key, value in aggregate.items()
            if key != "hard_gate_failure_count"
        )
        and hard_gate_failures == 0
    ) else "FAIL"
    result = create_artifact(
        "eval-stress-result",
        stress_id,
        stress_id=stress_id,
        suite_id=suite["suite_id"],
        suite_digest=suite["canonical_digest"]["value"],
        suite_file_sha256=suite_file_sha256,
        graph_sha256=graph_hash_before,
        embedding_backend={
            "backend_id": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "assurance_tier": backend.assurance_tier,
            "production_qualified": backend.production_qualified,
        },
        iteration_count=repetitions,
        iterations=iterations,
        aggregate=aggregate,
        status=status,
        evaluated_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.evaluation-boundary": {
                "role": "post_runtime_verification",
                "runtime_judge_substitution": False,
                "stress_repetitions": repetitions,
            }
        },
    )
    result_path = output_dir / "stress-result.json"
    write_json_exclusive(result_path, result)
    return result, result_path


__all__ = ["StressEvaluationFailure", "run_stress_suite"]
