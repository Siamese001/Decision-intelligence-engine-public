#!/usr/bin/env python3
"""Run 30 End-to-End Tests examining Routing and Pipeline execution.

Compares:
- Branch A: NEE Decision Intelligence (15 queries across reasoning types)
- Branch B: Knowledge Graph Evidence Utility (15 queries across bounded evidence types)

Ensures exact front-door routing, pipeline execution, L2 policy, zero provider
leakage on graph utility, immutable graph truth (0 mutations), and durable
audit verification under the sealed network-free evaluator.
"""

from __future__ import annotations

import argparse
import sys
import time
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

CASES: tuple[dict[str, Any], ...] = (
    # =========================================================================
    # PART 1: 15 DECISION INTELLIGENCE CASES (NEE_DECISION_INTELLIGENCE)
    # Pipeline characteristics:
    # - Complex reasoning / synthesis / gap / opportunity analysis
    # - l2_policy: required
    # - Sealed network-free evaluator provider if L2 reached (or pre-L2 coverage block)
    # - 0 graph mutations
    # =========================================================================
    {
        "case_id": "di-01-nea-sherpa-relationship",
        "category": "decision_intelligence",
        "query_type": "relationship_explanation",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Explain how NEA and Project Beacon are connected and cite governed evidence.",
    },
    {
        "case_id": "di-02-pdlc-hub-sherpa-relationship",
        "category": "decision_intelligence",
        "query_type": "relationship_explanation",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Explain governed connections among AI-enabled PDLC, the AI Hub, and Project Beacon delivery, including dependencies and evidence.",
    },
    {
        "case_id": "di-03-reverse-edge-handoff-direction",
        "category": "decision_intelligence",
        "query_type": "relationship_explanation",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Explain whether governed NEE graph relationships support the proposed handoff direction.",
    },
    {
        "case_id": "di-04-sherpa-pdlc-relationship",
        "category": "decision_intelligence",
        "query_type": "relationship_explanation",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Explain the governed relationship between Project Beacon and AI-enabled SDLC / PDLC.",
    },
    {
        "case_id": "di-05-ai-hub-workstreams-relationship",
        "category": "decision_intelligence",
        "query_type": "relationship_explanation",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Explain the governed relationship between the AI Hub and delivery workstreams.",
    },
    {
        "case_id": "di-06-cross-workstream-network",
        "category": "decision_intelligence",
        "query_type": "cross_workstream_synthesis",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "How do Project Beacon, the AI Hub, partner dependencies, risks, and opportunities connect across workstreams?",
    },
    {
        "case_id": "di-07-compare-recommend-options",
        "category": "decision_intelligence",
        "query_type": "cross_workstream_synthesis",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Compare NEE evidence across options and recommend which to choose.",
    },
    {
        "case_id": "di-08-sherpa-gaps-risks-conflicts",
        "category": "decision_intelligence",
        "query_type": "gap_risk_inconsistency",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Identify governed Project Beacon gaps, risks, conflicts, and missing evidence.",
    },
    {
        "case_id": "di-09-sparse-evidence-risk-assessment",
        "category": "decision_intelligence",
        "query_type": "gap_risk_inconsistency",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Assess governed Project Beacon gaps across sparse current-state evidence and candidate patterns.",
    },
    {
        "case_id": "di-10-cross-source-diagnostic",
        "category": "decision_intelligence",
        "query_type": "gap_risk_inconsistency",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Diagnose the cross-source governed graph relationships and counterevidence.",
    },
    {
        "case_id": "di-11-nea-sherpa-opportunities-nba",
        "category": "decision_intelligence",
        "query_type": "opportunity_nba",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "What are the strongest connected opportunities across NEA and Project Beacon, which pain points and dependencies do they address, and what evidence supports them?",
    },
    {
        "case_id": "di-12-a2a-architecture-recommendation",
        "category": "decision_intelligence",
        "query_type": "opportunity_nba",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Recommend a future-state A2A architecture across governed business case, current state, patterns, risks, and gaps.",
    },
    {
        "case_id": "di-13-a2a-architecture-patterns",
        "category": "decision_intelligence",
        "query_type": "opportunity_nba",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Recommend a future-state A2A architecture across governed current state, patterns, risks, and gaps.",
    },
    {
        "case_id": "di-14-nea-sherpa-synthesis",
        "category": "decision_intelligence",
        "query_type": "cross_workstream_synthesis",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "Compare cross-workstream dependencies and patterns between NEA and Project Beacon.",
    },
    {
        "case_id": "di-15-sherpa-nba-opportunities",
        "category": "decision_intelligence",
        "query_type": "opportunity_nba",
        "expected_destination": "NEE_DECISION_INTELLIGENCE",
        "expected_l2_policy": "required",
        "question": "What next best actions and governed opportunities exist across Project Beacon delivery workstreams?",
    },

    # =========================================================================
    # PART 2: 15 KNOWLEDGE GRAPH EVIDENCE UTILITY CASES (GRAPH_EVIDENCE_UTILITY)
    # Pipeline characteristics:
    # - Exact lookups and project status snapshots
    # - l2_policy: forbidden / not_required
    # - Strict 0 external provider calls (governed evidence extraction)
    # - 0 graph mutations
    # =========================================================================
    {
        "case_id": "kg-01-exact-pdlc-records",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Which exact governed PDLC records are available?",
    },
    {
        "case_id": "kg-02-exact-sherpa-records",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Which exact governed Project Beacon records are available?",
    },
    {
        "case_id": "kg-03-record-lookup-123",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Show governed record record-123.",
    },
    {
        "case_id": "kg-04-record-lookup-bc7c",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Show governed record retrieval-record-bc7c51c11b5f5087e4a110fb6cf8.",
    },
    {
        "case_id": "kg-05-list-sherpa-citations",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "List the exact governed Project Beacon records and citations.",
    },
    {
        "case_id": "kg-06-record-lookup-456",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Show governed record record-456.",
    },
    {
        "case_id": "kg-07-list-nea-citations",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "List the exact governed NEA records and citations.",
    },
    {
        "case_id": "kg-08-record-lookup-789",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Show governed record record-789.",
    },
    {
        "case_id": "kg-09-status-current-edo-atlas",
        "category": "knowledge_graph",
        "query_type": "project_status_current",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "What is the current status of the EDO Atlas Pilot project?",
    },
    {
        "case_id": "kg-10-exact-ai-hub-records",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Which exact governed records exist for the AI Hub?",
    },
    {
        "case_id": "kg-11-exact-pdlc-citations",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "List the exact governed citations for PDLC delivery.",
    },
    {
        "case_id": "kg-12-exact-edo-atlas-records",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Which exact governed records exist for the EDO Atlas Pilot?",
    },
    {
        "case_id": "kg-13-status-history-edo-atlas",
        "category": "knowledge_graph",
        "query_type": "project_status_history",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Show the status history of the EDO Atlas Pilot project since June 2026.",
    },
    {
        "case_id": "kg-14-exact-sherpa-citations",
        "category": "knowledge_graph",
        "query_type": "exact_lookup",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "Which exact governed citations exist for Project Beacon delivery?",
    },
    {
        "case_id": "kg-15-status-current-edo-program",
        "category": "knowledge_graph",
        "query_type": "project_status_current",
        "expected_destination": "GRAPH_EVIDENCE_UTILITY",
        "expected_l2_policy": "forbidden",
        "question": "What is the current project status for the EDO program?",
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
    required = result.get("required_stages")
    if isinstance(required, list) and required:
        stages = tuple(required)
    else:
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
    case: dict[str, Any],
    result: dict[str, Any],
    output_dir: Path,
    runtime_root: Path,
    provider: SealedEvalProviderFactory,
    duration_s: float,
) -> dict[str, Any]:
    run_dir = runtime_root / "runs" / str(result["run_id"])
    plan = read_json(output_dir / "query_plan.json")
    destination = read_json(output_dir / "query_destination_decision.json")
    retrieval = read_json(output_dir / "retrieval_evidence_report.json")
    l2_path = run_dir / "l2_execution.json"
    l2 = read_json(l2_path) if l2_path.is_file() else None
    l2_disposition = l2.get("l2_disposition") if l2 else None
    l2_reached = l2 is not None and l2_disposition != "SKIPPED"
    audit = _audit(runtime_root, result, l2_reached=l2_reached)
    provider_calls = int(
        l2.get("total_l2_provider_call_count", 0) if l2 is not None else 0
    )
    terminal = result.get("terminal_outcome")
    expected_dest = case["expected_destination"]
    actual_dest = destination.get("destination")
    route_aligned = actual_dest == expected_dest
    graph_unchanged = int(result.get("graph_mutation_count", 0)) == 0

    is_di = case["category"] == "decision_intelligence"
    if is_di:
        policy_aligned = plan.get("l2_policy") == "required"
        fail_closed_block = (
            result.get("status") == "BLOCKED"
            and (terminal == "RETRIEVAL_COVERAGE_INSUFFICIENT" or terminal is None)
            and provider_calls == 0
        )
        execution_aligned = audit.get("status") == "PASS" and (
            fail_closed_block
            or (
                result.get("status") in {"COMPLETED", "BLOCKED"}
                and (l2 is None or l2_disposition in {"PRIMARY", "REPAIRED", "ESCALATED", "SKIPPED"})
            )
        )
    else:
        # Knowledge Graph Evidence Utility: strictly 0 provider calls, provider-free extraction
        policy_aligned = plan.get("l2_policy") in {"not_required", "forbidden"}
        provider_free = provider_calls == 0
        execution_aligned = audit.get("status") == "PASS" and provider_free and policy_aligned

    status = (
        "PASS"
        if route_aligned and graph_unchanged and policy_aligned and execution_aligned
        else "FAIL"
    )

    # ── Delivery outcome classification ─────────────────────────────
    answer_present = bool(result.get("answer"))
    if answer_present and result.get("status") == "COMPLETED":
        delivery_outcome = "DELIVERED"
    elif fail_closed_block if is_di else False:
        delivery_outcome = "BLOCKED_SAFETY"
    elif result.get("status") == "BLOCKED" and not answer_present:
        delivery_outcome = "BLOCKED_POLICY"
    else:
        delivery_outcome = "FAILED"

    # ── X-gate ledger status extraction ─────────────────────────────
    gates: dict[str, str | None] = {"x1": None, "x2": None, "x3": None, "l4": None}
    x_gates_path = output_dir / "x_gates.json"
    if x_gates_path.is_file():
        x_gates_data = read_json(x_gates_path)
        for gate_key in ("x1", "x2", "x3", "l4"):
            gate_entry = x_gates_data.get(gate_key)
            if isinstance(gate_entry, dict):
                gates[gate_key] = gate_entry.get("status") or gate_entry.get("outcome")
    else:
        # Fall back to learning events in the ledger
        ledger_dir = run_dir / "learning_events"
        if ledger_dir.is_dir():
            for event_file in sorted(ledger_dir.glob("*.json")):
                try:
                    event = read_json(event_file)
                except Exception:
                    continue
                stage = event.get("stage")
                if stage in ("X1", "X2", "X3", "L4"):
                    gate_key = stage.lower()
                    event_status = event.get("status") or event.get("gate_status")
                    if event_status and gates[gate_key] is None:
                        gates[gate_key] = event_status

    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "question": case["question"],
        "query_type": case["query_type"],
        "duration_seconds": round(duration_s, 2),
        "run_id": result["run_id"],
        "trace_id": result["trace_id"],
        "expected": {
            "destination": expected_dest,
            "l2_policy": case["expected_l2_policy"],
            "max_provider_calls": 999 if is_di else 0,
            "graph_mutation_count": 0,
        },
        "actual": {
            "status": result.get("status"),
            "destination": actual_dest,
            "routed_query_type": destination.get("query_type"),
            "l2_policy": plan.get("l2_policy"),
            "l2_reached": l2_reached,
            "l2_disposition": l2_disposition,
            "provider_failure_code": l2.get("provider_failure_code") if l2 else None,
            "provider_call_count": provider_calls,
            "answer_present": answer_present,
            "terminal_outcome": terminal,
            "graph_mutation_count": result.get("graph_mutation_count", 0),
            "retrieval_mode": plan.get("retrieval_mode"),
            "allowed_lanes": plan.get("allowed_lanes", []),
            "required_material_record_ids_by_lane": (
                l2.get("required_material_record_ids_by_lane")
                if l2
                else retrieval.get("required_material_record_ids_by_lane", {})
            ),
        },
        "alignment": {
            "overall": status,
            "delivery_outcome": delivery_outcome,
            "route": "PASS" if route_aligned else "FAIL",
            "policy": "PASS" if policy_aligned else "FAIL",
            "execution": "PASS" if execution_aligned else "FAIL",
            "graph_mutation": "PASS" if graph_unchanged else "FAIL",
            "audit_status": audit.get("status"),
            "audit_digest": audit.get("event_chain_digest"),
            "audit_stage_count": audit.get("event_count"),
        },
        "gates": gates,
        "artifacts": {
            "query_output_dir": str(output_dir.resolve()),
            "runtime_run_dir": str(run_dir.resolve()),
            "query_destination_decision": str(
                (output_dir / "query_destination_decision.json").resolve()
            ),
            "query_plan": str((output_dir / "query_plan.json").resolve()),
            "retrieval_evidence_report": str(
                (output_dir / "retrieval_evidence_report.json").resolve()
            ),
            "l2_execution": str(l2_path.resolve()) if l2 else None,
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# End-to-End Pipeline Evaluation: Decision Intelligence vs Knowledge Graph",
        "",
        f"**Overall Status**: `{report['status']}`",
        f"**Generated At**: `{report['generated_at']}`",
        "",
        "### Pipeline Branch Comparison",
        "",
        "| Branch | Category | Cases | Delivered | Safety Block | Route Pass | Policy Pass | Execution Pass | Calls | Mutations |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cat_key, cat_stats in report["categories"].items():
        lines.append(
            f"| {cat_stats['display_name']} | `{cat_key}` | {cat_stats['case_count']} | "
            f"{cat_stats['delivery_pass_count']}/{cat_stats['case_count']} | "
            f"{cat_stats['safety_block_count']}/{cat_stats['case_count']} | "
            f"{cat_stats['route_pass_count']}/{cat_stats['case_count']} | "
            f"{cat_stats['policy_pass_count']}/{cat_stats['case_count']} | "
            f"{cat_stats['execution_pass_count']}/{cat_stats['case_count']} | "
            f"{cat_stats['provider_call_count']} | {cat_stats['graph_mutation_count']} |"
        )

    lines.extend(
        [
            "",
            "### Detailed Case Execution Table (30 Tests)",
            "",
            "| Case ID | Branch | Destination | Query Type | L2 Policy | Delivery | X1 | X2 | X3 | L4 | Calls | Alignment |",
            "|---|---|---|---|---|---|---|---|---|---|---:|---|",
        ]
    )
    for case in report["cases"]:
        actual = case["actual"]
        gates = case.get("gates", {})
        lines.append(
            "| {case_id} | {cat} | {destination} | {query_type} | {l2_pol} | {delivery} | {x1} | {x2} | {x3} | {l4} | {calls} | **{align}** |".format(
                case_id=case["case_id"],
                cat="DI" if case["category"] == "decision_intelligence" else "KG",
                destination=actual["destination"],
                query_type=actual["routed_query_type"] or case["query_type"],
                l2_pol=actual["l2_policy"],
                delivery=case["alignment"].get("delivery_outcome", "N/A"),
                x1=gates.get("x1") or "—",
                x2=gates.get("x2") or "—",
                x3=gates.get("x3") or "—",
                l4=gates.get("l4") or "—",
                calls=actual["provider_call_count"],
                align=case["alignment"]["overall"],
            )
        )

    lines.extend(
        [
            "",
            "### Delivery Outcomes",
            "",
            f"- **Delivered**: `{report['aggregate']['delivery_pass_count']}` / `{report['aggregate']['case_count']}`",
            f"- **Safety Blocked**: `{report['aggregate']['safety_block_count']}`",
            f"- **Policy Blocked**: `{report['aggregate']['policy_block_count']}`",
            f"- **Failed**: `{report['aggregate']['failed_count']}`",
            "",
            "### Native Runtime & Retrieval Evidence",
            "",
            f"- **Runtime root**: `{report['runtime']['runtime_root']}`",
            f"- **Active generation**: `{report['runtime']['generation_id']}`",
            f"- **Embedding backend**: `{report['runtime']['embedding_backend']}`",
            f"- **Model ID & Revision**: `{report['runtime']['model_id']} ({report['runtime']['model_revision']})`",
            f"- **Graph SHA-256**: `{report['runtime']['graph_sha256']}`",
            f"- **Sidecar SHA-256**: `{report['runtime']['sidecar_sha256']}`",
            f"- **Total Provider Calls**: `{report['aggregate']['provider_call_count']}` (0 on Knowledge Graph utility)",
            f"- **Total Graph Mutations**: `{report['aggregate']['graph_mutation_count']}` (strictly 0 across all runs)",
            f"- **Total Duration**: `{report['aggregate']['total_duration_seconds']}s`",
            "",
        ]
    )
    return "\n".join(lines)


def run(*, runtime_root: Path, graph: Path, output_root: Path) -> Path:
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        import shutil
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    backend: EmbeddingBackend = BgeM3TestBackend()
    provider = SealedEvalProviderFactory()
    batch_run_ts = int(time.time())
    engine = QueryEngine(
        runtime_root,
        graph,
        backend,
        provider_factory=provider,
    )

    cases: list[dict[str, Any]] = []
    total_t0 = time.time()
    for idx, case in enumerate(CASES, 1):
        query_output = output_root / case["case_id"]
        run_user_id = f"e2e30-{batch_run_ts}-{case['case_id']}"
        print(f"[{idx:02d}/30] Running {case['case_id']} ({case['category']})...", flush=True)
        t0 = time.time()
        result = engine.ask(
            case["question"],
            query_type=case["query_type"],
            output_dir=query_output,
            user_id=run_user_id,
            classification="internal",
            audience="internal_consulting",
            provider_egress_allowed=True,
        )
        t1 = time.time()
        report_item = _case_report(
            case=case,
            result=result,
            output_dir=query_output,
            runtime_root=runtime_root,
            provider=provider,
            duration_s=t1 - t0,
        )
        cases.append(report_item)
        print(
            f"       -> {report_item['alignment'].get('delivery_outcome', 'N/A')} | "
            f"Dest: {report_item['actual']['destination']} | "
            f"Alignment: {report_item['alignment']['overall']} ({t1 - t0:.2f}s)",
            flush=True,
        )

    total_duration = round(time.time() - total_t0, 2)
    manager = ReleaseManager(runtime_root, graph)
    active = manager.active_pair()
    active_graph = manager._resolve(active["graph_path"])

    categories: dict[str, Any] = {}
    for cat_key, display in [
        ("decision_intelligence", "Decision Intelligence"),
        ("knowledge_graph", "Knowledge Graph"),
    ]:
        cat_cases = [c for c in cases if c["category"] == cat_key]
        categories[cat_key] = {
            "display_name": display,
            "case_count": len(cat_cases),
            "delivery_pass_count": sum(c["alignment"].get("delivery_outcome") == "DELIVERED" for c in cat_cases),
            "safety_block_count": sum(c["alignment"].get("delivery_outcome") == "BLOCKED_SAFETY" for c in cat_cases),
            "route_pass_count": sum(c["alignment"]["route"] == "PASS" for c in cat_cases),
            "policy_pass_count": sum(c["alignment"]["policy"] == "PASS" for c in cat_cases),
            "execution_pass_count": sum(c["alignment"]["execution"] == "PASS" for c in cat_cases),
            "overall_pass_count": sum(c["alignment"]["overall"] == "PASS" for c in cat_cases),
            "provider_call_count": sum(c["actual"]["provider_call_count"] for c in cat_cases),
            "graph_mutation_count": sum(c["actual"]["graph_mutation_count"] for c in cat_cases),
        }

    report: dict[str, Any] = {
        "schema_version": "2.0.0",
        "artifact_id": "pipeline-routing-e2e-30-report",
        "status": "PASS" if all(case["alignment"]["overall"] == "PASS" for case in cases) else "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": {
            "case_count": len(cases),
            "decision_intelligence_case_count": len([c for c in cases if c["category"] == "decision_intelligence"]),
            "knowledge_graph_case_count": len([c for c in cases if c["category"] == "knowledge_graph"]),
            "provider_posture": "sealed-eval-fake-provider",
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
            "sidecar_sha256": active.get("sidecar_sha256"),
            "production_qualified": active.get("production_qualified"),
        },
        "aggregate": {
            "case_count": len(cases),
            "overall_pass_count": sum(case["alignment"]["overall"] == "PASS" for case in cases),
            "delivery_pass_count": sum(
                case["alignment"].get("delivery_outcome") == "DELIVERED" for case in cases
            ),
            "safety_block_count": sum(
                case["alignment"].get("delivery_outcome") == "BLOCKED_SAFETY" for case in cases
            ),
            "policy_block_count": sum(
                case["alignment"].get("delivery_outcome") == "BLOCKED_POLICY" for case in cases
            ),
            "failed_count": sum(
                case["alignment"].get("delivery_outcome") == "FAILED" for case in cases
            ),
            "route_alignment_pass_count": sum(
                case["alignment"]["route"] == "PASS" for case in cases
            ),
            "policy_alignment_pass_count": sum(
                case["alignment"]["policy"] == "PASS" for case in cases
            ),
            "execution_alignment_pass_count": sum(
                case["alignment"]["execution"] == "PASS" for case in cases
            ),
            "provider_call_count": sum(
                case["actual"]["provider_call_count"] for case in cases
            ),
            "graph_mutation_count": sum(
                case["actual"]["graph_mutation_count"] for case in cases
            ),
            "total_duration_seconds": total_duration,
        },
        "categories": categories,
        "cases": cases,
    }

    json_path = output_root / "batch_report.json"
    write_json(json_path, report)
    md_path = output_root / "batch_report.md"
    md_path.write_text(_markdown(report), encoding="utf-8")
    return json_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT / ".local" / "main" / "runtime-bge-m3-sherpa-strategic-20260729",
    )
    parser.add_argument(
        "--graph",
        type=Path,
        default=(
            ROOT
            / ".local"
            / "main"
            / "runtime-bge-m3-sherpa-strategic-20260729"
            / "generations"
            / "generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3"
            / "graph.sqlite"
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / ".local" / "e2e-30-routing-and-pipeline",
    )
    args = parser.parse_args()
    result_path = run(
        runtime_root=args.runtime_root,
        graph=args.graph,
        output_root=args.output_root,
    )
    print(f"\nExecution complete. Batch report written to: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
