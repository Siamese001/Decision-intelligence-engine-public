"""
tests/test_stage03_retry_circuit_breaker_e2e.py

End-to-End and Adversarial Regression Suite for Stage 03 Retry Circuit Breaker & Reasoning Effort.

Covers:
1. Exact production regression: Tanner Evals incident where Attempt 1 scored 66, Attempt 2 scored 65 (regressed),
   and the circuit breaker previously tripped prematurely because bound_attempts.restore ran before the circuit breaker check.
2. Invariant bypass prevention: Verifies CandidateCircuitBreaker still reliably trips when consecutive failures are truly identical.
3. Directive freshness: Verifies Attempt 3 receives the critique and remediation directive from Attempt 2 rather than stale Attempt 1.
4. Rollback and recovery: Verifies dominating Attempt 1 is restored if final attempt fails.
5. Reasoning budget preservation: Verifies Stage 03 preserves provider and metadata reasoning_effort='high' without falling back to 'low'.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.lifecycle import run_synthesis_loop
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker
from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner


class Stage03RetryCircuitBreakerE2ETests(unittest.TestCase):
    """End-to-end regression and edge-case verification for Stage 03 retry and circuit-breaker evaluation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_p = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_01_exact_production_regression_distinct_regressed_attempt_executes_all_retries(self) -> None:
        """
        Category: Exact production regression
        Tanner Evals regression: Attempt 1 scores 66, Attempt 2 produces distinct candidate scoring 65.
        Circuit breaker must NOT trip on Attempt 2, and must execute Attempt 3 which succeeds with score 92.
        """
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        metadata = {
            "project": "nea",
            "meeting_name": "NEA_Agentic_Governance_Evals",
            "circuit_breaker": cb,
            "output3_only": True,
        }

        # Mock evaluations for 3 attempts
        eval_att1 = QAEvaluationResult(
            passed=False,
            qa_report={"failures": ["Proposal 13 contains conversational filler"]},
            judge_report={
                "status": "FAIL",
                "output_3_evaluation": {"composite_score": 66.0, "verdict": "FAIL"},
                "critique": "Remove conversational filler from proposal 13.",
            },
            final_decision={
                "status": "FAIL",
                "output_3_pass": False,
                "failures": ["Proposal 13 contains conversational filler"],
            },
            authoritative_write_authorized=False,
        )

        eval_att2 = QAEvaluationResult(
            passed=False,
            qa_report={"failures": ["Proposal 14 missing atomic rationale and evidence citation"]},
            judge_report={
                "status": "FAIL",
                "output_3_evaluation": {
                    "composite_score": 65.0,
                    "verdict": "FAIL",
                    "forensic_rationale": "Fix proposal 14 evidence citation.",
                },
            },
            final_decision={
                "status": "FAIL",
                "output_3_pass": False,
                "failures": ["Proposal 14 missing atomic rationale and evidence citation"],
            },
            authoritative_write_authorized=False,
        )

        eval_att3 = QAEvaluationResult(
            passed=True,
            qa_report={"failures": []},
            judge_report={
                "status": "PASS",
                "output_3_evaluation": {"composite_score": 92.0, "verdict": "PASS"},
            },
            final_decision={
                "status": "PASS",
                "output_3_pass": True,
                "authoritative_write_authorized": True,
            },
            authoritative_write_authorized=True,
        )

        eval_sequence = [eval_att1, eval_att2, eval_att3]

        mock_qa = MagicMock()
        mock_qa.evaluate.side_effect = lambda *a, attempt=1, **kw: eval_sequence[min(attempt - 1, len(eval_sequence) - 1)]

        directives_seen: list[str] = []
        mock_synth = MagicMock()

        def _synthesize(*a, attempt=1, directive=None, remediation_directive=None, **kw):
            eff_dir = directive or remediation_directive or ""
            directives_seen.append(eff_dir)
            return SynthesisResult(
                output_3=f'{{"entities": ["E{attempt}"], "score_target": {attempt}}}',
                attempt=attempt,
            )

        mock_synth.synthesize.side_effect = _synthesize

        with patch("dual_pipeline.canonical_pipeline.lifecycle.get_synthesis_strategy", return_value=mock_synth), \
             patch("dual_pipeline.canonical_pipeline.lifecycle.get_qa_strategy", return_value=mock_qa), \
             patch("dual_pipeline.canonical_pipeline.attempt_binding.verify_attempt_binding"):

            attempts_run, synth_out, eval_out = run_synthesis_loop(
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                provider=MagicMock(),
                provider_type="mock",
                model_name="gpt-6-luna",
                reasoning_effort="high",
                source_text="**Amit Ayer**: Testing regression flow.",
                pa_context={"metadata": metadata},
                metadata=metadata,
                out_p=self.out_p,
                start_t=0.0,
                max_judge_retries=2,
            )

            # Assert all 3 attempts executed
            self.assertEqual(attempts_run, 3)
            self.assertFalse(cb.is_tripped())
            self.assertEqual(cb.consecutive_failures, 0)
            self.assertTrue(eval_out.authoritative_write_authorized)
            self.assertEqual(eval_out.final_decision.get("status"), "PASS")

            # Directive freshness: Attempt 3 must have received Attempt 2's critique
            self.assertIn("Fix proposal 14 evidence citation", directives_seen[2])

    def test_02_invariant_bypass_attempt_true_identical_failures_still_trip_circuit_breaker(self) -> None:
        """
        Category: Invariant bypass attempt
        Verifies that when two consecutive attempts produce truly identical failures,
        CandidateCircuitBreaker still halts the loop on Attempt 2 to protect token budget.
        """
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        metadata = {
            "project": "nea",
            "circuit_breaker": cb,
            "output3_only": True,
        }

        identical_eval = QAEvaluationResult(
            passed=False,
            qa_report={"failures": ["Permanent syntax violation"]},
            judge_report={
                "status": "FAIL",
                "output_3_evaluation": {"composite_score": 60.0, "verdict": "FAIL"},
                "critique": "Unfixable syntax error.",
            },
            final_decision={
                "status": "FAIL",
                "output_3_pass": False,
                "failures": ["Permanent syntax violation"],
            },
            authoritative_write_authorized=False,
        )

        mock_qa = MagicMock()
        mock_qa.evaluate.return_value = identical_eval

        mock_synth = MagicMock()
        mock_synth.synthesize.side_effect = lambda *a, attempt=1, **kw: SynthesisResult(
            output_3=f'{{"entities": ["static"], "jitter": {attempt}}}', attempt=attempt
        )

        with patch("dual_pipeline.canonical_pipeline.lifecycle.get_synthesis_strategy", return_value=mock_synth), \
             patch("dual_pipeline.canonical_pipeline.lifecycle.get_qa_strategy", return_value=mock_qa), \
             patch("dual_pipeline.canonical_pipeline.attempt_binding.verify_attempt_binding"):

            attempts_run, synth_out, eval_out = run_synthesis_loop(
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                provider=MagicMock(),
                provider_type="mock",
                model_name="gpt-6-luna",
                reasoning_effort="high",
                source_text="Source text",
                pa_context={"metadata": metadata},
                metadata=metadata,
                out_p=self.out_p,
                start_t=0.0,
                max_judge_retries=2,
            )

            self.assertEqual(attempts_run, 2)
            self.assertTrue(cb.is_tripped())
            self.assertEqual(cb.consecutive_failures, 2)
            self.assertIn("identical consecutive", cb.trip_reason)

    def test_03_rollback_and_recovery_restores_dominating_attempt_on_final_regression(self) -> None:
        """
        Category: Rollback & recovery
        When all attempts fail and subsequent attempts regress, bound_attempts.restore
        guarantees that the returned candidate and evaluation represent the highest quality attempt.
        """
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        metadata = {
            "project": "nea",
            "circuit_breaker": cb,
            "output3_only": True,
        }

        # Attempt 1: Score 66
        eval_1 = QAEvaluationResult(
            passed=False,
            qa_report={"failures": ["Minor error 1"]},
            judge_report={"output_3_evaluation": {"composite_score": 66.0, "verdict": "FAIL"}},
            final_decision={"status": "FAIL", "output_3_pass": False},
            authoritative_write_authorized=False,
        )
        # Attempt 2: Score 65
        eval_2 = QAEvaluationResult(
            passed=False,
            qa_report={"failures": ["Minor error 2"]},
            judge_report={"output_3_evaluation": {"composite_score": 65.0, "verdict": "FAIL"}},
            final_decision={"status": "FAIL", "output_3_pass": False},
            authoritative_write_authorized=False,
        )
        # Attempt 3: Score 60
        eval_3 = QAEvaluationResult(
            passed=False,
            qa_report={"failures": ["Minor error 3"]},
            judge_report={"output_3_evaluation": {"composite_score": 60.0, "verdict": "FAIL"}},
            final_decision={"status": "FAIL", "output_3_pass": False},
            authoritative_write_authorized=False,
        )

        evals = [eval_1, eval_2, eval_3]
        mock_qa = MagicMock()
        mock_qa.evaluate.side_effect = lambda *a, attempt=1, **kw: evals[min(attempt - 1, 2)]

        mock_synth = MagicMock()
        mock_synth.synthesize.side_effect = lambda *a, attempt=1, **kw: SynthesisResult(
            output_3=f'{{"attempt": {attempt}}}', attempt=attempt
        )

        with patch("dual_pipeline.canonical_pipeline.lifecycle.get_synthesis_strategy", return_value=mock_synth), \
             patch("dual_pipeline.canonical_pipeline.lifecycle.get_qa_strategy", return_value=mock_qa), \
             patch("dual_pipeline.canonical_pipeline.attempt_binding.verify_attempt_binding"):

            attempts_run, synth_out, eval_out = run_synthesis_loop(
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                provider=MagicMock(),
                provider_type="mock",
                model_name="gpt-6-luna",
                reasoning_effort="high",
                source_text="Source text",
                pa_context={"metadata": metadata},
                metadata=metadata,
                out_p=self.out_p,
                start_t=0.0,
                max_judge_retries=2,
            )

            # All 3 attempts executed
            self.assertEqual(attempts_run, 3)
            # Evaluated score returned must be restored Attempt 1 (66.0) rather than degraded Attempt 3 (60.0)
            score = eval_out.judge_report["output_3_evaluation"]["composite_score"]
            self.assertEqual(score, 66.0)
            self.assertIn('"attempt": 1', synth_out.output_3)

    def test_04_stage03_reasoning_effort_preservation_in_prompt_prefix_aligner(self) -> None:
        """
        Category: Boundary payload & configuration integrity
        Verifies that Stage 03 respects reasoning_effort='high' and allocates 8192 budget tokens
        instead of hardcoding effort to 'low' and budget to 2048.
        """
        sample_text = "**Speaker 1**: Dialogue turn with detailed system architecture explanation.\n" * 250  # ~2000 words

        # 1. Explicit high reasoning effort in metadata
        budget_high = PromptPrefixAligner.calculate_reasoning_token_budget(
            sample_text,
            metadata={"reasoning_effort": "high"},
            stage="stage_03",
        )
        self.assertEqual(budget_high["reasoning_effort"], "high")
        self.assertEqual(budget_high["budget_tokens"], 8192)
        self.assertEqual(budget_high["tier"], "DEEP_STRUCTURED")

        # 2. Stage-specific override in metadata
        budget_stage03 = PromptPrefixAligner.calculate_reasoning_token_budget(
            sample_text,
            metadata={"stage03_reasoning_effort": "medium"},
            stage="stage_03",
        )
        self.assertEqual(budget_stage03["reasoning_effort"], "medium")
        self.assertEqual(budget_stage03["budget_tokens"], 4096)

        # 3. Environment fallback when metadata is empty
        with patch.dict(os.environ, {"REASONING_EFFORT": "high"}):
            budget_env = PromptPrefixAligner.calculate_reasoning_token_budget(
                sample_text,
                metadata={},
                stage="stage_03",
            )
            self.assertEqual(budget_env["reasoning_effort"], "high")
            self.assertEqual(budget_env["budget_tokens"], 8192)


if __name__ == "__main__":
    unittest.main()
