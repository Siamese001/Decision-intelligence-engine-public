"""Unit and Integration Tests for Pipeline A Ingestion Agents (Wave 2).

Tests:
1. Agent A1 (AudioForensicsAgent) acoustic segmentation.
2. Agent A2 (RosterReconcilerAgent) phonetic roster disambiguation.
3. Agent A3 (MultimodalSlideExtractor) diagram and slide extraction.
4. Agent A4 (ForensicNormalizerAgent) stutter cleanup and Artifact 01 rendering.
5. Agent A0 (IngestionCoordinator) parallel A2A fan-out and DAG progression.
"""
from __future__ import annotations

import unittest

from dual_pipeline.a2a import (
    A2ATask,
    A2ATaskStatus,
)
from pipeline_a_intake.agents import (
    AudioForensicsAgent,
    ForensicNormalizerAgent,
    IngestionCoordinator,
    MultimodalSlideExtractor,
    RosterReconcilerAgent,
    get_forensic_normalizer_card,
    get_forensic_transcript_healer_card,
    get_ingestion_coordinator_card,
    get_multimodal_slide_card,
    get_roster_reconciler_card,
)


class TestPipelineAAgents(unittest.IsolatedAsyncioTestCase):
    """Verifies Pipeline A specialized agents and coordinator."""

    async def test_audio_forensics_agent(self) -> None:
        agent = AudioForensicsAgent(mock_mode=True)
        task = A2ATask(
            task_id="t-audio-1",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Extract speaker turns",
            status=A2ATaskStatus.SUBMITTED,
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(len(res["speaker_turns"]), 2)
        self.assertIn("Alex Sanchez", [t["speaker"] for t in res["speaker_turns"]])

    async def test_roster_reconciler_agent(self) -> None:
        agent = RosterReconcilerAgent(mock_mode=True)
        turns = [
            {"speaker": "Alex", "text": "Testing audio turn", "start_ms": 0, "end_ms": 1000},
            {"speaker": "Mandy", "text": "Second turn", "start_ms": 1000, "end_ms": 2000},
        ]
        roster = ["Alex Sanchez", "harper Hoffman"]
        task = A2ATask(
            task_id="t-roster-1",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Reconcile names",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"speaker_turns": turns, "roster_context": roster},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        speakers = [t["speaker"] for t in res["reconciled_turns"]]
        self.assertIn("Alex Sanchez", speakers)
        self.assertIn("harper Hoffman", speakers)

    async def test_multimodal_slide_extractor(self) -> None:
        agent = MultimodalSlideExtractor(mock_mode=True)
        task = A2ATask(
            task_id="t-slide-1",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Extract slide claims",
            status=A2ATaskStatus.SUBMITTED,
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["slide_count"], 2)
        self.assertTrue(res["has_diagrams"])

    async def test_forensic_normalizer_agent(self) -> None:
        agent = ForensicNormalizerAgent(mock_mode=True)
        turns = [
            {
                "speaker": "Alex Sanchez",
                "start_ms": 0,
                "end_ms": 3000,
                "text": "Um, we we need to finalize the contract.",
            }
        ]
        task = A2ATask(
            task_id="t-norm-1",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Clean transcript and build Artifact 01",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "reconciled_turns": turns,
                "meeting_name": "Project LENS Sync",
                "meeting_date": "09/12/2026",
            },
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        md = res["artifact_01_markdown"]
        self.assertIn("Ambiguity Lock Register (ALR)", md)
        self.assertIn("Alex Sanchez", md)
        self.assertIn("[00:00]", md)
        # Verify stutter cleanup
        self.assertNotIn("we we", md)

    async def test_ingestion_coordinator_parallel_fanout(self) -> None:
        coordinator = IngestionCoordinator(mock_mode=True)
        task = A2ATask(
            task_id="t-coord-e2e",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="Ingest multimodal meeting",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "meeting_name": "Project Beacon Architecture Review",
                "meeting_date": "09/12/2026",
                "roster_context": ["Alex Sanchez", "harper Hoffman"],
            },
        )
        res = await coordinator.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(res["turn_count"], 2)
        self.assertIn("Ambiguity Lock Register (ALR)", res["artifact_01_markdown"])
        self.assertIn("l1_plan", res)
        l1_plan = res["l1_plan"]
        self.assertIn(l1_plan.model_binding["model_id"], ("gpt-5.6-sol", "gpt-5.6-luna"))
        self.assertIn(l1_plan.model_binding["reasoning_effort"], ("high", "max"))
        self.assertTrue(l1_plan.sanitization_passed)

    async def test_ingestion_coordinator_sanitization_fail_closed(self) -> None:
        coordinator = IngestionCoordinator(mock_mode=True)
        task = A2ATask(
            task_id="t-coord-fail-closed",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="Ingest toxic transcript",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "meeting_name": "Leak Attempt Meeting",
                "raw_transcript_text": "Here is the key: REDACTED_PRIVATE_KEY_HEADER\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----",
            },
        )
        with self.assertRaises(ValueError) as ctx:
            await coordinator.handle_task(task)
        self.assertIn("L0 ROUTING BLOCKED: L1 sanitization failed", str(ctx.exception))

    async def test_l1_fusion_dag_detection_live_and_multimodal(self) -> None:
        from dual_pipeline.transcript_stages import L1ForensicCognition
        l1 = L1ForensicCognition()

        # Test Live Stream -> FUSION_D
        ctx_live = {
            "clean_transcript": "Live audio stream test",
            "metadata": {"source_type": "LIVE_AUDIO", "is_live": True},
        }
        plan_live = l1.plan(ctx_live)
        self.assertEqual(plan_live.fusion_dag, "FUSION_D")
        self.assertIn(plan_live.model_binding["model_id"], ("gpt-5.6-sol", "gpt-5.6-luna"))

        # Test Audio + Deck -> FUSION_C
        ctx_deck = {
            "clean_transcript": "Recorded meeting with slides",
            "metadata": {
                "extensions": {
                    "com.nee.fusion_coordinator": {
                        "temporal_anchor": "recording.mp4",
                        "semantic_enhancers": ["presentation.pptx"],
                    }
                }
            },
        }
        plan_deck = l1.plan(ctx_deck)
        self.assertEqual(plan_deck.fusion_dag, "FUSION_C")

    def test_pipeline_a_gpt_5_6_sol_bindings(self) -> None:
        """Verifies Pipeline A agent cards and model bindings under governed model tiers."""
        from dual_pipeline.model_registry import ModelTier, get_binding
        binding_frontier = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        binding_healer = get_binding(ModelTier.SURGICAL_HEALER)

        coord_card = get_ingestion_coordinator_card()
        self.assertEqual(coord_card.model_binding.model_id, binding_frontier.model_id)
        self.assertIn(coord_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(coord_card.model_binding.temperature)

        normalizer_card = get_forensic_normalizer_card()
        self.assertEqual(normalizer_card.model_binding.model_id, binding_frontier.model_id)
        self.assertIn(normalizer_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(normalizer_card.model_binding.temperature)

        roster_card = get_roster_reconciler_card()
        self.assertEqual(roster_card.model_binding.model_id, binding_frontier.model_id)
        self.assertIn(roster_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(roster_card.model_binding.temperature)

        slide_card = get_multimodal_slide_card()
        self.assertEqual(slide_card.model_binding.model_id, binding_frontier.model_id)
        self.assertIn(slide_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(slide_card.model_binding.temperature)

        healer_card = get_forensic_transcript_healer_card()
        self.assertEqual(healer_card.model_binding.model_id, binding_healer.model_id)
        self.assertIn(healer_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(healer_card.model_binding.temperature)

    async def test_ingestion_coordinator_execute_direct_dag(self) -> None:
        """Verifies direct deterministic DAG execution path under gpt-5.6-sol medium."""
        coordinator = IngestionCoordinator(mock_mode=True)
        res = await coordinator.execute_direct_dag(
            meeting_name="Direct DAG E2E Sync",
            meeting_date="09/12/2026",
            raw_transcript_text="Alex Sanchez: System online and verified.",
            roster_context=["Alex Sanchez", "harper Hoffman"],
            model_id="gpt-5.6-sol",
            reasoning_effort="medium",
        )
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["execution_mode"], "DIRECT_DAG")
        self.assertEqual(res["model_id"], "gpt-5.6-sol")
        self.assertEqual(res["reasoning_effort"], "medium")
        self.assertGreaterEqual(res["turn_count"], 2)
        self.assertIn("Ambiguity Lock Register (ALR)", res["artifact_01_markdown"])
        self.assertIsNotNone(res["claim_check_pointer"])
        self.assertTrue(res["l1_plan"].sanitization_passed)


    async def test_pipeline_a_roster_hitl_20_percent_margin_gating(self) -> None:
        """Verifies Pipeline A speaker disambiguation enforces 20% margin gating and ledger recording under gpt-5.6-luna."""
        import tempfile
        from pathlib import Path

        from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
        from dual_pipeline.hitl_governance import HitlSurfacingMode
        from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "pipeline_a_ledger.json"
            arbiter = HitlArbiterAgent(
                ledger_path=ledger_path,
                surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
                margin_threshold=0.20,
                model_id="gpt-5.6-luna",
                reasoning_effort="high",
            )
            bridge = AntigravityUIBridge(
                arbiter=arbiter,
                mock_mode=True,
            )
            roster_agent = RosterReconcilerAgent(
                mock_mode=True,
                hitl_arbiter=arbiter,
                ui_bridge=bridge,
                model_id="gpt-5.6-luna",
                reasoning_effort="high",
            )

            # Speaker turn with multi-candidate roster match:
            # "Alex" matches "Alex Sanchez" (0.90) and "Alex B" (0.65) -> margin: 0.25 > 0.20
            # Under DIVERGENCE_GT_THRESHOLD: surfaces HITL and resolves via bridge, recording to ledger
            speaker_turns = [{"speaker": "Alex", "text": "Deploying the A2A mesh architecture."}]
            roster = ["Alex Sanchez", "Alex B", "John Doe"]

            reconciled = await roster_agent.reconcile_speakers(speaker_turns, roster)
            self.assertEqual(len(reconciled), 1)
            self.assertEqual(reconciled[0]["speaker"], "Alex Sanchez")

            # Verify decision persisted to ledger
            history = arbiter.store.get_history("roster_reconciliation")
            self.assertGreaterEqual(len(history), 1)
            self.assertEqual(history[0]["selected_key"], "person:alex_sanchez")


if __name__ == "__main__":
    unittest.main()


