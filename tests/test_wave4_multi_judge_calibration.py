"""
Acceptance test suite for Wave 4: Multi-Micro-Judge Calibration & Golden Dataset Evaluation.
Target Configuration: OpenAI gpt-5.6-luna (reasoning_effort="high").
"""

import json

from dual_pipeline.llm_judge import (
    CrossArtifactReconciler,
    DecomposedMultiJudge,
)
from evals.evaluate_multi_judge_calibration import (
    MultiJudgeCalibrationEvaluator,
    build_golden_benchmark_dataset,
    compute_cohens_kappa,
)


def test_cohens_kappa_perfect_and_partial():
    """Verify Cohen's Kappa computation on perfect, partial, and degraded agreement."""
    # Perfect agreement -> kappa = 1.0
    gt = ["PASS", "PASS", "FAIL", "FAIL", "PASS", "FAIL"]
    pred_perfect = ["PASS", "PASS", "FAIL", "FAIL", "PASS", "FAIL"]
    assert compute_cohens_kappa(gt, pred_perfect) == 1.0

    # 1 disagreement out of 6 -> kappa between 0.6 and 0.8
    pred_minor = ["PASS", "PASS", "FAIL", "FAIL", "PASS", "PASS"]
    k_minor = compute_cohens_kappa(gt, pred_minor)
    assert 0.60 <= k_minor < 1.0

    # Complete disagreement -> negative kappa
    pred_inverse = ["FAIL", "FAIL", "PASS", "PASS", "FAIL", "PASS"]
    k_inv = compute_cohens_kappa(gt, pred_inverse)
    assert k_inv < 0.0


def test_golden_dataset_structure_and_invariants():
    """Verify golden benchmark dataset integrity, balance, and metadata."""
    dataset = build_golden_benchmark_dataset()
    assert len(dataset) >= 8
    pos_cases = [c for c in dataset if c["ground_truth_verdict"] == "PASS"]
    neg_cases = [c for c in dataset if c["ground_truth_verdict"] == "FAIL"]

    assert len(pos_cases) >= 2
    assert len(neg_cases) >= 5

    for case in dataset:
        assert "case_id" in case
        assert "f1" in case and len(case["f1"]) > 50
        assert "f2" in case and len(case["f2"]) > 50
        assert "f3" in case and isinstance(case["f3"], dict)
        assert "meta" in case and isinstance(case["meta"], dict)


def test_non_compensating_veto_gate():
    """Verify that a single micro-judge defect or reconciliation failure vetoes release unconditionally."""
    judge = DecomposedMultiJudge(model="gpt-5.6-luna", reasoning_effort="high", mock=True)

    f1_clean = (
        "# APEX_TRANS Insights Hub -- Verbatim Transcript\n\n"
        "**Date:** 09/10/2026\n"
        "[00:00:25] **Alex Rivera**: Verbatim dialogue verified.\n"
    )
    f2_clean = (
        "# Subject: Executive Recap | 09/10/2026\n\n"
        "**Date:** 09/10/2026\n\n"
        "## Next Steps & Action Items\n"
        "| # | Action Item | Owner | Target Date |\n"
        "|---|---|---|---|\n"
        "| 1 | Action Item | Alex Rivera | 09/11/2026 |\n"
    )
    meta = {"meeting_name": "NEET_Discovery", "date": "09/10/2026"}

    # Case A: F3 contains Rule C-19 leak -> out3 score drops to 30, verdict FAIL
    f3_c19_leak = {
        "schema_version": "5.7",
        "title": "APEX_TRANS Graph",
        "canonical_topic": "INTERNAL_ADVISORY career leverage strategy",
        "graph_mutations": {"entity_upserts": []},
    }
    res_a = judge.evaluate_parallel(
        output_1_text=f1_clean,
        output_2_text=f2_clean,
        output_3_text=json.dumps(f3_c19_leak),
        raw_transcript=f1_clean,
        metadata=meta,
    )
    assert res_a["status"] == "FAIL"
    assert res_a["output_1_evaluation"]["composite_score"] >= 80  # out1 passed
    assert res_a["output_2_evaluation"]["composite_score"] >= 80  # out2 passed
    assert res_a["output_3_evaluation"]["verdict"] == "FAIL"      # out3 failed closed

    # Case B: Cross-artifact date mismatch (reconciliation failure)
    meta_bad_date = {"meeting_name": "NEET_Discovery", "date": "2026-09-10"}
    f3_clean = {"schema_version": "5.7", "title": "APEX_TRANS Graph", "graph_mutations": {"entity_upserts": []}}
    res_b = judge.evaluate_parallel(
        output_1_text=f1_clean,
        output_2_text=f2_clean,
        output_3_text=json.dumps(f3_clean),
        raw_transcript=f1_clean,
        metadata=meta_bad_date,
    )
    assert res_b["status"] == "FAIL"
    assert res_b["cross_artifact_reconciliation"]["passed"] is False


def test_cross_artifact_reconciler_invariants():
    """Verify sub-second deterministic CrossArtifactReconciler gate checks."""
    reconciler = CrossArtifactReconciler()

    f1 = "Output 1 text"
    f2 = "Output 2 text"
    f3 = {"schema_version": "5.7", "graph_mutations": {"entity_upserts": []}}
    meta_ok = {"date": "09/10/2026"}

    # 1. Valid inputs pass
    res_ok = reconciler.reconcile(f1, f2, f3, meta_ok)
    assert res_ok["passed"] is True
    assert res_ok["status"] == "PASS"

    # 2. Schema version != 5.7 fails
    res_bad_schema = reconciler.reconcile(f1, f2, {"schema_version": "4.2"}, meta_ok)
    assert res_bad_schema["passed"] is False
    assert any("schema_version must be '5.7'" in f for f in res_bad_schema["failures"])

    # 3. Mutation budget > 25 fails
    oversized_entities = [
        {"id": f"system:cluster_{i}", "type": "system", "mutation": "create"}
        for i in range(26)
    ]
    res_bad_budget = reconciler.reconcile(f1, f2, {"schema_version": "5.7", "graph_mutations": {"entity_upserts": oversized_entities}}, meta_ok)
    assert res_bad_budget["passed"] is False
    assert res_bad_budget["mutation_budget_cleared"] is False

    # 4. Disallowed entity prefix fails
    bad_prefix_entities = [{"id": "cloud:aws_s3", "type": "system", "mutation": "create"}]
    res_bad_prefix = reconciler.reconcile(f1, f2, {"schema_version": "5.7", "graph_mutations": {"entity_upserts": bad_prefix_entities}}, meta_ok)
    assert res_bad_prefix["passed"] is False
    assert any("does not use an allowed prefix" in f for f in res_bad_prefix["failures"])

    # 5. Rule C-19 marker in Output 02 fails
    res_c19 = reconciler.reconcile(f1, "Leaked $1.2M negotiation goal in recap.", f3, meta_ok)
    assert res_c19["passed"] is False
    assert any("RULE C-19 VIOLATION" in f for f in res_c19["failures"])


def test_decomposed_multi_judge_luna_high_propagation():
    """Verify DecomposedMultiJudge binds and propagates gpt-5.6-luna (high) to all micro-judges."""
    judge = DecomposedMultiJudge(model="gpt-5.6-luna", reasoning_effort="high", mock=True)

    assert judge.model == "gpt-5.6-luna"
    assert judge.reasoning_effort == "high"
    assert judge.judge_forensic.model == "gpt-5.6-luna"
    assert judge.judge_forensic.reasoning_effort == "high"
    assert judge.judge_recap.model == "gpt-5.6-luna"
    assert judge.judge_recap.reasoning_effort == "high"
    assert judge.judge_graph.model == "gpt-5.6-luna"
    assert judge.judge_graph.reasoning_effort == "high"

    # Evaluation returns Rubric v2.0
    f1 = "Alex Rivera: Verbatim transcript."
    f2 = "Executive Recap."
    f3 = {"schema_version": "5.7", "graph_mutations": {"entity_upserts": []}}
    res = judge.evaluate_parallel(
        output_1_text=f1,
        output_2_text=f2,
        output_3_text=json.dumps(f3),
        raw_transcript=f1,
        metadata={"date": "09/10/2026"},
    )
    assert res["rubric_version"] == "2.0"
    assert res["evaluator_model"] == "gpt-5.6-luna"
    assert res["reasoning_effort"] == "high"
    assert res["evaluation_mode"] == "DECOMPOSED_CONCURRENT"


def test_full_wave4_multi_judge_calibration_evaluator_luna_high():
    """Execute complete 5-gate calibration suite across golden dataset under gpt-5.6-luna (high)."""
    evaluator = MultiJudgeCalibrationEvaluator(
        target_model="gpt-5.6-luna",
        target_reasoning_effort="high",
        use_mock=True,
    )
    result = evaluator.run_all()

    assert result["status"] == "PASS"
    assert result["target_model"] == "gpt-5.6-luna"
    assert result["reasoning_effort"] == "high"

    # Verify all 5 non-compensating gates passed
    gates = result["gates"]
    assert gates["1. golden_dataset_conformance"] is True
    assert gates["2. cohen_kappa_calibration"] is True
    assert gates["3. non_compensating_veto_enforcement"] is True
    assert gates["4. rubric_v2_model_binding"] is True
    assert gates["5. rule_c19_leakage_veto"] is True

    # Check metrics
    metrics = result["metrics"]
    assert metrics["cohens_kappa"] >= 0.82
    assert metrics["accuracy"] == 1.0
    assert metrics["sensitivity"] == 1.0
    assert metrics["specificity"] == 1.0


def test_full_wave4_multi_judge_calibration_evaluator_luna_medium():
    """Execute complete 5-gate calibration suite across golden dataset under gpt-5.6-luna (medium)."""
    evaluator = MultiJudgeCalibrationEvaluator(
        target_model="gpt-5.6-luna",
        target_reasoning_effort="medium",
        use_mock=True,
    )
    result = evaluator.run_all()

    assert result["status"] == "PASS"
    assert result["target_model"] == "gpt-5.6-luna"
    assert result["reasoning_effort"] == "medium"

    # Verify all 5 non-compensating gates passed
    gates = result["gates"]
    assert gates["1. golden_dataset_conformance"] is True
    assert gates["2. cohen_kappa_calibration"] is True
    assert gates["3. non_compensating_veto_enforcement"] is True
    assert gates["4. rubric_v2_model_binding"] is True
    assert gates["5. rule_c19_leakage_veto"] is True

    # Check metrics
    metrics = result["metrics"]
    assert metrics["cohens_kappa"] >= 0.82
    assert metrics["accuracy"] == 1.0
    assert metrics["sensitivity"] == 1.0
    assert metrics["specificity"] == 1.0
