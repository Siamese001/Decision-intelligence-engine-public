#!/usr/bin/env python3
"""Run the bounded ten-query Decision Intelligence E2E check locally.

The provider is the sealed network-free evaluator. This tool never sends the
governed packet to an external service and never mutates graph truth.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json, sha256_file, write_json
from dual_pipeline.evals.fakes import SealedEvalProviderFactory
from dual_pipeline.index import EmbeddingBackend
from dual_pipeline.query import QueryEngine
from dual_pipeline.release import ReleaseManager
from dual_pipeline.telemetry import RunRecorder
from tests.embedding_fixture import BgeM3TestBackend

CASES: tuple[dict[str, str], ...] = (
    {
        "case_id": "q01-nea-project_beacon-relationship",
        "query_type": "relationship_explanation",
        "question": "Explain how NEA and Project Beacon are connected and cite governed evidence.",
    },
    {
        "case_id": "q02-ai-hub-project_beacon-relationship",
        "query_type": "relationship_explanation",
        "question": "Explain governed connections among AI-enabled PDLC, the AI Hub, and Project Beacon delivery, including dependencies and evidence.",
    },
    {
        "case_id": "q03-cross-workstream-network",
        "query_type": "cross_workstream_synthesis",
        "question": "How do Project Beacon, the AI Hub, partner dependencies, risks, and opportunities connect across workstreams?",
    },
    {
        "case_id": "q04-project_beacon-nea-patterns",
        "query_type": "cross_workstream_synthesis",
        "question": "Synthesize governed patterns across Project Beacon and NEA.",
    },
    {
        "case_id": "q05-gap-risk-conflict",
        "query_type": "gap_risk_inconsistency",
        "question": "Identify governed Project Beacon gaps, risks, conflicts, and missing evidence.",
    },
    {
        "case_id": "q06-opportunity-nba",
        "query_type": "opportunity_nba",
        "question": "What are the strongest connected opportunities across NEA and Project Beacon, which pain points and dependencies do they address, and what evidence supports them?",
    },
    {
        "case_id": "q07-candidate-review",
        "query_type": "candidate_promotion_review",
        "question": "Review candidate Project Beacon patterns against approved evidence without promotion.",
    },
    {
        "case_id": "q08-approved-relationship-map",
        "query_type": "relationship_explanation",
        "question": "Which approved relationships connect Project Beacon to architecture components and delivery workstreams?",
    },
    {
        "case_id": "q09-facts-vs-hypotheses",
        "query_type": "cross_workstream_synthesis",
        "question": "Separate approved graph facts from candidate hypotheses and recommended next actions for Project Beacon.",
    },
    {
        "case_id": "q10-decision-brief-reuse",
        "query_type": "opportunity_nba",
        "question": "Produce a decision brief on Project Beacon reuse, dependencies, risks, and next-best actions, citing only governed evidence.",
    },
)

BASE_STAGES = (
    "U0",
    "L1",
    "L0",
    "C0.1",
    "C0.2",
    "C0.3",
    "L2D",
)
POST_L2_STAGES = ("PA", "L2")
FINAL_STAGES = ("X1", "X2", "X3", "L4")


def _audit(
    runtime_root: Path,
    result: dict[str, Any],
    *,
    l2_reached: bool,
) -> dict[str, Any]:
    run_id = str(result["run_id"])
    trace_id = str(result["trace_id"])
    run_dir = runtime_root / "runs" / run_id
    first_event = sorted((run_dir / "learning_events").glob("*.json"))[0]
    first = read_json(first_event)
    stages = BASE_STAGES + (POST_L2_STAGES if l2_reached else ()) + FINAL_STAGES
    return RunRecorder(
        runtime_root,
        run_id=run_id,
        trace_id=trace_id,
        use_case="query",
        graph_snapshot=first.get("graph_snapshot"),
        model_or_index_generation=first.get("model_or_index_generation"),
    ).audit(stages)


def _case_report(
    *,
    case: dict[str, str],
    result: dict[str, Any],
    output_dir: Path,
    runtime_root: Path,
    provider: SealedEvalProviderFactory,
) -> dict[str, Any]:
    run_dir = runtime_root / "runs" / str(result["run_id"])
    plan = read_json(output_dir / "query_plan.json")
    destination = read_json(output_dir / "query_destination_decision.json")
    retrieval = read_json(output_dir / "retrieval_evidence_report.json")
    l2_path = run_dir / "l2_execution.json"
    l2 = read_json(l2_path) if l2_path.is_file() else None
    l2_reached = l2 is not None
    audit = _audit(runtime_root, result, l2_reached=l2_reached)
    provider_calls = int(
        l2.get("total_l2_provider_call_count", 0) if l2 is not None else 0
    )
    terminal = result.get("terminal_outcome")
    fail_closed_block = (
        result.get("status") == "BLOCKED"
        and terminal == "RETRIEVAL_COVERAGE_INSUFFICIENT"
        and provider_calls == 0
    )
    route_aligned = destination.get("destination") == "NEE_DECISION_INTELLIGENCE"
    graph_unchanged = int(result.get("graph_mutation_count", 0)) == 0
    execution_aligned = audit.get("status") == "PASS" and (
        fail_closed_block
        or (
            result.get("status") == "COMPLETED"
            and l2 is not None
            and l2.get("l2_disposition") in {"PRIMARY", "REPAIRED", "ESCALATED"}
            and bool(result.get("answer"))
        )
    )
    status = "PASS" if route_aligned and graph_unchanged and execution_aligned else "FAIL"
    return {
        "case_id": case["case_id"],
        "question": case["question"],
        "query_type": case["query_type"],
        "run_id": result["run_id"],
        "trace_id": result["trace_id"],
        "expected": {
            "destination": "NEE_DECISION_INTELLIGENCE",
            "l2_policy": "required",
            "graph_mutation_count": 0,
        },
        "actual": {
            "status": result.get("status"),
            "destination": destination.get("destination"),
            "l2_policy": plan.get("l2_policy"),
            "l2_reached": l2_reached,
            "l2_disposition": l2.get("l2_disposition") if l2 else None,
            "provider_failure_code": l2.get("provider_failure_code") if l2 else None,
            "provider_call_count": provider_calls,
            "answer_present": bool(result.get("answer")),
            "terminal_outcome": terminal,
            "graph_mutation_count": result.get("graph_mutation_count", 0),
            "required_material_record_ids_by_lane": (
                l2.get("required_material_record_ids_by_lane")
                if l2
                else retrieval.get("required_material_record_ids_by_lane", {})
            ),
            "missing_required_material_record_ids": (
                l2.get("missing_required_material_record_ids", []) if l2 else []
            ),
            "provider_implementation": (
                "sealed-eval-fake-provider" if provider_calls else None
            ),
        },
        "alignment": {
            "overall": status,
            "route": "PASS" if route_aligned else "FAIL",
            "execution": "PASS" if execution_aligned else "FAIL",
            "graph_mutation": "PASS" if graph_unchanged else "FAIL",
            "fail_closed_coverage_block": fail_closed_block,
            "audit_status": audit.get("status"),
            "audit_digest": audit.get("event_chain_digest"),
            "audit_stage_count": audit.get("event_count"),
        },
        "artifacts": {
            "query_output_dir": str(output_dir.resolve()),
            "runtime_run_dir": str(run_dir.resolve()),
            "query_plan": str((output_dir / "query_plan.json").resolve()),
            "retrieval_evidence_report": str(
                (output_dir / "retrieval_evidence_report.json").resolve()
            ),
            "l2_execution": str(l2_path.resolve()) if l2 else None,
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Decision Intelligence E2E",
        "",
        f"Status: **{report['status']}**",
        "",
        "The sealed local evaluator was used. A coverage block is PASS only when it occurs before PA/L2 with zero provider calls.",
        "",
        "| Query | Type | Route | L2 | Disposition | Calls | Outcome | Alignment |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    for case in report["cases"]:
        actual = case["actual"]
        lines.append(
            "| {case_id} | {query_type} | {destination} | {l2} | {disposition} | {calls} | {outcome} | {alignment} |".format(
                case_id=case["case_id"],
                query_type=case["query_type"],
                destination=actual["destination"],
                l2="reached" if actual["l2_reached"] else "pre-L2",
                disposition=actual["l2_disposition"] or "-",
                calls=actual["provider_call_count"],
                outcome=actual["terminal_outcome"] or actual["status"],
                alignment=case["alignment"]["overall"],
            )
        )
    lines.extend(
        [
            "",
            "## Native Evidence",
            "",
            f"- Runtime root: `{report['runtime']['runtime_root']}`",
            f"- Active generation: `{report['runtime']['generation_id']}`",
            f"- Embedding backend: `{report['runtime']['embedding_backend']}`",
            f"- Graph SHA-256: `{report['runtime']['graph_sha256']}`",
            f"- Provider calls: `{report['aggregate']['provider_call_count']}`",
            f"- Graph mutations: `{report['aggregate']['graph_mutation_count']}`",
            f"- Route alignment: `{report['aggregate']['route_alignment_pass_count']}/{report['aggregate']['case_count']}`",
            "",
            "Each query row has native plan, retrieval report, audit, and (when reached) `l2_execution.json` paths in the JSON report.",
            "",
        ]
    )
    return "\n".join(lines)


def run(*, runtime_root: Path, graph: Path, output_root: Path) -> Path:
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    backend: EmbeddingBackend = BgeM3TestBackend()
    provider = SealedEvalProviderFactory()
    run_label = f"local-e2e-{output_root.name}"
    engine = QueryEngine(
        runtime_root,
        graph,
        backend,
        provider_factory=provider,
    )
    cases: list[dict[str, Any]] = []
    for case in CASES:
        query_output = output_root / case["case_id"]
        result = engine.ask(
            case["question"],
            query_type=case["query_type"],
            output_dir=query_output,
            user_id=run_label,
            classification="internal",
            audience="internal_consulting",
            provider_egress_allowed=True,
        )
        cases.append(
            _case_report(
                case=case,
                result=result,
                output_dir=query_output,
                runtime_root=runtime_root,
                provider=provider,
            )
        )

    manager = ReleaseManager(runtime_root, graph)
    active = manager.active_pair()
    active_graph = manager._resolve(active["graph_path"])
    report: dict[str, Any] = {
        "schema_version": "2.0.0",
        "artifact_id": "decision-intelligence-e2e-10",
        "status": "PASS" if all(case["alignment"]["overall"] == "PASS" for case in cases) else "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": {
            "case_count": len(cases),
            "destination": "NEE_DECISION_INTELLIGENCE",
            "provider_posture": "sealed-eval-fake-provider",
            "real_openai_egress": "NOT_USED",
            "graph_mutation_expected": 0,
        },
        "runtime": {
            "runtime_root": str(runtime_root.resolve()),
            "generation_id": active.get("generation_id"),
            "embedding_backend": active.get("embedding_backend"),
            "model_id": active.get("model_id"),
            "model_revision": active.get("model_revision"),
            "model_manifest_digest": active.get("model_manifest_digest"),
            "graph_sha256": sha256_file(active_graph),
            "production_qualified": active.get("production_qualified"),
        },
        "aggregate": {
            "case_count": len(cases),
            "route_alignment_pass_count": sum(
                case["alignment"]["route"] == "PASS" for case in cases
            ),
            "l2_reached_count": sum(case["actual"]["l2_reached"] for case in cases),
            "answer_present_count": sum(case["actual"]["answer_present"] for case in cases),
            "provider_call_count": sum(
                case["actual"]["provider_call_count"] for case in cases
            ),
            "graph_mutation_count": sum(
                case["actual"]["graph_mutation_count"] for case in cases
            ),
            "expected_fail_closed_coverage_blocks": sum(
                case["alignment"]["fail_closed_coverage_block"] for case in cases
            ),
        },
        "provider_requests": [
            {"model": request.model, "purpose": request.purpose}
            for request in provider.requests
        ],
        "cases": cases,
    }
    json_path = output_root / "batch_report.json"
    write_json(json_path, report)
    (output_root / "batch_report.md").write_text(_markdown(report), encoding="utf-8")
    return json_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(run(runtime_root=args.runtime_root, graph=args.graph, output_root=args.output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
