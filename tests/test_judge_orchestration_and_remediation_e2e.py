"""E2E verification tests for Judge Orchestration, Release Policy, and Remediation Circuit Breaker (Wave 3)."""
import json
import unittest
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.attempt_binding import _dominates
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import check_circuit_breaker
from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    CriterionApplicability,
    JudgeCriterion,
    validate_output3_scorecard,
)
from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
from dual_pipeline.release_policy import (
    compute_release_evaluation_digest,
    validate_micro_judge_scorecard,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker
from dual_pipeline.transcript_qa_core.evaluator import _apply_partial_qa_skips, _eval_output_n, _eval_output1
from dual_pipeline.transcript_qa_core.judge_failure_diagnostics import JudgeFailureDiagnosticsCollector
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    extract_remediation_from_user_content,
)


class JudgeOrchestrationAndRemediationE2ETests(unittest.TestCase):
    """End-to-end verification of Wave 3 orchestration, release policy, and remediation loop fixes."""

    def test_e2e_circuit_breaker_distinct_hashes_for_distinct_failures(self):
        """Verify CandidateCircuitBreaker produces distinct hashes for distinct QA and Judge failures."""
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)

        # Create two distinct failure scenarios
        eval_result_1 = MagicMock()
        eval_result_1.authoritative_write_authorized = False
        eval_result_1.qa_report = {
            "failed_gates": ["gate_1_1_format"],
            "output_01_forensic_transcript": {"passed": False, "failed_gates": ["gate_1_1_format"]},
        }
        eval_result_1.judge_report = {"judge_failure_diagnostics": {"failures": [{"output": "01", "observed": "low_score"}]}}
        eval_result_1.final_decision = {"status": "FAIL", "output_1_pass": False}

        eval_result_2 = MagicMock()
        eval_result_2.authoritative_write_authorized = False
        eval_result_2.qa_report = {
            "failed_gates": ["gate_2_14_overlap"],
            "output_02_executive_recap": {"passed": False, "failed_gates": ["gate_2_14_overlap"]},
        }
        eval_result_2.judge_report = {"judge_failure_diagnostics": {"failures": [{"output": "02", "observed": "recap_mismatch"}]}}
        eval_result_2.final_decision = {"status": "FAIL", "output_2_pass": False}

        # Check circuit breaker on failure 1
        cand_1 = MagicMock(output_1="cand 1", output_2="cand 2", output_3="cand 3")
        tripped_1 = check_circuit_breaker(cb, eval_result_1, cand_1, attempt=1, max_attempts=3)
        self.assertFalse(tripped_1)
        hash_1 = cb.last_error_hash
        # Ensure it didn't hash to the constant empty-dict hash 500af4730b2e
        self.assertNotEqual(hash_1[:12], "500af4730b2e")

        # Check circuit breaker on failure 2 (different failure)
        cand_2 = MagicMock(output_1="cand 1 v2", output_2="cand 2 v2", output_3="cand 3 v2")
        tripped_2 = check_circuit_breaker(cb, eval_result_2, cand_2, attempt=2, max_attempts=3)
        self.assertFalse(tripped_2, "Circuit breaker should not trip when failures are distinct across attempts")
        hash_2 = cb.last_error_hash
        self.assertNotEqual(hash_1, hash_2, "Distinct failures must produce distinct error hashes")
        self.assertEqual(cb.consecutive_failures, 1, "Consecutive failure counter must reset to 1 on distinct failure")

    def test_e2e_skipped_scorecards_pass_release_policy_validation(self):
        """Verify _apply_partial_qa_skips sets composite_score: None and passes release policy scorecard validation."""
        qa_report = {
            "passed": False,
            "output_01_forensic_transcript": {"passed": False, "status": "FAIL"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
        }
        meta = {"mode": "STANDALONE"}
        evals = (None, None, None)
        out1_eval, out2_eval, out3_eval = _apply_partial_qa_skips(qa_report, evals, meta)

        self.assertIsNotNone(out1_eval)
        self.assertEqual(out1_eval.get("status"), "SKIPPED")
        self.assertIsNone(out1_eval.get("composite_score"), "SKIPPED scorecard must have composite_score: None")

        valid, errs = validate_micro_judge_scorecard(out1_eval, output_num=1)
        self.assertTrue(valid, f"SKIPPED scorecard with composite_score: None must pass validation, got: {errs}")

    def test_e2e_high_water_mark_rollback_restores_best_candidate(self):
        """Verify SelectiveRemediationStateMachine.get_prior_candidate returns best candidate when regressed."""
        sm = SelectiveRemediationStateMachine(max_attempts=3)

        # Attempt 1: Good candidate (score 92)
        cand_attempt1 = "Content of Attempt 1 with high score 92"
        sm.evaluate_attempt(
            final_decision={"output_2_pass": True, "output_2_score": 92},
            judge_report={"output_2_evaluation": {"composite_score": 92}},
            qa_report={"output_02_executive_recap": {"passed": True}},
            artifacts={"output_2": cand_attempt1},
        )
        self.assertEqual(sm.get_high_water_mark_score("output_2"), 92)

        sm.advance_attempt()

        # Attempt 2: Regressed candidate (score 70)
        cand_attempt2 = "Content of Attempt 2 with regressed score 70"
        sm.evaluate_attempt(
            final_decision={"output_2_pass": False, "output_2_score": 70},
            judge_report={"output_2_evaluation": {"composite_score": 70}},
            qa_report={"output_02_executive_recap": {"passed": False}},
            artifacts={"output_2": cand_attempt2},
        )
        self.assertTrue(sm.has_regressed("output_2"))

        # get_prior_candidate must restore best candidate (Attempt 1), NOT regressed latest candidate (Attempt 2)
        prior_cand = sm.get_prior_candidate("output_2")
        self.assertEqual(prior_cand, cand_attempt1, "Regressed candidate must fall back to high-water mark best candidate")

    def test_e2e_attempt_binding_dominates_allows_quality_rollback(self):
        """Verify _dominates allows rollback to an attempt with higher aggregate quality without dropping passes."""
        prior_eval = MagicMock()
        prior_eval.authoritative_write_authorized = False
        prior_eval.judge_report = {
            "aggregate_composite_score": 91.5,
            "output_1_evaluation": {"composite_score": 95},
            "output_2_evaluation": {"composite_score": 92},
            "output_3_evaluation": {"composite_score": 88},
        }
        prior_eval.final_decision = {
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": False,
        }

        current_eval = MagicMock()
        current_eval.authoritative_write_authorized = False
        current_eval.judge_report = {
            "aggregate_composite_score": 82.0,
            "output_1_evaluation": {"composite_score": 80},
            "output_2_evaluation": {"composite_score": 85},
            "output_3_evaluation": {"composite_score": 81},
        }
        current_eval.final_decision = {
            "output_1_pass": False,
            "output_2_pass": False,
            "output_3_pass": False,
        }

        outputs = ("output_1", "output_2", "output_3")
        # Prior is clearly superior in quality and passes
        self.assertTrue(_dominates(prior_eval, current_eval, outputs))

    def test_e2e_remediation_directive_extraction_from_user_content(self):
        """Verify extract_remediation_from_user_content extracts directive cleanly for cached execution."""
        sample_prompt = (
            "<forensic_transcript>\n[00:00:25] Alice: Hello\n</forensic_transcript>\n\n"
            "<remediation_directive>\nPREVIOUS ATTEMPT FAILED.\n"
            "CRITIQUE DIRECTIVE:\nExpand discussion section with more detail.\n\nSURGICAL\n"
            "</remediation_directive>"
        )
        _, critique = extract_remediation_from_user_content(sample_prompt)
        self.assertIsNotNone(critique)
        self.assertIn("Expand discussion section with more detail", critique)

    def test_e2e_release_policy_digest_invariant_to_retry_scope(self):
        """Verify compute_release_evaluation_digest is invariant to retry_scope addition."""
        eval_data_1 = {
            "status": "FAIL",
            "overall_verdict": "FAIL",
            "aggregate_composite_score": 75.0,
        }
        digest_before = compute_release_evaluation_digest(eval_data_1)

        # Mutate by adding retry_scope
        eval_data_2 = dict(eval_data_1)
        eval_data_2["retry_scope"] = "output_1,output_2"
        digest_after = compute_release_evaluation_digest(eval_data_2)

        self.assertEqual(digest_before, digest_after, "retry_scope must be excluded from evaluation digest computation")

    def test_e2e_judge_failure_diagnostics_ignores_skipped_scorecards(self):
        """Verify JudgeFailureDiagnosticsCollector ignores SKIPPED scorecards and emits no phantom score=0 failures."""
        collector = JudgeFailureDiagnosticsCollector()
        judge_report = {
            "output_1_evaluation": {
                "status": "SKIPPED",
                "composite_score": None,
                "verdict": "FAIL",
                "reason": "DETERMINISTIC_QA_FAILED",
            },
            "output_2_evaluation": {
                "status": "PASS",
                "composite_score": 95,
                "verdict": "PASS",
            },
            "output_3_evaluation": {
                "status": "PASS",
                "composite_score": 92,
                "verdict": "PASS",
            },
        }
        count = collector.extract_from_judge_report(judge_report)
        self.assertEqual(count, 0, "SKIPPED scorecards must not generate LLM judge failure records")
        self.assertEqual(len(collector.failures), 0)


if __name__ == "__main__":
    unittest.main()
