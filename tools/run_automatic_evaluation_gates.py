#!/usr/bin/env python3
"""Run the network-free automatic evaluation gate used before push."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.evals.assurance import run_workflow_suite  # noqa: E402
from dual_pipeline.l1_evaluation import run_l1_evaluation_suite  # noqa: E402

DEFAULT_SUITE = Path("evals/suites/agentic-workflow-assurance-e1.json")
DEFAULT_OUTPUT_ROOT = Path(".local/automatic-evals")
DEFAULT_TREATMENT = "candidate-bounded"
DEFAULT_L1_SUITE = Path("evals/suites/query-l1-agentic-control-e1.json")


def _under_repository(repository_root: Path, value: Path) -> Path:
    candidate = value.expanduser()
    if not candidate.is_absolute():
        candidate = repository_root / candidate
    return candidate.resolve()


def run_gate(
    *,
    repository_root: Path,
    suite_path: Path = DEFAULT_SUITE,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    treatment_id: str = DEFAULT_TREATMENT,
    evaluation_id: str | None = None,
    l1_suite_path: Path = DEFAULT_L1_SUITE,
    include_full_mesh: bool = False,
    progress: bool = True,
) -> dict[str, object]:
    """Execute and validate the automatic workflow gate."""

    repository_root = repository_root.expanduser().resolve()
    suite_path = _under_repository(repository_root, suite_path)
    output_root = _under_repository(repository_root, output_root)
    l1_suite_path = _under_repository(repository_root, l1_suite_path)

    total_stages = 3 if include_full_mesh else 2
    pbar = None
    if progress and tqdm is not None and sys.stderr.isatty():
        pbar = tqdm(total=total_stages, desc="Automatic Evaluation Gates", unit="gate", file=sys.stderr)

    # Stage 1: Workflow Assurance Suite
    if pbar:
        pbar.set_postfix_str("Running Workflow Assurance")
    result, result_path = run_workflow_suite(
        suite_path=suite_path,
        output_root=output_root,
        treatment_id=treatment_id,
        evaluation_id=evaluation_id,
    )
    validate_artifact(result)
    if pbar:
        pbar.update(1)

    # Stage 2: L1 Controller Evaluation
    if pbar:
        pbar.set_postfix_str("Running L1 Controller Gate")
    l1_result = run_l1_evaluation_suite(
        suite_path=l1_suite_path,
        output_root=output_root / "l1-controller",
        evaluation_id=evaluation_id or "automatic-l1-controller",
    )
    if pbar:
        pbar.update(1)

    # Stage 3: Full Mesh A->D Integration Gate (Optional / Pre-push profile)
    full_mesh_result = None
    if include_full_mesh:
        if pbar:
            pbar.set_postfix_str("Running Full Mesh A->D Gate")
        try:
            from dual_pipeline.model_registry import ModelRegistry, ModelTier
            from evals.evaluate_full_mesh_a_to_d_live import FullMeshAtoDLiveEvaluator
            def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            evaluator = FullMeshAtoDLiveEvaluator(
                model=def_m,
                reasoning_effort=def_e,
                mock_mode=True,
                output_dir=output_root / "full-mesh-live",
            )
            mesh_out = evaluator.run_full_mesh_pipeline()
            full_mesh_result = mesh_out.get("audit_envelope")
        except Exception as exc:
            full_mesh_result = {
                "status": "FAIL",
                "error": str(exc),
            }
        if pbar:
            pbar.update(1)

    if pbar:
        pbar.set_postfix_str("Complete")
        pbar.close()

    aggregate = result["aggregate"]
    overall_status = "PASS"
    if result["status"] != "PASS" or l1_result["status"] != "PASS":
        overall_status = "FAIL"
    if include_full_mesh and (not full_mesh_result or full_mesh_result.get("status") != "PASS"):
        overall_status = "FAIL"

    report = {
        "status": overall_status,
        "evaluation_id": result["evaluation_id"],
        "suite_id": result["suite_id"],
        "suite_digest": result["suite_digest"],
        "treatment_id": result["treatment_id"],
        "case_count": aggregate["case_count"],
        "passed_case_count": aggregate["passed_case_count"],
        "failed_case_count": aggregate["failed_case_count"],
        "invalid_case_count": aggregate["invalid_case_count"],
        "external_provider_calls": 0,
        "graph_mutations": 0,
        "result_digest": result["canonical_digest"]["value"],
        "result_path": str(result_path),
        "l1_evaluation": l1_result,
    }
    if full_mesh_result is not None:
        report["full_mesh_evaluation"] = full_mesh_result

    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the sealed candidate-bounded workflow suite and fail unless "
            "every hard gate passes."
        )
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--treatment-id", default=DEFAULT_TREATMENT)
    parser.add_argument("--evaluation-id")
    parser.add_argument("--l1-suite", type=Path, default=DEFAULT_L1_SUITE)
    parser.add_argument("--include-full-mesh", action="store_true", help="Include full mesh A->D live integration gate")
    parser.add_argument("--no-progress", action="store_true", help="Disable visual progress bars")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_gate(
        repository_root=args.repository_root,
        suite_path=args.suite,
        output_root=args.output_root,
        treatment_id=args.treatment_id,
        evaluation_id=args.evaluation_id,
        l1_suite_path=args.l1_suite,
        include_full_mesh=args.include_full_mesh,
        progress=not args.no_progress,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

