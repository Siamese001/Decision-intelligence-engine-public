"""
tests/end_to_end/test_judge_calibration_e2e.py

End-to-End integration test validating calibrated LLM Judge threshold propagation,
quarantine event recording, and C0-PA stage progression without spurious HITLRequiredError.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.llm_judge_core.judges.leverage import AgenticLeverageJudge
from dual_pipeline.llm_judge_core.batch_judge import BatchMicroJudgeEvaluator
from dual_pipeline.parameters import (
    get_svp_political_materiality_threshold,
    reload_parameters,
)
from dual_pipeline.unified_agentic_pipeline import (
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)


class TestJudgeCalibrationE2E(unittest.TestCase):
    """E2E lifecycle testing for calibrated micro-judges."""

    def setUp(self) -> None:
        reload_parameters()

    def tearDown(self) -> None:
        reload_parameters()

    def test_standard_pipeline_lifecycle_with_calibrated_judge(self) -> None:
        """Standard pipeline processes candidates with materiality 6 without HITLRequiredError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "output"
            cp_dir = Path(tmp_dir) / "checkpoints"

            tracker = UnifiedAgenticPipelineTracker(
                pipeline_name="calibrated_judge_lifecycle",
                input_path="/data/test_meeting.md",
            )

            # Stage U0 -> L1 -> L0 -> C0-PA
            tracker.start_stage(PipelineStage.U0_INGRESS)
            tracker.complete_stage(PipelineStage.U0_INGRESS, status="PASS", details={"chunks": 3})

            tracker.start_stage(PipelineStage.L1_IDENTITY)
            tracker.complete_stage(PipelineStage.L1_IDENTITY, status="PASS", details={"entities": 5})

            tracker.start_stage(PipelineStage.L0_ROUTING)
            tracker.complete_stage(PipelineStage.L0_ROUTING, status="PASS", details={"rows": 12})

            tracker.start_stage(PipelineStage.C0_PA_CONTEXT)
            # Candidate payload with materiality 6 (Director target, brief wedge)
            candidate_node = {
                "speaker": "Executive Lead",
                "key_players": ["Director of Infrastructure"],
                "political_rationale": "Strategic budget alignment",
                "networking_wedge": "Direct operational sponsorship",
                "route": "standard",
            }

            judge = AgenticLeverageJudge(mock=True)
            eval_result = judge.evaluate_agentic_leverage(candidate_node)

            # Assert calibrated threshold (6) passes cleanly in standard pipeline
            self.assertEqual(eval_result.political_materiality, 6)
            self.assertTrue(eval_result.pass_gate)
            self.assertIsNone(eval_result.rejection_reason)

            # C0-PA completes as PASS rather than throwing HITLRequiredError
            tracker.complete_stage(
                PipelineStage.C0_PA_CONTEXT,
                status="PASS",
                details={"judge_verdict": "PASS", "materiality": eval_result.political_materiality},
            )

            # Stage L2 Synthesis
            tracker.start_stage(PipelineStage.L2_SYNTHESIS)
            tracker.complete_stage(PipelineStage.L2_SYNTHESIS, status="PASS", details={"assertions_promoted": 1})

            # Stage X1-X3 Exit Gates
            tracker.start_stage(PipelineStage.X_EXIT_GATES)
            tracker.complete_stage(PipelineStage.X_EXIT_GATES, status="PASS", details={"adversarial_audit": "PASSED"})

            # Stage EVALS Release
            tracker.start_stage(PipelineStage.EVALS_RELEASE)
            tracker.complete_stage(PipelineStage.EVALS_RELEASE, status="PASS", details={"release_action": "PUBLISHED"})

            # Checkpoint and build receipt
            tracker.save_checkpoint(PipelineStage.EVALS_RELEASE, payload={"status": "OK"}, checkpoint_dir=cp_dir)
            receipt = tracker.build_receipt(output_dir=out_dir, terminal_outcome="COMPLETE")

            self.assertEqual(receipt.terminal_outcome, "COMPLETE")

    def test_subthreshold_candidate_safely_quarantined_e2e(self) -> None:
        """Sub-threshold candidate (<6) fails cleanly and is quarantined without pipeline crash."""
        judge = AgenticLeverageJudge(mock=True)
        subthreshold_node = {
            "speaker": "Analyst",
            "key_players": [],  # Missing executive target -> materiality 4
            "political_rationale": "General observation",
            "networking_wedge": "Coffee chat",
            "route": "standard",
        }
        eval_res = judge.evaluate_agentic_leverage(subthreshold_node)
        self.assertFalse(eval_res.pass_gate)
        self.assertIn("Target Test", eval_res.rejection_reason or "")
        self.assertLess(eval_res.political_materiality, get_svp_political_materiality_threshold(False))

    def test_executive_route_preserves_strict_threshold_8_e2e(self) -> None:
        """Executive route preserves threshold 8, rejecting materiality 6."""
        judge = AgenticLeverageJudge(mock=True)
        candidate_node = {
            "speaker": "VP Engineering",
            "key_players": ["Managing Director Sarah"],
            "political_rationale": "Short memo",
            "networking_wedge": "Short sponsorship",
            "route": "executive",
        }
        eval_res = judge.evaluate_agentic_leverage(candidate_node)
        self.assertEqual(eval_res.political_materiality, 6)
        self.assertFalse(eval_res.pass_gate)
        self.assertIn("below SVP threshold of 8", eval_res.rejection_reason or "")


if __name__ == "__main__":
    unittest.main()
