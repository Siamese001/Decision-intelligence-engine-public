"""
tests/end_to_end/test_refactored_pipeline_lifecycle_e2e.py

End-to-End test validating full 7-stage lifecycle with checkpoint persistence
and receipt compilation on refactored unified pipeline primitives.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.unified_agentic_pipeline import (
    PIPELINE_STAGE_SEQUENCE,
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)


class TestRefactoredPipelineLifecycleE2E(unittest.TestCase):
    def test_full_lifecycle_with_checkpoints_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "output"
            cp_dir = Path(tmp_dir) / "checkpoints"

            tracker = UnifiedAgenticPipelineTracker(
                pipeline_name="governed_e2e_pipeline",
                input_path="/data/input_doc.md",
            )

            # Traverse all 7 stages and checkpoint each
            for stage in PIPELINE_STAGE_SEQUENCE:
                tracker.start_stage(stage)
                details = {"stage_name": stage.value, "metric": 42}
                tracker.complete_stage(stage, status="PASS", details=details)
                tracker.save_checkpoint(stage, payload=details, checkpoint_dir=cp_dir)

            # Build receipt
            receipt = tracker.build_receipt(output_dir=out_dir, terminal_outcome="COMPLETE")
            self.assertEqual(receipt.terminal_outcome, "COMPLETE")
            self.assertEqual(len(receipt.stages), 7)

            # Write receipt to disk
            receipt_path = receipt.write_to_dir(out_dir)
            self.assertTrue(receipt_path.is_file())

            # Verify resume capability from clean tracker
            fresh_tracker = UnifiedAgenticPipelineTracker(
                pipeline_name="governed_e2e_pipeline",
                input_path="/data/input_doc.md",
            )
            fresh_tracker.resume_to_stage(PipelineStage.L2_SYNTHESIS, checkpoint_dir=cp_dir)
            self.assertEqual(len(fresh_tracker.completed_stages), 4)
            self.assertIn(PipelineStage.C0_PA_CONTEXT, fresh_tracker.completed_stages)


if __name__ == "__main__":
    unittest.main()
