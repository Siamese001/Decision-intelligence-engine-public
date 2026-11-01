"""
tests/test_refactored_orchestration_and_checkpoints.py

Unit tests verifying Wave 5 code refactoring:
1. DecisionOrchestrator Antigravity SDK bridge (to_antigravity_agent_config)
2. Roles taxonomy extraction (CANONICAL_18_ROLES)
3. UnifiedAgenticPipelineTracker checkpoint delegation via unified_agentic_checkpoint
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.decision.agents.decision_orchestrator import (
    DecisionOrchestrator,
)
from dual_pipeline.decision.agents.roles import CANONICAL_18_ROLES
from dual_pipeline.unified_agentic_pipeline import (
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)


class TestRefactoredOrchestrationAndCheckpoints(unittest.TestCase):
    def test_canonical_18_roles_taxonomy(self) -> None:
        self.assertEqual(len(CANONICAL_18_ROLES), 18)
        self.assertIn("Executive Sponsor", CANONICAL_18_ROLES)
        self.assertIn("Chief Architect", CANONICAL_18_ROLES)
        self.assertIn("FinOps Lead", CANONICAL_18_ROLES)

    def test_decision_orchestrator_antigravity_sdk_bridge(self) -> None:
        orchestrator = DecisionOrchestrator()
        config = orchestrator.to_antigravity_agent_config()
        self.assertIsNotNone(config)
        self.assertTrue(len(config.subagents) >= 3)
        names = [s.name for s in config.subagents]
        self.assertIn("ingestion_specialist", names)
        self.assertIn("retrieval_specialist", names)
        self.assertIn("spark_analytics_specialist", names)

    def test_checkpoint_delegation_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tracker = UnifiedAgenticPipelineTracker(
                pipeline_name="test_pipeline",
                input_path="input.txt",
            )
            tracker.start_stage(PipelineStage.U0_INGRESS)
            tracker.complete_stage(PipelineStage.U0_INGRESS, status="PASS", details={"items": 10})

            # Save checkpoint
            cp_path = tracker.save_checkpoint(
                PipelineStage.U0_INGRESS,
                payload={"items": 10},
                checkpoint_dir=tmp_dir,
            )
            self.assertTrue(Path(cp_path).is_file())

            # Load checkpoint
            loaded = tracker.load_checkpoint(PipelineStage.U0_INGRESS, checkpoint_dir=tmp_dir)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.get("items"), 10)

            # Check can resume
            can_resume = tracker.can_resume_from_stage(PipelineStage.L1_IDENTITY, checkpoint_dir=tmp_dir)
            self.assertTrue(can_resume)


if __name__ == "__main__":
    unittest.main()
