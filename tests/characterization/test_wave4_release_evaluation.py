"""
tests/characterization/test_wave4_release_evaluation.py

Wave 4 characterization tests:
- Decoupled release policy invariants and consolidation (dual_pipeline/release_policy.py)
- Non-compensating 11 retrieval release gates evaluation
- Micro-judge scorecard validation and score bounds
- Cryptographic evaluation receipt digest sealing
- Auto-heal default isolation (no side-effect mutation during evaluation)
- Module line budget compliance (<= 400 lines)
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.release_policy import (
    CANONICAL_RELEASE_POLICY,
    RETRIEVAL_RELEASE_11_GATES,
    ReleaseVerdict,
    compute_release_evaluation_digest,
    evaluate_retrieval_11_gates,
    validate_micro_judge_scorecard,
)
from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release


class TestWave4ReleaseEvaluation(unittest.TestCase):
    """Characterization tests for Wave 4 Release & Evaluation Gate Decoupling."""

    def test_module_line_budgets(self) -> None:
        """Verify all touched and core release evaluation modules adhere to the 400-line budget."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        target_modules = [
            "dual_pipeline/release_policy.py",
            "dual_pipeline/llm_judge_core/orchestrator.py",
            "dual_pipeline/transcript_qa_core/evaluator.py",
            "dual_pipeline/release_thresholds.py",
        ]
        for rel_path in target_modules:
            abs_path = repo_root / rel_path
            self.assertTrue(abs_path.exists(), f"Module {rel_path} does not exist")
            line_count = len(abs_path.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                400,
                f"Module {rel_path} exceeds 400-line budget ({line_count} lines)",
            )

    def test_canonical_release_policy_thresholds(self) -> None:
        """Verify CANONICAL_RELEASE_POLICY threshold values and evaluation."""
        self.assertEqual(CANONICAL_RELEASE_POLICY.output1_pass_threshold, 95)
        self.assertEqual(CANONICAL_RELEASE_POLICY.output2_pass_threshold, 95)
        self.assertEqual(CANONICAL_RELEASE_POLICY.output3_pass_threshold, 95)
        self.assertEqual(CANONICAL_RELEASE_POLICY.advisory_floor, 80)

        # Output 1: >=95 -> PASS, 80-94 -> PASS_ADVISORY, <80 -> FAIL
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 96),
            ReleaseVerdict.PASS,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 92),
            ReleaseVerdict.PASS_ADVISORY,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 85),
            ReleaseVerdict.PASS_ADVISORY,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 75),
            ReleaseVerdict.FAIL,
        )

        # Output 2: >=95 -> PASS, <95 -> FAIL (no advisory)
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 96),
            ReleaseVerdict.PASS,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 95),
            ReleaseVerdict.PASS,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 94),
            ReleaseVerdict.FAIL,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 89),
            ReleaseVerdict.FAIL,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 79),
            ReleaseVerdict.FAIL,
        )

        # Output 3: >=95 -> PASS, <95 -> FAIL (no advisory)
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 95),
            ReleaseVerdict.PASS,
        )
        self.assertEqual(
            CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 94),
            ReleaseVerdict.FAIL,
        )

    def test_retrieval_11_gates_evaluation(self) -> None:
        """Verify evaluate_retrieval_11_gates handles all passed, failures, and missing gates."""
        self.assertEqual(len(RETRIEVAL_RELEASE_11_GATES), 11)

        # All 11 gates passed
        valid_gates = {gate: True for gate in RETRIEVAL_RELEASE_11_GATES}
        passed, failures = evaluate_retrieval_11_gates(valid_gates)
        self.assertTrue(passed)
        self.assertEqual(failures, [])

        # Missing gate
        missing_gates = valid_gates.copy()
        del missing_gates["real_bge_m3"]
        passed, failures = evaluate_retrieval_11_gates(missing_gates)
        self.assertFalse(passed)
        self.assertIn("real_bge_m3 (MISSING)", failures)

        # Failed gate
        failed_gates = valid_gates.copy()
        failed_gates["latency"] = False
        passed, failures = evaluate_retrieval_11_gates(failed_gates)
        self.assertFalse(passed)
        self.assertIn("latency (FAILED)", failures)

    def test_validate_micro_judge_scorecard(self) -> None:
        """Verify scorecard validation for bounds, sum consistency, and verdict matching."""
        valid_scorecard = {
            "dimension_1": 25,
            "dimension_2": 24,
            "dimension_3": 25,
            "dimension_4": 24,
            "composite_score": 98,
            "verdict": "PASS",
        }
        valid, errors = validate_micro_judge_scorecard(valid_scorecard, output_num=1)
        self.assertTrue(valid)
        self.assertEqual(errors, [])

        # Dimension out of bounds (> 25)
        bad_dim = valid_scorecard.copy()
        bad_dim["dimension_1"] = 26
        valid, errors = validate_micro_judge_scorecard(bad_dim, output_num=1)
        self.assertFalse(valid)
        self.assertTrue(any("out of bounds" in e for e in errors))

        # Composite score mismatch with dimension sum
        bad_sum = valid_scorecard.copy()
        bad_sum["composite_score"] = 90
        valid, errors = validate_micro_judge_scorecard(bad_sum, output_num=1)
        self.assertFalse(valid)
        self.assertTrue(any("does not match dimension sum" in e for e in errors))

        # Verdict mismatch
        bad_verdict = valid_scorecard.copy()
        bad_verdict["verdict"] = "FAIL"
        valid, errors = validate_micro_judge_scorecard(bad_verdict, output_num=1)
        self.assertFalse(valid)
        self.assertTrue(any("does not match policy expected" in e for e in errors))

    def test_cryptographic_evaluation_digest(self) -> None:
        """Verify compute_release_evaluation_digest is deterministic and 64-char SHA-256."""
        payload1 = {"status": "PASS", "score": 95, "run_id": "RUN-001"}
        payload2 = {"run_id": "RUN-001", "score": 95, "status": "PASS"}
        payload3 = {"status": "FAIL", "score": 75, "run_id": "RUN-001"}

        digest1 = compute_release_evaluation_digest(payload1)
        digest2 = compute_release_evaluation_digest(payload2)
        digest3 = compute_release_evaluation_digest(payload3)

        self.assertEqual(len(digest1), 64)
        self.assertEqual(digest1, digest2, "Keys order should not affect canonical digest")
        self.assertNotEqual(digest1, digest3, "Different content must produce different digests")

    def test_decomposed_multi_judge_evaluation_seals_digest(self) -> None:
        """Verify DecomposedMultiJudge.evaluate_parallel returns a sealed evaluation_receipt_digest."""
        judge = DecomposedMultiJudge(mock=True)
        f1_text = "Speaker 1: Hello team.\nSpeaker 2: Confirmed."
        f2_text = "# Executive Recap\n\n## Decisions\n- Decision 1"
        f3_data = {"nodes": [{"id": "decision:1"}], "edges": []}

        result = judge.evaluate_parallel(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_data=f3_data,
            source_text="Speaker 1: Hello team.\nSpeaker 2: Confirmed.",
            metadata={"meeting_name": "Test Sync", "date": "09-13-2026"},
        )

        self.assertIn("evaluation_receipt_digest", result)
        receipt_digest = result["evaluation_receipt_digest"]
        self.assertIsInstance(receipt_digest, str)
        self.assertEqual(len(receipt_digest), 64)

        # Verify digest recalculation matches the result (with receipt digest excluded or matched)
        result_without_digest = {k: v for k, v in result.items() if k != "evaluation_receipt_digest"}
        expected_digest = compute_release_evaluation_digest(result_without_digest)
        self.assertEqual(receipt_digest, expected_digest)

    def test_evaluator_auto_heal_default_is_false(self) -> None:
        """Verify evaluate_judge_and_release does not mutate inputs when called with defaults."""
        f1_text = "Speaker 1: Hello team.\nSpeaker 2: Confirmed."
        f2_text = "# Executive Recap\n\n## Decisions\n- Decision 1"
        f3_data = '{"nodes": [{"id": "decision:1"}], "edges": []}'

        qa_report, judge_eval, release_decision = evaluate_judge_and_release(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_data,
            source_text="Speaker 1: Hello team.\nSpeaker 2: Confirmed.",
            metadata={"meeting_name": "Test Sync", "date": "09-13-2026"},
            mock=True,
        )

        self.assertIsInstance(qa_report, dict)
        self.assertIsInstance(judge_eval, dict)
        self.assertIsInstance(release_decision, dict)

        # Since auto_heal defaults to False, deterministic_healing should NOT be present in qa_report
        self.assertNotIn("deterministic_healing", qa_report)
        self.assertNotIn("healed_artifacts", qa_report)


if __name__ == "__main__":
    unittest.main()
