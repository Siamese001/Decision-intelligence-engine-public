"""Tests for unified_pipeline canonical package alias and facade.

Verifies that `unified_pipeline` serves as a complete, transparent, full-fidelity
SSOT replacement for `dual_pipeline`.
"""

import unittest


class TestUnifiedPipelinePackageAlias(unittest.TestCase):
    """Verify package alias exports and dynamic resolution."""

    def test_top_level_symbol_exports(self) -> None:
        """Top-level symbols exported by unified_pipeline match dual_pipeline."""
        import dual_pipeline.document_pipeline_core.pipeline as ddp
        import dual_pipeline.transcript_pipeline_core.pipeline as dtp
        import dual_pipeline.unified_agentic_pipeline as duap
        import unified_pipeline as up

        self.assertIs(up.UnifiedAgenticPipeline, duap.UnifiedAgenticPipeline)
        self.assertIs(up.PipelineStage, duap.PipelineStage)
        self.assertIs(up.PipelineSequencingViolationError, duap.PipelineSequencingViolationError)
        self.assertIs(up.PipelineExecutionReceipt, duap.PipelineExecutionReceipt)
        self.assertIs(up.UnifiedAgenticPipelineTracker, duap.UnifiedAgenticPipelineTracker)
        self.assertIs(up.RawTranscriptPipeline, dtp.RawTranscriptPipeline)
        self.assertIs(up.DocumentPipeline, ddp.DocumentPipeline)

    def test_pipeline_stage_enum_invariants(self) -> None:
        """Verify the 7-stage DAG is accessible through unified_pipeline."""
        import unified_pipeline as up

        stages = [
            up.PipelineStage.U0_INGRESS,
            up.PipelineStage.L1_IDENTITY,
            up.PipelineStage.L0_ROUTING,
            up.PipelineStage.C0_PA_CONTEXT,
            up.PipelineStage.L2_SYNTHESIS,
            up.PipelineStage.X_EXIT_GATES,
            up.PipelineStage.EVALS_RELEASE,
        ]
        self.assertEqual(len(stages), 7)
        self.assertEqual(up.PipelineStage.U0_INGRESS.value, "U0")
        self.assertEqual(up.PipelineStage.EVALS_RELEASE.value, "EVALS")

    def test_submodule_dynamic_import(self) -> None:
        """Submodules under unified_pipeline.* resolve to dual_pipeline.* equivalents."""
        import unified_pipeline.unified_agentic_pipeline as uap

        import dual_pipeline.unified_agentic_pipeline as duap

        self.assertIs(uap.UnifiedAgenticPipeline, duap.UnifiedAgenticPipeline)

    def test_tracker_lifecycle_from_unified_import(self) -> None:
        """UnifiedAgenticPipelineTracker instantiates and transitions through stages."""
        from unified_pipeline import PipelineStage, UnifiedAgenticPipelineTracker

        tracker = UnifiedAgenticPipelineTracker(
            pipeline_name="UnifiedTestPipeline",
            input_path="test_source.txt",
        )
        tracker.start_stage(PipelineStage.U0_INGRESS)
        tracker.complete_stage(PipelineStage.U0_INGRESS)
        self.assertIn("U0", tracker.stage_records)
        self.assertEqual(tracker.stage_records["U0"].status, "PASS")

    def test_dir_includes_dual_pipeline_exports(self) -> None:
        """__dir__() includes all __all__ members."""
        import unified_pipeline as up

        available = dir(up)
        for sym in [
            "UnifiedAgenticPipeline",
            "RawTranscriptPipeline",
            "DocumentPipeline",
            "PipelineStage",
            "QueryEngine",
            "IntakeEngine",
        ]:
            self.assertIn(sym, available)


if __name__ == "__main__":
    unittest.main()
