"""Construct-Validity & High-ROI Enhancement Regression Tests for Evaluation Surfaces.

Covers:
- Wave 1 (Construct-Validity Gap Fixes):
  - GAP-01: compute_groundedness rejects mixed valid + fabricated inline citations and prefix collisions.
  - GAP-02: DeliverableStrategicEvaluator enforces Rule 9 Anti-AI hygiene (em-dashes, buzzwords) and SBAI+ backing.
  - GAP-03: compute_copilot_scorecard penalizes groundedness and speakability on silent speak-turn omissions.
  - GAP-04: DialecticalAndHITLCalibrationEvaluator verifies the governed 20% (0.20) SSOT ambiguity margin boundary.
  - GAP-05: PipelineBEvaluator honors db_path for SQLite DLQ persistence; PipelineCEvaluator invokes PolicyGatedRetrievalEngine.search.
  - GAP-06: evaluate_inference_similarity fails closed when vectors/backend are missing unless allow_precomputed_similarity=True.
- Wave 2 (High-ROI Enhancements):
  - ENH-01: tools/audit_eval_construct_validity.py AST & runtime construct-validity auditor.
  - ENH-02: tools/calibrate_retrieval_thresholds.py empirical F1 / Youden's J threshold sweep.
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.evals.answer_eval_core.metrics import compute_groundedness
from dual_pipeline.evals.pipelines.pipeline_b_evaluator import PipelineBEvaluator
from dual_pipeline.evals.pipelines.pipeline_c_evaluator import PipelineCEvaluator
from dual_pipeline.evals.strategic.deliverable_evaluator import (
    DeliverableStrategicEvaluator,
)
from evals.copilot_scorecard import compute_copilot_scorecard
from evals.evaluate_dialectical_and_hitl_calibration import (
    DialecticalAndHITLCalibrationEvaluator,
)
from tools.audit_eval_construct_validity import (
    audit_repository,
    scan_source_for_violations,
)
from tools.audit_retrieval_gates import evaluate_inference_similarity
from tools.calibrate_retrieval_thresholds import (
    calibrate_thresholds,
    sweep_optimal_threshold,
)


class TestWave1ConstructValidityFixes:
    """Wave 1 construct-validity gap tests across Surfaces 1-5."""

    def test_gap01_compute_groundedness_rejects_mixed_fabricated_citations_and_prefix_collision(self) -> None:
        """Sentence with both a valid and fabricated ast-... citation must fail grounding."""
        rec_mixed = {
            "response_text": "Substation Alpha is online [ast-valid-1, ast-fabricated-99].",
            "cited_assertion_ids": ["ast-valid-1"],
        }
        assert compute_groundedness(rec_mixed) == 0.0

        # Two sentences: 1 fully grounded, 1 mixed -> score must be 0.5
        rec_partial = {
            "response_text": (
                "Substation Alpha is online [ast-valid-1]. "
                "Transformer Beta is overloaded [ast-valid-1, ast-fabricated-99]."
            ),
            "cited_assertion_ids": ["ast-valid-1"],
        }
        assert compute_groundedness(rec_partial) == 0.5

        # Prefix collision: citing 'rec-1' must not ground 'rec-10'
        rec_prefix = {
            "response_text": "Telemetry confirmed in rec-10.",
            "cited_assertion_ids": ["rec-1"],
        }
        assert compute_groundedness(rec_prefix) == 0.0

    def test_gap02_deliverable_evaluator_rejects_rule9_em_dash_and_buzzwords(
        self, tmp_path: Path
    ) -> None:
        """DeliverableStrategicEvaluator must fail Dim 9 when Output 02 contains em-dashes, buzzwords, or unbacked SBAI+."""
        evaluator = DeliverableStrategicEvaluator()
        p1 = tmp_path / "01_transcript.md"
        p2 = tmp_path / "02_recap.md"
        p3 = tmp_path / "03_signal.json"

        p1.write_text("Clean transcript.", encoding="utf-8")
        p3.write_text(json.dumps({"schema_version": "5.8", "meeting_id": "2026-10-30"}), encoding="utf-8")

        # 1. Inject em-dash and banned buzzword 'delve'
        p2.write_text(
            "Meeting Objective: We will delve into the $500,000 budget \u2014 and align on next steps.",
            encoding="utf-8",
        )
        sc_emdash = evaluator.evaluate_deliverables(p1, p2, p3)
        dim9_bad = sc_emdash.dimension_scores["dim_9_board_synthesis"]
        assert dim9_bad.passed is False
        assert dim9_bad.details["rule9_anti_ai_clean"] is False
        assert len(dim9_bad.details["rule9_violations"]) >= 2

        # 2. SBAI+ performance review without Situation/Behavior/Action/Impact backing
        p2.write_text(
            "Meeting Objective: Conduct SBAI+ performance review for $200,000 portfolio next steps.",
            encoding="utf-8",
        )
        sc_sbai_bad = evaluator.evaluate_deliverables(p1, p2, p3)
        assert sc_sbai_bad.dimension_scores["dim_9_board_synthesis"].passed is False
        assert sc_sbai_bad.dimension_scores["dim_9_board_synthesis"].details["sbai_competency_backed"] is False

        # 3. Clean executive recap passes Dim 9
        p2.write_text(
            "Meeting Objective: Align on $500,000 portfolio budget and actionable next steps.",
            encoding="utf-8",
        )
        sc_clean = evaluator.evaluate_deliverables(p1, p2, p3)
        assert sc_clean.dimension_scores["dim_9_board_synthesis"].passed is True
        assert sc_clean.dimension_scores["dim_9_board_synthesis"].score == 100.0

    def test_gap03_copilot_scorecard_penalizes_silent_omission_on_speak_cases(self) -> None:
        """Silent omission on an expected 'speak' case must penalize groundedness and speakability."""
        cases = [
            {"id": "c1", "expected_action": "speak", "expected_assertion_ids": ["ast-1"]},
            {"id": "c2", "expected_action": "silent"},
        ]
        results = [
            {"id": "c1", "status": "FAIL", "response": "", "cited_assertion_ids": []},
            {"id": "c2", "status": "PASS", "response": "", "cited_assertion_ids": []},
        ]
        sc = compute_copilot_scorecard(cases, results)
        assert sc.groundedness == 0.5
        assert sc.speakability == 0.5
        assert sc.relevance == 0.5
        assert sc.passed is False

    def test_gap04_dialectical_hitl_evaluator_enforces_20_percent_ssot_margin(self) -> None:
        """Gate 3 & Gate 4 must verify the 20% (0.20) SSOT ambiguity margin boundary."""
        evaluator = DialecticalAndHITLCalibrationEvaluator()
        g3_ok, g3_det = evaluator.evaluate_gate_3_ambiguity_margin_rule()
        assert g3_ok is True
        assert g3_det["margin_threshold"] == 0.20
        assert g3_det["ambiguous_margin"] == 0.20
        assert g3_det["ambiguous_case_surfaced"] is True

        g4_ok, g4_det = evaluator.evaluate_gate_4_prior_feedback_convergence()
        assert g4_ok is True
        assert g4_det["calibrated_margin"] > 0.20
        assert g4_det["autonomous_convergence_achieved"] is True

    def test_gap05_pipeline_b_and_c_evaluators_honor_sqlite_dlq_and_retrieval_engine(
        self, tmp_path: Path
    ) -> None:
        """PipelineBEvaluator must persist DLQ to SQLite when db_path is supplied; PipelineCEvaluator must invoke PolicyGatedRetrievalEngine."""
        pb = PipelineBEvaluator()
        dlq_res = pb.evaluate_dlq_quarantine_reliability(db_path=tmp_path)
        assert dlq_res["passed"] is True
        assert dlq_res["persisted_to_sqlite"] is True
        assert (tmp_path / "evidence.db").is_file()

        pc = PipelineCEvaluator()
        law6_res = pc.evaluate_law6_quarantine_enforcement()
        assert law6_res["passed"] is True
        assert law6_res["engine_search_quarantine_enforced"] is True

        law2_res = pc.evaluate_law2_namespace_isolation()
        assert law2_res["passed"] is True
        assert law2_res["engine_stealth_namespace_isolated"] is True

    def test_gap06_audit_retrieval_gates_fails_closed_without_vectors_or_backend(self) -> None:
        """evaluate_inference_similarity must quarantine claims that only carry unverified bge_m3_similarity floats by default."""
        unverified_claims = [
            {"claim": "Self-reported high similarity without vectors", "bge_m3_similarity": 0.99, "source": "Slide 1"}
        ]
        approved_strict, quarantined_strict = evaluate_inference_similarity(unverified_claims, min_score=0.85)
        assert len(approved_strict) == 0
        assert len(quarantined_strict) == 1

        # With real vectors, it is approved
        verified_claims = [
            {
                "claim": "Vector-verified claim",
                "claim_vector": [0.9, 0.4, 0.1],
                "source_vector": [0.89, 0.41, 0.11],
                "source": "Slide 1",
            }
        ]
        approved_vec, quarantined_vec = evaluate_inference_similarity(verified_claims, min_score=0.85)
        assert len(approved_vec) == 1
        assert len(quarantined_vec) == 0


class TestWave2HighRoiEnhancements:
    """Wave 2 high-ROI evaluation enhancement tests (ENH-01, ENH-02, ENH-03)."""

    def test_enh01_audit_eval_construct_validity_passes_repo_and_catches_adversarial_ast(self) -> None:
        """audit_repository must report 0 violations on the remediated repo and flag injected AST tautologies/clamps."""
        report = audit_repository()
        assert report["status"] == "PASS"
        assert report["violation_count"] == 0
        assert report["files_scanned"] >= 35

        bad_source = (
            "def evaluate_fake_gate(x):\n"
            "    return True\n\n"
            "def compute_paired_bootstrap_diff(cand, base):\n"
            "    assert True\n"
            "    return max(0.0, cand - base)\n"
        )
        violations = scan_source_for_violations(bad_source, filename="synthetic_bad_eval.py")
        rule_ids = {v.rule_id for v in violations}
        assert "VACUOUS_EVALUATE_STUB" in rule_ids
        assert "TAUTOLOGICAL_ASSERT_TRUE" in rule_ids
        assert "CLAMPED_REGRESSION_DELTA" in rule_ids

    def test_enh02_calibrate_retrieval_thresholds_empirical_f1_sweep(
        self, tmp_path: Path
    ) -> None:
        """calibrate_thresholds must sweep F1/Youden's J over labeled distributions and emit a receipt."""
        receipt_path = tmp_path / "calibration_receipt.json"
        res = calibrate_thresholds(verify_only=True, output_receipt_path=receipt_path)
        assert receipt_path.is_file()
        assert res["tau_grounding"] == 0.725
        assert res["tau_contradiction"] == 0.40
        assert res["tau_coverage"] == 0.90
        assert res["sweep_metrics"]["grounding"]["f1"] == 1.0
        assert res["sweep_metrics"]["contradiction"]["f1"] == 1.0
        assert res["sweep_metrics"]["coverage"]["f1"] == 1.0

        # Shifted distribution must shift optimal_tau dynamically (proving non-hardcoded sweep)
        shifted_samples = [(0.95, 1), (0.91, 1), (0.85, 0), (0.80, 0)]
        shifted_sweep = sweep_optimal_threshold(shifted_samples, candidate_grid=[0.70, 0.82, 0.88, 0.96])
        assert shifted_sweep["optimal_tau"] == 0.88
        assert shifted_sweep["f1"] == 1.0

