"""
End-to-End Multi-Pipeline Evaluation Characterization Suite.

Validates the full integrated 2026+ agentic evaluation pyramid:
  Tier 1: Action Trajectory & Tool Call Fidelity
  Tier 2: Dialectical Judge & Agreement Calibration
  Tier 3: Hermetic Sandboxing & Environmental State Delta Verification
  Tier 4: Canary Contamination & Adversarial Injection Defense
  Tier 5: Pareto Frontier Economics & Regression Auditing
"""

from __future__ import annotations

import pytest

from dual_pipeline.evals.adversarial import (
    CanaryEngine,
    PromptInjectionDefenseTester,
)
from dual_pipeline.evals.calibration import (
    JudgeCalibrationRunner,
    PositionSwapDebiaser,
)
from dual_pipeline.evals.pareto import (
    ParetoFrontierAnalyzer,
    ParetoPoint,
)
from dual_pipeline.evals.sandbox import (
    ChaosInjector,
    SandboxEnvironment,
    StateDeltaAuditor,
)
from dual_pipeline.evals.trajectory import (
    ToolSchemaValidator,
    TrajectoryEvaluator,
    TrajectoryStep,
    TrajectoryTrace,
)


def test_e2e_integrated_pipeline_evaluation():
    """Execute full 5-tier evaluation stack simulating an end-to-end agentic workflow."""
    # ── Tier 1: Trajectory Tracing & Tool Schema Adherence ──────────────────
    validator = ToolSchemaValidator({
        "extract_entities": {
            "type": "object",
            "properties": {"transcript_id": {"type": "string"}},
            "required": ["transcript_id"],
            "additionalProperties": False,
        },
        "commit_graph": {
            "type": "object",
            "properties": {"node_count": {"type": "integer"}},
            "required": ["node_count"],
            "additionalProperties": False,
        },
    })
    traj_evaluator = TrajectoryEvaluator(schema_validator=validator)

    trace = TrajectoryTrace(
        trace_id="e2e-workflow-trace-01",
        goal="Ingest transcript and commit graph entities",
        pipeline_id="pipeline_b_to_c",
        steps=[
            TrajectoryStep(
                1, "agent:extractor", "tool_call", "extract_entities",
                {"transcript_id": "tx:apex_trans-09-10"}, status="SUCCESS"
            ),
            TrajectoryStep(
                2, "agent:graph_writer", "tool_call", "commit_graph",
                {"node_count": 5}, status="SUCCESS"
            ),
            TrajectoryStep(
                3, "agent:orchestrator", "final_response", None, {}, status="SUCCESS"
            ),
        ],
        optimal_step_count=3,
    )
    scorecard = traj_evaluator.evaluate_trace(trace)
    assert scorecard.passed
    assert scorecard.efficiency_ratio == 1.0
    assert scorecard.schema_compliance_rate == 1.0
    assert scorecard.loop_count == 0

    # ── Tier 2: Calibrated Dialectical Judge & Position-Swap ────────────────
    calib_runner = JudgeCalibrationRunner(min_kappa=0.82)
    golden_set = [
        {"id": f"gt-{i}", "ground_truth_verdict": "PASS" if i < 6 else "FAIL"}
        for i in range(12)
    ]
    report = calib_runner.run_calibration(
        golden_set,
        lambda case: (case["ground_truth_verdict"], 0.94),
        evaluator_name="DecomposedMultiJudgeE2E",
    )
    assert report.passed
    assert report.cohens_kappa == 1.0

    debiaser = PositionSwapDebiaser()
    swap_res = debiaser.evaluate_pair(
        {"id": "doc_v1"}, {"id": "doc_v2"},
        lambda a, b: (a["id"], 0.90) if a["id"] == "doc_v1" else ("doc_v1", 0.90)
    )
    assert swap_res.is_consistent
    assert not swap_res.order_bias_detected

    # ── Tier 3: Sandboxed Environmental State Delta & Chaos Fault Injection ──
    with SandboxEnvironment() as sandbox_cfg:
        auditor = StateDeltaAuditor(sandbox_cfg.graph_path)
        base_snap = auditor.capture_snapshot()

        # Simulate agent writing nodes into sandboxed DB
        import sqlite3
        conn = sqlite3.connect(str(sandbox_cfg.graph_path))
        cur = conn.cursor()
        for i in range(5):
            cur.execute(
                "INSERT INTO node (node_id, node_type, name, created_at) VALUES (?, ?, ?, ?)",
                (f"node:e2e_{i}", "system", f"Substation {i}", "2026-09-13T00:00:00Z"),
            )
        conn.commit()
        conn.close()

        delta_report = auditor.compute_delta(base_snap)
        assert not delta_report.is_idempotent_noop
        assert delta_report.hash_changed
        assert delta_report.table_deltas["node"] == 5

        # Test chaos resilience
        injector = ChaosInjector()
        resilient_fn = injector.wrap_callable(lambda: "success", failure_mode="timeout", trigger_count=1)
        with pytest.raises(TimeoutError):
            resilient_fn()
        # Second call recovers
        assert resilient_fn() == "success"

    # ── Tier 4: Canary Contamination & Adversarial Injections ───────────────
    canary_engine = CanaryEngine()
    token = canary_engine.generate_canary("e2e_privacy_check")
    clean_output = "Public summary of operational baseline across APEX_TRANS operating companies."
    canary_engine.assert_no_leakage(clean_output, token)

    injection_tester = PromptInjectionDefenseTester()
    v_c13 = injection_tester.evaluate_c13_defense(clean_output)
    v_c19 = injection_tester.evaluate_c19_firewall(clean_output)
    assert v_c13.defense_successful
    assert v_c19.defense_successful

    # ── Tier 5: Multi-Objective Pareto Frontier Analysis ────────────────────
    pareto_analyzer = ParetoFrontierAnalyzer()
    points = [
        ParetoPoint("tier_fast", 88.0, 800.0, 30.0, "none"),
        ParetoPoint("tier_high_reasoning", 98.5, 4500.0, 220.0, "high"),
    ]
    frontier = pareto_analyzer.compute_frontier(points)
    assert all(p.is_pareto_optimal for p in frontier)
