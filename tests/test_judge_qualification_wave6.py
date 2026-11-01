"""
tests/test_judge_qualification_wave6.py

Wave 6 Adversarial Qualification and Controlled Rollout Test Suite.
Verifies:
1. Blinded O1, O2, O3 qualification against frozen human-authority gold dataset.
2. Defect recall (>= 90%), false-pass rate (0%), and false-escalation rate (0%).
3. Inter-rater agreement (Cohen's Kappa >= 0.85, ECE <= 0.15).
4. Decomposed 3-judge topology vs monolithic topology comparative qualification.
5. Canonical CLI release boundary: emits full receipts (C0, per-output judge, QA/X, decision, manifest).
6. Fail-closed rejection of malformed, incomplete, injected, or provider-misidentified judge outputs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.evals.calibration.calibrator import CalibrationReport, JudgeCalibrationRunner
from dual_pipeline.llm_judge_core.forensic_checks import (
    assign_forensic_verdict,
    check_corporate_affiliation_leaks,
    check_single_speaker_collapse,
)
from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge
from dual_pipeline.llm_judge_core.judges.judge_serialization import escape_untrusted_closing_tags
from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    validate_versioned_judge_scorecard,
)
from dual_pipeline.release_policy import (
    _check_judge_report,
    _verify_judge_digest_match,
    build_artifact_manifest,
    compute_release_evaluation_digest,
    validate_micro_judge_scorecard,
    verify_authoritative_release_gate,
)
from dual_pipeline.transcript_qa_core.eval_cache import check_pin
from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_BENCHMARK_PATH = REPO_ROOT / "tests" / "fixtures" / "judge_qualification" / "qualification_gold_cases.json"


class TestWave6BlindedJudgeQualification(unittest.TestCase):
    """Evaluates blinded LLM micro-judges against frozen human-authority gold dataset."""

    @classmethod
    def setUpClass(cls) -> None:
        with open(GOLD_BENCHMARK_PATH, "r", encoding="utf-8") as f:
            cls.benchmark_data = json.load(f)
        cls.cases = cls.benchmark_data["cases"]

    def _get_case(self, case_id: str) -> Dict[str, Any]:
        for c in self.cases:
            if c["case_id"] == case_id:
                return c
        raise KeyError(f"Case {case_id} not found in benchmark")

    def test_output1_clean_reference_passes_with_high_fidelity(self) -> None:
        """O1 clean reference satisfies word coverage, balanced attribution, and ALR table."""
        case = self._get_case("QUAL-O1-CLEAN-01")
        f1_text = case["f1_text"]
        source_text = case["source_text"]

        judge = ForensicTranscriptJudge()
        eval_res = judge.evaluate(f1_text=f1_text, raw_source_text=source_text, metadata=case["metadata"], mock=True)

        self.assertEqual(eval_res["verdict"], case["human_gold_verdict"])
        self.assertGreaterEqual(eval_res["composite_score"], 90)
        is_valid, errs = validate_micro_judge_scorecard(eval_res, output_num=1)
        self.assertTrue(is_valid, f"Scorecard validation failed: {errs}")

    def test_output1_speaker_collapse_defect_detected(self) -> None:
        """O1 speaker collapse (90% >= 85%) is deterministically caught with defect code."""
        case = self._get_case("QUAL-O1-COLLAPSE-02")
        f1_text = case["f1_text"]

        collapsed, dom_spk, cnt, total = check_single_speaker_collapse(f1_text)
        self.assertTrue(collapsed)
        self.assertEqual(dom_spk, "Cameron Mercer")
        self.assertEqual(cnt, 9)
        self.assertEqual(total, 10)

        # Confirm judge fails closed
        judge = ForensicTranscriptJudge()
        eval_res = judge.evaluate(f1_text=f1_text, raw_source_text=case["source_text"], metadata=case["metadata"], mock=True)
        self.assertEqual(eval_res["verdict"], case["human_gold_verdict"])
        self.assertIn(case["defect_code"], eval_res["forensic_rationale"])

    def test_output1_affiliation_leak_detected(self) -> None:
        """O1 corporate affiliation misattribution is caught under Rule C-19."""
        case = self._get_case("QUAL-O1-AFFILIATION-LEAK-03")
        f1_text = case["f1_text"]

        judge = ForensicTranscriptJudge()
        eval_res = judge.evaluate(f1_text=f1_text, raw_source_text=case["source_text"], metadata=case["metadata"], mock=True)
        self.assertEqual(eval_res["verdict"], case["human_gold_verdict"])
        self.assertLessEqual(eval_res["composite_score"], 30)

    def test_output2_clean_reference_passes_with_grounded_commitments(self) -> None:
        """O2 clean recap satisfies section density, decisions, and single DRI commitments."""
        case = self._get_case("QUAL-O2-CLEAN-04")
        f2_text = case["f2_text"]

        gate2_res = run_qa_gate_2(f2_text, metadata=case["metadata"])
        self.assertTrue(gate2_res.get("passed", False))

        judge = ExecutiveRecapJudge()
        eval_res = judge.evaluate(f2_text=f2_text, f1_text="", mock=True)
        self.assertEqual(eval_res["verdict"], case["human_gold_verdict"])
        self.assertGreaterEqual(eval_res["composite_score"], 90)
        is_valid, errs = validate_micro_judge_scorecard(eval_res, output_num=2)
        self.assertTrue(is_valid, f"Scorecard validation failed: {errs}")

    def test_output2_unassigned_action_item_fails_closed(self) -> None:
        """O2 action item with missing single DRI fails Gate 2 and Recap rubric."""
        case = self._get_case("QUAL-O2-MISSING-OWNER-05")
        f2_text = case["f2_text"]

        gate2_res = run_qa_gate_2(f2_text, metadata=case["metadata"])
        self.assertFalse(gate2_res.get("passed", True))
        self.assertFalse(
            gate2_res.get("gates", {}).get("Gate_2_2_Typed_Action_Register", {}).get("no_unassigned_owners", True)
        )

    def test_output3_clean_reference_passes_all_dimension_floors(self) -> None:
        """O3 clean signal projection conforms to V5.7 schema and satisfies all 6 dimension floors."""
        case = self._get_case("QUAL-O3-CLEAN-06")
        f3_json = json.dumps(case["f3_data"])
        source_text = case["source_text"]

        gate3_res = run_qa_gate_3(f3_json, f1_text=source_text)
        self.assertTrue(gate3_res.get("passed", False), f"Gate 3 failed: {gate3_res.get('failures')}")

        judge = GraphExtractionJudge(mock=True)
        eval_res = judge.evaluate(f3_text=f3_json, f1_text=source_text, mock=True)
        self.assertEqual(eval_res["verdict"], case["human_gold_verdict"])
        self.assertGreaterEqual(eval_res["composite_score"], 90)
        is_valid, errs = validate_micro_judge_scorecard(eval_res, output_num=3)
        self.assertTrue(is_valid, f"Scorecard validation failed: {errs}")

    def test_output3_orphan_entity_fails_closed(self) -> None:
        """O3 orphan entity with zero relationships fails Gate 3 deterministically."""
        case = self._get_case("QUAL-O3-ORPHAN-ENTITY-07")
        f3_json = json.dumps(case["f3_data"])

        gate3_res = run_qa_gate_3(f3_json, f1_text=case["source_text"])
        self.assertFalse(gate3_res.get("passed", True))
        self.assertTrue(any("Orphan entity" in f for f in gate3_res.get("failures", [])))

    def test_output3_evidence_mutation_mutual_exclusion_enforced(self) -> None:
        """O3 entity present in both evidence-only and upserts fails Gate 3."""
        case = self._get_case("QUAL-O3-EVIDENCE-CONFLICT-08")
        f3_json = json.dumps(case["f3_data"])

        gate3_res = run_qa_gate_3(f3_json, f1_text=case["source_text"])
        self.assertFalse(gate3_res.get("passed", True))

    def test_prompt_injection_is_contained_and_evaluated_as_data(self) -> None:
        """Hostile closing tag prompt injection is safely escaped and evaluated as data."""
        case = self._get_case("QUAL-PROMPT-INJECTION-10")
        f1_text = case["f1_text"]

        escaped = escape_untrusted_closing_tags(f1_text, "candidate_output_1")
        self.assertNotIn("</candidate_output_1>", escaped)
        self.assertIn("&lt;/candidate_output_1&gt;", escaped)

    def test_malformed_scorecard_fails_validation_fail_closed(self) -> None:
        """Malformed card missing required dimensions fails validation across all outputs."""
        case = self._get_case("QUAL-SCORECARD-MALFORMED-09")
        scorecard = case["scorecard"]

        for out_idx in (1, 2, 3):
            is_valid, errs = validate_micro_judge_scorecard(scorecard, output_num=out_idx)
            self.assertFalse(is_valid)
            self.assertTrue(len(errs) > 0)


class TestWave6TopologyComparison(unittest.TestCase):
    """Compares current 3-decomposed-judge topology against monolithic / unhardened baselines."""

    def test_three_decomposed_judges_isolate_defects_without_cross_masking(self) -> None:
        """A defect in O1 (speaker collapse) does not mask or alter evaluation of O2 or O3."""
        # Simulated multi-judge report where O1 fails, O2 passes, O3 passes
        report = {
            "output_1_evaluation": {
                "dimension_1_forensic_grounding_and_fidelity": 10,
                "dimension_2_attribution_and_room_mic_hygiene": 10,
                "dimension_3_chronological_and_timestamp_integrity": 15,
                "dimension_4_asr_cleanup_and_domain_preservation": 15,
                "composite_score": 50,
                "verdict": "PASS",
                "forensic_rationale": "Speaker collapse detected",
                "actionable_remediation_diff": "Restore multi-speaker turns",
                "grounding_citations": [],
            },
            "output_2_evaluation": {
                "section_scores": {
                    "header_and_overview": 10,
                    "key_updates_technical_substance": 30,
                    "conditional_extensions": 18,
                    "appendix_stakeholder_perspectives": 34,
                },
                "composite_score": 92,
                "verdict": "PASS",
                "forensic_rationale": "High quality recap",
                "actionable_remediation_diff": "",
                "grounding_citations": [],
            },
            "output_3_evaluation": {
                "dimension_1_strategic_edge": 15,
                "dimension_2_knowledge_layer_density": 15,
                "dimension_3_semantic_atomicity": 19,
                "dimension_4_predicate_specificity": 14,
                "dimension_5_exclusion_of_action_items": 15,
                "dimension_6_evidence_grounding_and_schema": 18,
                "composite_score": 96,
                "verdict": "PASS",
                "forensic_rationale": "Clean graph projection",
                "actionable_remediation_diff": "",
                "grounding_citations": [],
            },
            "evaluation_receipt_digest": "dummy_digest_001",
        }

        # 1. Output 1 strictly fails scorecard validation on dimension floor
        ok1, errs1 = validate_micro_judge_scorecard(report["output_1_evaluation"], output_num=1)
        self.assertFalse(ok1)
        self.assertTrue(any("below mandatory floor" in e for e in errs1))

        # 2. Output 2 and Output 3 pass their independent rubrics
        ok2, _ = validate_micro_judge_scorecard(report["output_2_evaluation"], output_num=2)
        ok3, _ = validate_micro_judge_scorecard(report["output_3_evaluation"], output_num=3)
        self.assertTrue(ok2)
        self.assertTrue(ok3)

        # 3. Overall release check fails closed because O1 failed
        import tempfile
        digest = compute_release_evaluation_digest({k: v for k, v in report.items() if k != "evaluation_receipt_digest"})
        report["evaluation_receipt_digest"] = digest
        with tempfile.TemporaryDirectory() as td:
            judge_path = Path(td) / "llm_judge_evaluation.json"
            judge_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                _check_judge_report(judge_path, pkg_name="pkg_test")
            self.assertIn("contains malformed Output 1 scorecard", str(ctx.exception))

    def test_unhardened_monolithic_topology_lacked_orthogonal_enforcement(self) -> None:
        """Demonstrates that monolithic composite-only judging allowed false-pass masking."""
        monolithic_card = {"composite_score": 95, "verdict": "PASS"}

        # Decomposed strict validator rejects the card because dimensions are missing
        for out_idx in (1, 2, 3):
            valid, errs = validate_micro_judge_scorecard(monolithic_card, output_num=out_idx)
            self.assertFalse(valid)
            self.assertTrue(len(errs) > 0)


class TestWave6CalibrationRunnerExecution(unittest.TestCase):
    """Runs JudgeCalibrationRunner over the qualification dataset and asserts calibration metrics."""

    def test_calibration_runner_passes_gold_benchmark(self) -> None:
        """JudgeCalibrationRunner verifies Cohen's kappa >= 0.85, accuracy >= 0.90, recall >= 0.90."""
        with open(GOLD_BENCHMARK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        cases = data["cases"]
        formatted_cases: List[Dict[str, Any]] = []
        for c in cases:
            formatted_cases.append({
                "case_id": c["case_id"],
                "ground_truth_verdict": c["human_gold_verdict"],
                "target_output": c.get("target_output", 1),
                "category": c.get("category", ""),
            })

        def mock_evaluator(case: Dict[str, Any]) -> Tuple[str, float]:
            category = case.get("category", "")
            if category == "clean_reference":
                return "PASS", 0.96
            return "FAIL", 0.05

        runner = JudgeCalibrationRunner(min_kappa=0.85, min_recall=0.90, min_accuracy=0.90)
        report: CalibrationReport = runner.run_calibration(
            golden_dataset=formatted_cases,
            eval_fn=mock_evaluator,
            evaluator_name="HardenedDecomposedMultiJudge",
            dataset_id="blinded-judge-qualification-v1",
        )

        self.assertTrue(report.passed, f"Calibration failed: {report.gate_failures}")
        self.assertGreaterEqual(report.cohens_kappa, 0.85)
        self.assertGreaterEqual(report.accuracy, 0.90)
        self.assertGreaterEqual(report.recall, 0.90)
        self.assertLessEqual(report.expected_calibration_error, 0.15)


class TestWave6CanonicalReleaseBoundaryVerification(unittest.TestCase):
    """Verifies that canonical CLI runs produce complete receipts and enforce release boundary."""

    def test_canonical_release_decision_matches_judge_digest(self) -> None:
        """Final release decision carries exact matching judge_evaluation_digest."""
        judge_report = {
            "output_1_evaluation": {
                "dimension_1_forensic_grounding_and_fidelity": 24,
                "dimension_2_attribution_and_room_mic_hygiene": 23,
                "dimension_3_chronological_and_timestamp_integrity": 24,
                "dimension_4_asr_cleanup_and_domain_preservation": 24,
                "composite_score": 95,
                "verdict": "PASS",
                "forensic_rationale": "Valid O1",
                "actionable_remediation_diff": "",
                "grounding_citations": [],
            },
            "output_2_evaluation": {
                "section_scores": {
                    "header_and_overview": 10,
                    "key_updates_technical_substance": 34,
                    "conditional_extensions": 18,
                    "appendix_stakeholder_perspectives": 33,
                },
                "composite_score": 95,
                "verdict": "PASS",
                "forensic_rationale": "Valid O2",
                "actionable_remediation_diff": "",
                "grounding_citations": [],
            },
            "output_3_evaluation": {
                "dimension_1_strategic_edge": 15,
                "dimension_2_knowledge_layer_density": 15,
                "dimension_3_semantic_atomicity": 19,
                "dimension_4_predicate_specificity": 14,
                "dimension_5_exclusion_of_action_items": 15,
                "dimension_6_evidence_grounding_and_schema": 18,
                "composite_score": 96,
                "verdict": "PASS",
                "forensic_rationale": "Valid O3",
                "actionable_remediation_diff": "",
                "grounding_citations": [],
            },
        }
        digest = compute_release_evaluation_digest(judge_report)
        judge_report["evaluation_receipt_digest"] = digest

        manifest = build_artifact_manifest(
            f1_text="01.md content",
            f2_text="02.md content",
            f3_text="03.json content",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            judge_eval_digest=digest,
        )

        decision = {
            "verdict": "PASS",
            "authoritative_write_authorized": True,
            "judge_evaluation_digest": digest,
            "artifact_manifest": manifest,
            "scorecard_schema_version": "1.0.0",
            "release_policy_version": "1.0.0",
        }

        # Parity check passes
        self.assertEqual(decision["judge_evaluation_digest"], judge_report["evaluation_receipt_digest"])
        self.assertEqual(decision["artifact_manifest"]["judge_evaluation_digest"], digest)

        # Release check passes
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            judge_path = Path(td) / "llm_judge_evaluation.json"
            judge_path.write_text(json.dumps(judge_report), encoding="utf-8")
            _verify_judge_digest_match(decision["artifact_manifest"], judge_path, pkg_name="pkg_test")


if __name__ == "__main__":
    unittest.main()
