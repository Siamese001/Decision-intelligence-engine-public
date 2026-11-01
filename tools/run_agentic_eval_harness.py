#!/usr/bin/env python3
"""
Unified Agentic Pipeline Evaluation Harness CLI.

Executes the complete multi-tier 2026+ agentic evaluation pyramid:
  Tier 1: Action Trajectory & Schema Evaluation
  Tier 2: Dialectical Judge & Inter-Rater Calibration
  Tier 3: Sandboxed State Delta & Chaos Fault Resilience
  Tier 4: Canary Contamination & Adversarial Injection Defense
  Tier 5: Pareto Frontier Economics & Longitudinal Ledger Update

Produces sealed, hash-bound RFC-8259 evaluation receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from dual_pipeline.evals.adversarial import (
    CanaryEngine,
    PromptInjectionDefenseTester,
)
from dual_pipeline.evals.calibration import (
    JudgeCalibrationRunner,
)
from dual_pipeline.evals.pareto import (
    ParetoFrontierAnalyzer,
    ParetoPoint,
)
from dual_pipeline.evals.pipelines import (
    PipelineAEvaluator,
    PipelineBEvaluator,
    PipelineCEvaluator,
    PipelineDEvaluator,
)
from dual_pipeline.evals.rollup import ThreeLevelRollupEngine
from dual_pipeline.evals.sandbox import (
    ChaosInjector,
    SandboxEnvironment,
    StateDeltaAuditor,
)
from dual_pipeline.evals.strategic import ExecutiveStrategicEvaluator
from dual_pipeline.evals.trajectory import (
    ToolSchemaValidator,
    TrajectoryEvaluator,
    TrajectoryStep,
    TrajectoryTrace,
)


def run_agentic_evaluation_suite(
    output_receipt_path: Path = ROOT / "reports" / "executive_eval_harness_latest_receipt.json",
    update_ledger: bool = True,
    pipeline: str = "all",
    strategic: bool = True,
) -> Dict[str, Any]:
    """Execute all tiers of the agentic evaluation pyramid, pipeline evals, and strategic dimensions."""
    stages = [
        "Tier 1: Action Trajectory & Schema Evaluation",
        "Tier 2: Dialectical Judge & Agreement Calibration",
        "Tier 3: Sandboxed State Delta & Chaos Resilience",
        "Tier 4: Canary & Adversarial Injection Defense",
        "Tier 5: Pareto Frontier & Longitudinal Ledger",
    ]
    if pipeline in ("all", "a", "b", "c", "d"):
        stages.append(f"Pipelines A-D Suitability ({pipeline})")
    if strategic:
        stages.append("Tier 6: Strategic Alpha & Executive Exoskeleton ($1.2M Benchmark)")

    pbar = None
    if tqdm is not None and sys.stderr.isatty():
        pbar = tqdm(total=len(stages), desc="Agentic Eval Pyramid", unit="tier", file=sys.stderr)

    tier_results: Dict[str, Any] = {}
    all_passed = True

    # ── Tier 1: Action Trajectory & Schema Evaluation ───────────────────────
    if pbar:
        pbar.set_postfix_str(stages[0])
    schema_val = ToolSchemaValidator({
        "read_node": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"], "additionalProperties": False},
        "query_graph": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
    })
    traj_eval = TrajectoryEvaluator(schema_validator=schema_val)
    sample_trace = TrajectoryTrace(
        trace_id="eval-harness-live-trace",
        goal="Verify telemetry",
        pipeline_id="pipeline_c",
        steps=[
            TrajectoryStep(1, "agent:retrieval", "tool_call", "query_graph", {"query": "test"}, status="SUCCESS"),
            TrajectoryStep(2, "agent:retrieval", "tool_call", "read_node", {"node_id": "node:test"}, status="SUCCESS"),
            TrajectoryStep(3, "agent:retrieval", "final_response", None, {}, status="SUCCESS"),
        ],
        optimal_step_count=3,
    )
    scorecard = traj_eval.evaluate_trace(sample_trace)
    tier1_pass = scorecard.passed
    tier_results["tier_1_trajectory"] = scorecard.to_dict()
    all_passed = all_passed and tier1_pass
    if pbar:
        pbar.update(1)

    # ── Tier 2: Dialectical Judge & Agreement Calibration ───────────────────
    if pbar:
        pbar.set_postfix_str(stages[1])
    calib_runner = JudgeCalibrationRunner(min_kappa=0.82)
    from dual_pipeline.llm_judge import DialecticalJudge

    judge_inst = DialecticalJudge(margin_threshold=0.15)
    sample_golden = [
        {
            "id": f"case-{i}",
            "question": f"Strategic choice {i}",
            "options": [
                {"option_id": "opt_a", "label": "Option A", "confidence_score": 0.85 if i < 6 else 0.52, "raw_confidence": 0.85 if i < 6 else 0.52},
                {"option_id": "opt_b", "label": "Option B", "confidence_score": 0.40 if i < 6 else 0.48, "raw_confidence": 0.40 if i < 6 else 0.48},
            ],
            # If margin >= 0.15, autonomous resolution -> PASS; if margin < 0.15, HITL required -> FAIL
            "ground_truth_verdict": "PASS" if i < 6 else "FAIL",
        }
        for i in range(10)
    ]

    def _judge_eval_fn(case: Dict[str, Any]) -> Tuple[str, float]:
        res = judge_inst.evaluate_perspectives(
            decision_id=case["id"],
            question=case["question"],
            options=case["options"],
        )
        verdict = "PASS" if res.status == "AUTONOMOUS_RESOLUTION" else "FAIL"
        top_conf = float(res.top_option.get("confidence_score", 0.85)) if res.top_option else 0.5
        return verdict, top_conf

    calib_report = calib_runner.run_calibration(
        sample_golden,
        _judge_eval_fn,
        evaluator_name="DialecticalJudge",
    )
    tier2_pass = calib_report.passed
    tier_results["tier_2_calibration"] = calib_report.to_dict()
    all_passed = all_passed and tier2_pass
    if pbar:
        pbar.update(1)

    # ── Tier 3: Sandboxed State Delta & Chaos Resilience ───────────────────
    if pbar:
        pbar.set_postfix_str(stages[2])
    with SandboxEnvironment() as sandbox_cfg:
        auditor = StateDeltaAuditor(sandbox_cfg.graph_path)
        base_snap = auditor.capture_snapshot()
        delta_rep = auditor.compute_delta(base_snap)
        tier3_delta_pass = delta_rep.is_idempotent_noop

        injector = ChaosInjector()
        wrapped = injector.wrap_callable(lambda x: f"ok_{x}", failure_mode="rate_limit", trigger_count=1)
        try:
            wrapped("test")
        except Exception:
            pass
        recovered_res = wrapped("test")
        tier3_chaos_pass = (recovered_res == "ok_test") and (injector.metrics.recovered_calls == 1)

    tier3_pass = tier3_delta_pass and tier3_chaos_pass
    tier_results["tier_3_sandbox"] = {
        "delta_report": delta_rep.to_dict(),
        "chaos_recovered": tier3_chaos_pass,
        "passed": tier3_pass,
    }
    all_passed = all_passed and tier3_pass
    if pbar:
        pbar.update(1)

    # ── Tier 4: Canary & Adversarial Injection Defense ─────────────────────
    if pbar:
        pbar.set_postfix_str(stages[3])
    canary_eng = CanaryEngine()
    test_canary = canary_eng.generate_canary("ci_test")
    clean_check = canary_eng.scan_for_leakage("Clean public response text without token", test_canary)
    contaminated_check = canary_eng.scan_for_leakage(f"Accidental leak of {test_canary.token_str} in output", test_canary)
    # Canary defense passes if clean text has no leak AND contaminated text is caught
    canary_pass = (not clean_check[0].is_leaked) and contaminated_check[0].is_leaked

    injection_tester = PromptInjectionDefenseTester()
    c13_verdict = injection_tester.evaluate_sanitizer_c13_neutralization()
    c19_verdict = injection_tester.evaluate_sanitizer_c19_firewall()
    tier4_pass = canary_pass and c13_verdict.defense_successful and c19_verdict.defense_successful
    tier_results["tier_4_adversarial"] = {
        "canary_passed": canary_pass,
        "c13_passed": c13_verdict.defense_successful,
        "c19_passed": c19_verdict.defense_successful,
        "passed": tier4_pass,
    }
    all_passed = all_passed and tier4_pass
    if pbar:
        pbar.update(1)

    # ── Tier 5: Pareto Frontier & Longitudinal Ledger ──────────────────────
    if pbar:
        pbar.set_postfix_str(stages[4])
    pareto_analyzer = ParetoFrontierAnalyzer()
    pareto_points = [
        ParetoPoint("config_fast", quality_score=85.0, latency_ms=1200.0, cost_microcredits=50.0, reasoning_effort="none"),
        ParetoPoint("config_medium", quality_score=94.0, latency_ms=3500.0, cost_microcredits=180.0, reasoning_effort="medium"),
        ParetoPoint("config_high_reasoning", quality_score=99.0, latency_ms=8200.0, cost_microcredits=450.0, reasoning_effort="high"),
    ]
    frontier = pareto_analyzer.compute_frontier(pareto_points)
    tier5_pass = all(p.is_pareto_optimal for p in frontier)
    tier_results["tier_5_pareto"] = {
        "frontier_points": [p.to_dict() for p in frontier],
        "passed": tier5_pass,
    }
    all_passed = all_passed and tier5_pass
    # ── Specialized Pipelines A-D Suitability Evaluations ──────────────────
    pipeline_results: Dict[str, Any] = {}
    if pipeline in ("all", "a"):
        pipe_a = PipelineAEvaluator().run_evaluation()
        pipeline_results["pipeline_a"] = pipe_a.to_dict()
        all_passed = all_passed and pipe_a.passed

    if pipeline in ("all", "b"):
        pipe_b = PipelineBEvaluator().run_evaluation()
        pipeline_results["pipeline_b"] = pipe_b.to_dict()
        all_passed = all_passed and pipe_b.passed

    if pipeline in ("all", "c"):
        pipe_c = PipelineCEvaluator().run_evaluation()
        pipeline_results["pipeline_c"] = pipe_c.to_dict()
        all_passed = all_passed and pipe_c.passed

    if pipeline in ("all", "d"):
        pipe_d = PipelineDEvaluator().run_evaluation()
        pipeline_results["pipeline_d"] = pipe_d.to_dict()
        all_passed = all_passed and pipe_d.passed

    # ── Tier 6: Strategic Alpha & Executive Exoskeleton ($1.2M Benchmark) ──
    strategic_results: Dict[str, Any] = {}
    if strategic:
        if pbar:
            pbar.set_postfix_str("Tier 6: Strategic Alpha ($1.2M Benchmark)")
        strat_eval = ExecutiveStrategicEvaluator()
        strat_scorecard = strat_eval.run_full_evaluation()
        strategic_results = strat_scorecard.to_dict()
        all_passed = all_passed and strat_scorecard.passed
        if pbar:
            pbar.update(1)

    if pbar:
        if pipeline in ("all", "a", "b", "c", "d"):
            pbar.update(1)
        pbar.close()

    # Three-Level (Micro -> Meso -> Macro) Rollup Evaluation
    rollup_report = ThreeLevelRollupEngine.evaluate_rollup(
        tier_results=tier_results,
        pipeline_results=pipeline_results,
        strategic_results=strategic_results,
    )
    terminal_verdict_bool = all_passed and rollup_report.overall_pass

    # Generate receipt payload
    receipt_id = f"eval_receipt_{int(time.time())}"
    timestamp_iso = datetime.now(timezone.utc).isoformat()
    receipt_data = {
        "receipt_id": receipt_id,
        "evaluated_at": timestamp_iso,
        "terminal_verdict": "PASS" if terminal_verdict_bool else "FAIL",
        "tiers_executed": len(stages),
        "three_level_rollup": rollup_report.to_dict(),
        "certification_status": rollup_report.certification_status,
        "hierarchical_lock_passed": rollup_report.hierarchical_lock_passed,
        "results": tier_results,
        "pipeline_results": pipeline_results,
        "strategic_results": strategic_results,
        "executive_maturity_level": strategic_results.get("maturity_level", 1.0) if strategic else None,
        "executive_verdict": strategic_results.get("verdict", "N/A") if strategic else None,
    }

    # Canonical RFC-8259 serialization & SHA-256 digest
    serialized_bytes = json.dumps(receipt_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt_digest = hashlib.sha256(serialized_bytes).hexdigest()
    receipt_data["receipt_digest"] = receipt_digest

    output_receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt_data, f, indent=2)

    # Update longitudinal ledger if requested
    if update_ledger:
        ledger_path = ROOT / "reports" / "longitudinal_eval_ledger.json"
        if ledger_path.exists():
            try:
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                new_entry = {
                    "run_id": receipt_id,
                    "timestamp": timestamp_iso,
                    "pipeline_version": "2026.1.0",
                    "rubric_version": "2.0.0",
                    "benchmark_suite": "Executive_Strategic_Pyramid_E2E",
                    "model_id": "unified_governed_tiers",
                    "reasoning_effort": "multi_tier",
                    "metrics": {
                        "accuracy": calib_report.accuracy,
                        "composite_score": scorecard.composite_score,
                        "cohens_kappa": calib_report.cohens_kappa,
                        "strategic_alpha_score": strategic_results.get("overall_score", 0.0),
                        "executive_maturity_level": strategic_results.get("maturity_level", 1.0),
                        "certification_status": rollup_report.certification_status,
                    },
                    "gates": {
                        "trajectory": tier1_pass,
                        "calibration": tier2_pass,
                        "sandbox": tier3_pass,
                        "adversarial": tier4_pass,
                        "pareto": tier5_pass,
                        "pipelines_ad": all(v.get("passed", False) for v in pipeline_results.values()) if pipeline_results else True,
                        "strategic_alpha": strategic_results.get("passed", True) if strategic else True,
                        "three_level_rollup": rollup_report.overall_pass,
                    },
                    "git_commit": "executive-strategic-eval-v2",
                    "metadata": {"receipt_digest": receipt_digest},
                }
                ledger.setdefault("entries", []).append(new_entry)
                ledger["total_entries"] = len(ledger["entries"])
                ledger["last_updated"] = timestamp_iso
                ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
            except Exception as exc:
                sys.stderr.write(f"Warning: Failed to update longitudinal ledger: {exc}\n")

    return receipt_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified Agentic & Strategic Evaluation Harness")
    parser.add_argument("--output-receipt", type=Path, default=ROOT / "reports" / "executive_eval_harness_latest_receipt.json")
    parser.add_argument("--pipeline", choices=["none", "all", "a", "b", "c", "d"], default="all", help="Execute specialized pipeline suitability evaluations")
    parser.add_argument("--strategic", action=argparse.BooleanOptionalAction, default=True, help="Execute 10 Strategic Intelligence Dimensions ($1.2M Benchmark)")
    parser.add_argument("--no-ledger", action="store_true", help="Skip updating longitudinal ledger")
    args = parser.parse_args()

    res = run_agentic_evaluation_suite(
        output_receipt_path=args.output_receipt,
        update_ledger=not args.no_ledger,
        pipeline=args.pipeline,
        strategic=args.strategic,
    )
    verdict = res.get("terminal_verdict", "FAIL")
    rollup = res.get("three_level_rollup", {})
    l1 = rollup.get("level_1_micro", {})
    l2 = rollup.get("level_2_meso", {})
    l3 = rollup.get("level_3_macro", {})
    print("\n" + "=" * 80)
    print("THREE-LEVEL (L1-L3) EVALUATION ROLLUP & BUSINESS CASE VERDICT")
    print("=" * 80)
    print(f"Level 1 (Micro Foundational) : [{'PASS' if l1.get('passed') else 'FAIL'}] Score: {l1.get('score')}% | {l1.get('diagnostic')}")
    print(f"Level 2 (Meso Defense)       : [{'PASS' if l2.get('passed') else 'FAIL'}] Score: {l2.get('score')}% | C-19 Leaks: {l2.get('c19_career_firewall_leaks')} | {l2.get('diagnostic')}")
    print(f"Level 3 (Macro Strategic)    : [{'PASS' if l3.get('passed') else 'FAIL'}] Score: {l3.get('score')}% | Dims: {l3.get('dimensions_passed_count')}/{l3.get('total_dimensions')} | {l3.get('verdict')}")
    print("-" * 80)
    print(f"Hierarchical Lock Enforced   : {res.get('hierarchical_lock_passed')}")
    print(f"Terminal Certification       : {res.get('certification_status')}")
    print("=" * 80)
    print(f"Receipt Digest: {res.get('receipt_digest')}")
    print(f"Output File   : {args.output_receipt}")
    sys.exit(0 if verdict == "PASS" else 1)


