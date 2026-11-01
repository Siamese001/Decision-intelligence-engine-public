"""
tests/end_to_end/test_checkpoint_memoization_e2e.py

End-to-End tests verifying Output 01/02 checkpoint memoization across late
graph admission failures, zero re-tokenization, and synthesis loop circuit breaking.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    check_circuit_breaker,
    memoize_substage_checkpoints,
)
from dual_pipeline.canonical_pipeline.lifecycle import run_synthesis_loop
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
    SynthesisResult,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker


class CheckpointMemoizationE2ETests(unittest.TestCase):
    """E2E suite verifying sub-stage memoization and futile loop termination."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test_memoize_e2e_")
        self.out_p = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_01_substage_checkpoints_memoized_and_reused_on_graph_failure(self) -> None:
        """Verify Output 01 and 02 are reused without re-execution when graph admission fails."""
        # 1. Setup prior attempt_1 where Output 1 and 2 passed, but Output 3 failed
        att_dir = self.out_p / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True, exist_ok=True)

        expected_o1 = "# Transcript\nSpeaker 1: Project LENS tokenomics optimization discussion." + ("\nDetail: " * 50)
        expected_o2 = "# Executive Recap\nSummary of LENS tokenomics spend optimization." + ("\nKey takeaway: " * 30)

        (att_dir / "output_1.ckpt.md").write_text(expected_o1, encoding="utf-8")
        (att_dir / "output_2.ckpt.md").write_text(expected_o2, encoding="utf-8")
        (att_dir / "output_3.ckpt.json").write_text("{}", encoding="utf-8")

        decision = {
            "status": "FAIL",
            "authoritative_write_authorized": False,
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": False,
        }
        (att_dir / "final_release_decision.json").write_text(json.dumps(decision), encoding="utf-8")

        # 2. Run memoization in Run 2 (retry after late graph failure)
        sm = SelectiveRemediationStateMachine(max_attempts=3)
        metadata = {
            "project": "internal_consulting",
            "meeting_name": "LENS_Tokenomics_Review",
            "graph_only_retry": True,
        }
        res = memoize_substage_checkpoints(self.out_p, sm, metadata, CanonicalModality.MEETING_TRANSCRIPT)

        self.assertTrue(res["output_1"], "Output 1 must be memoized from prior attempt.")
        self.assertTrue(res["output_2"], "Output 2 must be memoized from prior attempt.")
        self.assertTrue(sm.is_frozen("output_1"))
        self.assertTrue(sm.is_frozen("output_2"))
        self.assertEqual(sm.get_frozen_content("output_1"), expected_o1)
        self.assertEqual(sm.get_frozen_content("output_2"), expected_o2)

        # 3. Verify synthesis strategy reuses frozen content without invoking executors
        mock_forensic = MagicMock()
        mock_recap = MagicMock()
        mock_graph = MagicMock()
        mock_graph.execute.return_value = '{"nodes": [{"id": "N1"}]}'

        strategy = FullMeetingSynthesisStrategy(
            provider=MagicMock(),
            l2_forensic=mock_forensic,
            l2_recap=mock_recap,
            l2_graph=mock_graph,
        )

        pa_context = {"output_dir": str(self.out_p), "state_machine": sm, "metadata": metadata}
        synth_res = strategy.synthesize(
            source_text="Raw source text that should not be re-tokenized for 01 or 02.",
            pa_context=pa_context,
            metadata=metadata,
            attempt=1,
        )

        mock_forensic.execute.assert_not_called()
        mock_recap.execute.assert_not_called()
        self.assertEqual(synth_res.output_1, expected_o1)
        self.assertEqual(synth_res.output_2, expected_o2)

    def test_02_graph_only_admission_retry_flag_preserves_checkpoints_over_force(self) -> None:
        """Verify admission_retry flag protects intermediate checkpoints from force_rerun."""
        att_dir = self.out_p / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True, exist_ok=True)

        content_1 = "Valid transcript content with sufficient length." * 10
        content_2 = "Valid executive recap content with sufficient length." * 10
        (att_dir / "output_1.ckpt.md").write_text(content_1, encoding="utf-8")
        (att_dir / "output_2.ckpt.md").write_text(content_2, encoding="utf-8")

        sm = SelectiveRemediationStateMachine(max_attempts=3)
        metadata = {
            "project": "internal_consulting",
            "force_rerun": True,
            "retry_scope": "graph_only",
        }
        res = memoize_substage_checkpoints(self.out_p, sm, metadata, CanonicalModality.MEETING_TRANSCRIPT)

        self.assertTrue(res["output_1"])
        self.assertTrue(res["output_2"])
        self.assertTrue(sm.is_frozen("output_1"))

    def test_03_deliberate_reset_skips_memoization(self) -> None:
        """Verify deliberate full reset bypasses memoization and forces clean rerun."""
        att_dir = self.out_p / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "output_1.ckpt.md").write_text("Prior text" * 20, encoding="utf-8")

        sm = SelectiveRemediationStateMachine(max_attempts=3)
        metadata = {
            "project": "internal_consulting",
            "force_rerun": True,  # Clean deliberate reset without retry flag
        }
        res = memoize_substage_checkpoints(self.out_p, sm, metadata, CanonicalModality.MEETING_TRANSCRIPT)

        self.assertFalse(res["output_1"])
        self.assertFalse(res["output_2"])
        self.assertFalse(sm.is_frozen("output_1"))

    def test_04_circuit_breaker_terminates_repeat_failure_synthesis_loop(self) -> None:
        """Verify run_synthesis_loop halts at attempt 2 when candidate produces identical consecutive failure."""
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        metadata = {
            "project": "internal_consulting",
            "circuit_breaker": cb,
            "output3_only": True,
        }

        # Mock QA strategy that always returns identical failure
        mock_qa_strategy = MagicMock()
        mock_eval = QAEvaluationResult(
            passed=False,
            qa_report={"failures": ["Permanent ontology schema violation"]},
            judge_report={"reasons": ["Permanent ontology schema violation"]},
            final_decision={"status": "FAIL", "output_3_pass": False},
            authoritative_write_authorized=False,
        )
        mock_qa_strategy.evaluate.return_value = mock_eval

        # Mock synthesis strategy returning slightly varied candidate each attempt
        mock_synth_strategy = MagicMock()
        mock_synth_strategy.synthesize.side_effect = lambda *a, attempt=1, **kw: SynthesisResult(
            output_3=f'{{"entities": [], "jitter": "{attempt}"}}', attempt=attempt
        )

        # Patch get_synthesis_strategy, get_qa_strategy, and verify_attempt_binding
        from unittest.mock import patch
        with patch("dual_pipeline.canonical_pipeline.lifecycle.get_synthesis_strategy", return_value=mock_synth_strategy), \
             patch("dual_pipeline.canonical_pipeline.lifecycle.get_qa_strategy", return_value=mock_qa_strategy), \
             patch("dual_pipeline.canonical_pipeline.attempt_binding.verify_attempt_binding"):

            attempts_run, synth_out, eval_out = run_synthesis_loop(
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                provider=MagicMock(),
                provider_type="mock",
                model_name="mock-model",
                reasoning_effort="low",
                source_text="Speaker 1: Test source text",
                pa_context={"metadata": metadata},
                metadata=metadata,
                out_p=self.out_p,
                start_t=0.0,
                max_judge_retries=2,
            )

            # Circuit breaker must have tripped on attempt 2
            self.assertEqual(cb.consecutive_failures, 2)
            self.assertTrue(cb.is_tripped())
            self.assertIn("identical consecutive", cb.trip_reason)


if __name__ == "__main__":
    unittest.main()
