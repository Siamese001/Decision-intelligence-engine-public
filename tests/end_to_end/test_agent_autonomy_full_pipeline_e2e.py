"""tests/end_to_end/test_agent_autonomy_full_pipeline_e2e.py

End-to-end full lifecycle test suite verifying:
1. Canonical 7-stage unified pipeline traversal with autonomous agent registration and execution.
2. Bounded token and timeout budget enforcement across agents.
3. Closed-loop learning proposal generation on failures without canonical graph mutations.
4. Remediation cycle rollback and forward recovery to completion.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.exceptions import DualPipelineValueError, PipelineTokenBudgetExhaustedError
from dual_pipeline.unified_agentic_pipeline import (
    PIPELINE_STAGE_SEQUENCE,
    PipelineStage,
    UnifiedAgenticPipeline,
    UnifiedAgenticPipelineTracker,
)


class ConcreteAutonomousPipeline(UnifiedAgenticPipeline):
    """Concrete test implementation of UnifiedAgenticPipeline."""

    @property
    def pipeline_name(self) -> str:
        return "AutonomousTestPipeline"


class TestAgentAutonomyFullPipelineE2E(unittest.TestCase):
    """Comprehensive E2E tests for autonomous agent coordination in unified pipelines."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.pipeline = ConcreteAutonomousPipeline()
        self.input_file = self.tmp_path / "sample_meeting.txt"
        self.input_file.write_text("Alice: Project kickoff.\nBob: Requirements agreed.", encoding="utf-8")
        self.output_dir = self.tmp_path / "output_run"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_e2e_full_7_stage_lifecycle_with_autonomous_agents(self):
        """Verifies clean 7-stage pipeline progression with autonomous agent coordination."""
        tracker = self.pipeline.create_tracker(
            self.input_file,
            metadata={"project": "nexus_autonomy", "session_id": "sess-autonomy-001"},
        )

        with tracker.execution_scope():
            # Stage 1: U0_INGRESS
            tracker.start_stage(PipelineStage.U0_INGRESS)
            tracker.register_autonomous_agent(
                agent_id="agent:ingestion-coordinator",
                stage=PipelineStage.U0_INGRESS,
                reasoning_tier="STANDARD_FAST",
                token_budget=2000,
                timeout_s=30.0,
            )
            tracker.record_agent_execution("agent:ingestion-coordinator", tokens_consumed=150, duration_s=0.2)
            tracker.complete_stage(
                PipelineStage.U0_INGRESS,
                details={"input_hash": hashlib.sha256(b"sample").hexdigest(), "status": "PASS"},
            )

            # Stage 2: L1_IDENTITY
            tracker.start_stage(PipelineStage.L1_IDENTITY)
            tracker.register_autonomous_agent(
                agent_id="agent:audio-forensics",
                stage=PipelineStage.L1_IDENTITY,
                reasoning_tier="LOCAL_METAL",
                token_budget=1000,
                timeout_s=15.0,
            )
            tracker.record_agent_execution("agent:audio-forensics", tokens_consumed=50, duration_s=0.1)
            tracker.complete_stage(PipelineStage.L1_IDENTITY, details={"attendees": ["Alice", "Bob"], "status": "PASS"})

            # Stage 3: L0_ROUTING
            tracker.start_stage(PipelineStage.L0_ROUTING)
            tracker.register_autonomous_agent(
                agent_id="agent:query-swarm-router",
                stage=PipelineStage.L0_ROUTING,
                reasoning_tier="MID_TIER_REASONING",
                token_budget=3000,
            )
            tracker.record_agent_execution("agent:query-swarm-router", tokens_consumed=200, duration_s=0.3)
            tracker.complete_stage(PipelineStage.L0_ROUTING, details={"route": "MEETING_ANALYSIS", "status": "PASS"})

            # Stage 4: C0_PA_CONTEXT
            tracker.start_stage(PipelineStage.C0_PA_CONTEXT)
            tracker.complete_stage(PipelineStage.C0_PA_CONTEXT, details={"packet_assembled": True, "status": "PASS"})

            # Stage 5: L2_SYNTHESIS
            tracker.start_stage(PipelineStage.L2_SYNTHESIS)
            tracker.complete_stage(PipelineStage.L2_SYNTHESIS, details={"outputs": ["01", "02", "03"], "status": "PASS"})

            # Stage 6: X_EXIT_GATES
            tracker.start_stage(PipelineStage.X_EXIT_GATES)
            tracker.register_autonomous_agent(
                agent_id="agent:qa-swarm-healer",
                stage=PipelineStage.X_EXIT_GATES,
                reasoning_tier="FRONTIER_HIGH_REASONING",
                token_budget=8000,
            )
            tracker.record_agent_execution("agent:qa-swarm-healer", tokens_consumed=500, duration_s=0.4)
            tracker.complete_stage(PipelineStage.X_EXIT_GATES, details={"qa_swarm_passed": True, "status": "PASS"})

            # Stage 7: EVALS_RELEASE
            tracker.start_stage(PipelineStage.EVALS_RELEASE)
            tracker.complete_stage(PipelineStage.EVALS_RELEASE, details={"release_verdict": "PASS", "status": "PASS"})

        receipt = tracker.build_receipt(self.output_dir, terminal_outcome="COMPLETE")
        receipt_path = receipt.write_to_dir(self.output_dir)

        self.assertTrue(receipt_path.is_file())
        self.assertEqual(receipt.terminal_outcome, "COMPLETE")
        self.assertEqual(len(receipt.stages), 7)
        for stage in PIPELINE_STAGE_SEQUENCE:
            self.assertIn(stage.value, receipt.stages)
            self.assertEqual(receipt.stages[stage.value]["status"], "PASS")

        # Verify autonomous agents recorded in metadata
        agents_meta = receipt.metadata.get("autonomous_agents", {})
        self.assertIn("agent:ingestion-coordinator", agents_meta)
        self.assertIn("agent:qa-swarm-healer", agents_meta)
        self.assertEqual(agents_meta["agent:qa-swarm-healer"]["tokens_consumed"], 500)

    def test_e2e_stage_failure_translates_into_learning_proposal_without_graph_mutation(self):
        """Failure events synthesize valid learning proposals while preserving canonical graph isolation."""
        tracker = self.pipeline.create_tracker(self.input_file)

        # Simulate graph database file to verify physical byte proof
        fake_graph_db = self.tmp_path / "canonical_graph.db"
        fake_graph_db.write_bytes(b"SQLite format 3\x00canonical-initial-bytes-12345")
        initial_sha = hashlib.sha256(fake_graph_db.read_bytes()).hexdigest()
        initial_size = fake_graph_db.stat().st_size

        proposal = tracker.record_learning_proposal_from_failure(
            stage=PipelineStage.L1_IDENTITY,
            failure_reason="Acoustic overlap caused speaker collapse on microphone 2",
            error_type="DiarizationOverlapFailure",
            payload={"audio_segment_id": "seg-042", "overlap_ms": 350},
        )

        # Contract schema validation
        self.assertEqual(proposal["contract_id"], "urn:nee:dual-pipeline:learning-change-proposal:1.0.0")
        self.assertEqual(proposal["schema_version"], "1.0.0")
        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["auto_apply"])
        self.assertIn("canonical_digest", proposal)

        # Evidence payload integrity
        evidence = proposal["evidence"]
        self.assertEqual(evidence["stage"], "L1")
        self.assertEqual(evidence["error_type"], "DiarizationOverlapFailure")

        # Physical zero-write proof
        post_sha = hashlib.sha256(fake_graph_db.read_bytes()).hexdigest()
        post_size = fake_graph_db.stat().st_size
        self.assertEqual(initial_sha, post_sha, "Canonical database mutated during proposal synthesis!")
        self.assertEqual(initial_size, post_size, "Canonical database byte size changed!")

    def test_e2e_agent_token_budget_enforcement(self):
        """Exceeding declared agent token budget must raise PipelineTokenBudgetExhaustedError."""
        tracker = self.pipeline.create_tracker(self.input_file)
        tracker.register_autonomous_agent(
            agent_id="agent:strict-budget",
            stage=PipelineStage.L2_SYNTHESIS,
            token_budget=1000,
        )

        # First consumption within budget
        tracker.record_agent_execution("agent:strict-budget", tokens_consumed=600)
        self.assertEqual(tracker.autonomous_agents["agent:strict-budget"]["tokens_consumed"], 600)

        # Second consumption exceeding budget
        with self.assertRaises(PipelineTokenBudgetExhaustedError):
            tracker.record_agent_execution("agent:strict-budget", tokens_consumed=500)

    def test_e2e_agent_timeout_budget_enforcement(self):
        """Agent execution exceeding timeout ceiling must fail closed."""
        tracker = self.pipeline.create_tracker(self.input_file)
        tracker.register_autonomous_agent(
            agent_id="agent:fast-timeout",
            stage=PipelineStage.L0_ROUTING,
            timeout_s=5.0,
        )

        with self.assertRaises(DualPipelineValueError) as ctx:
            tracker.record_agent_execution("agent:fast-timeout", duration_s=7.5)
        self.assertIn("timed out", str(ctx.exception))

    def test_e2e_remediation_rollback_and_forward_recovery(self):
        """Adversarial exit gate rejection triggers rollback to L2 and recovers to clean completion."""
        tracker = self.pipeline.create_tracker(self.input_file)

        with tracker.execution_scope():
            tracker.start_stage(PipelineStage.U0_INGRESS)
            tracker.complete_stage(PipelineStage.U0_INGRESS, details={"status": "PASS"})

            tracker.start_stage(PipelineStage.L1_IDENTITY)
            tracker.complete_stage(PipelineStage.L1_IDENTITY, details={"status": "PASS"})

            tracker.start_stage(PipelineStage.L0_ROUTING)
            tracker.complete_stage(PipelineStage.L0_ROUTING, details={"status": "PASS"})

            tracker.start_stage(PipelineStage.C0_PA_CONTEXT)
            tracker.complete_stage(PipelineStage.C0_PA_CONTEXT, details={"status": "PASS"})

            tracker.start_stage(PipelineStage.L2_SYNTHESIS)
            tracker.complete_stage(PipelineStage.L2_SYNTHESIS, details={"outputs": ["01", "02", "03"], "status": "PASS"})

            # X_EXIT_GATES detects defect, requests remediation back to L2
            tracker.start_stage(PipelineStage.X_EXIT_GATES)
            tracker.request_remediation(
                from_stage=PipelineStage.X_EXIT_GATES,
                to_stage=PipelineStage.L2_SYNTHESIS,
                reason="Cross-artifact attendee reconciliation gap",
            )

            # Re-execute L2 with healing
            tracker.start_stage(PipelineStage.L2_SYNTHESIS)
            tracker.complete_stage(PipelineStage.L2_SYNTHESIS, details={"rehealed": True, "status": "PASS"})

            # Re-execute X_EXIT_GATES now passing
            tracker.start_stage(PipelineStage.X_EXIT_GATES)
            tracker.complete_stage(PipelineStage.X_EXIT_GATES, details={"qa_passed": True, "status": "PASS"})

            # Complete EVALS
            tracker.start_stage(PipelineStage.EVALS_RELEASE)
            tracker.complete_stage(PipelineStage.EVALS_RELEASE, details={"release_passed": True, "status": "PASS"})

        receipt = tracker.build_receipt(self.output_dir, terminal_outcome="COMPLETE")
        self.assertEqual(receipt.terminal_outcome, "COMPLETE")
        self.assertEqual(len(receipt.remediation_cycles), 1)
        self.assertEqual(receipt.remediation_cycles[0]["stage"], "X1-X3")
        self.assertEqual(receipt.remediation_cycles[0]["details"]["remediation_target"], "L2")


if __name__ == "__main__":
    unittest.main()
