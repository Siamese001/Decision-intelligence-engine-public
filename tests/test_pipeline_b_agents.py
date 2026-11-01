"""Unit and Integration Tests for Pipeline B Extraction Agents (Wave 3).

Tests:
1. Agent B1 (TechArchExtractor) architectural extraction.
2. Agent B2 (StrategicCommitmentExtractor) milestone extraction.
3. Agent B3 (StakeholderDynamicsAgent) local power dynamics & compute posture.
4. Agent B4 (FrictionBlockerAgent) local blocker detection & compute posture.
5. Agent B5 (AdjudicationJudgeAgent) Law 5 soft quarantine evaluation.
6. Agent B0 (ExtractionCoordinator) Law 1 parallel compute fork.
"""
from __future__ import annotations

import unittest

from dual_pipeline.a2a import (
    A2ATask,
    A2ATaskStatus,
    ComputePosture,
)
from dual_pipeline.model_registry import ModelTier, get_binding
from pipeline_b_extraction.agents import (
    AdjudicationJudgeAgent,
    ExtractionCoordinator,
    FrictionBlockerAgent,
    StakeholderDynamicsAgent,
    StrategicCommitmentExtractor,
    TechArchExtractor,
)


class TestPipelineBAgents(unittest.IsolatedAsyncioTestCase):
    """Verifies Pipeline B specialized extraction agents and compute fork."""

    async def test_tech_arch_agent(self) -> None:
        agent = TechArchExtractor(mock_mode=True)
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.assertEqual(agent.card.compute_posture, ComputePosture.CLIENT_SAFE)
        self.assertEqual(agent.card.model_binding.model_id, binding.model_id)
        self.assertEqual(agent.card.model_binding.reasoning_effort, binding.reasoning_effort)

        task = A2ATask(
            task_id="t-b1",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Extract tech arch",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": "ev-01", "text": "We are deploying AWS S3 with MLX.", "speaker": "Alex"},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(res["extracted_count"], 1)
        self.assertEqual(res["nodes"][0]["type"], "ARCHITECTURAL_DECISION")

    async def test_strategic_commitments_agent(self) -> None:
        agent = StrategicCommitmentExtractor(mock_mode=True)
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.assertEqual(agent.card.compute_posture, ComputePosture.CLIENT_SAFE)
        self.assertEqual(agent.card.model_binding.model_id, binding.model_id)

        task = A2ATask(
            task_id="t-b2",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Extract commitments",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": "ev-02", "text": "Sprint 1 finishes June 16.", "speaker": "Alex"},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(res["extracted_count"], 1)
        self.assertEqual(res["nodes"][0]["type"], "DELIVERY_MILESTONE")

    async def test_stakeholder_dynamics_agent(self) -> None:
        agent = StakeholderDynamicsAgent(mock_mode=True)
        # Law 1 & Law 2 enforcement: must be INTERNAL_ADVISORY
        self.assertEqual(agent.card.compute_posture, ComputePosture.INTERNAL_ADVISORY)
        self.assertEqual(agent.card.model_binding.provider, "mlx")

        task = A2ATask(
            task_id="t-b3",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Extract stakeholder dynamics",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": "ev-03", "text": "I will not approve this S3 design.", "speaker": "Sponsor"},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(res["extracted_count"], 1)
        self.assertEqual(res["nodes"][0]["category"], "DECISION_INFLUENCE")

    async def test_friction_blocker_agent(self) -> None:
        agent = FrictionBlockerAgent(mock_mode=True)
        # Law 1 & Law 2 enforcement: must be INTERNAL_ADVISORY
        self.assertEqual(agent.card.compute_posture, ComputePosture.INTERNAL_ADVISORY)

        task = A2ATask(
            task_id="t-b4",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Detect blockers",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": "ev-04", "text": "We are blocked by IAM roles.", "speaker": "Engineer"},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(res["extracted_count"], 1)
        self.assertEqual(res["nodes"][0]["category"], "BLOCKER")

    async def test_adjudication_judge_agent(self) -> None:
        agent = AdjudicationJudgeAgent(mock_mode=True)
        canonical = [
            {"name": "Valid Arch", "tacticality": 2.0, "strategic_value": 9.0, "confidence": 0.95},
            {"name": "Tactical Calendar Invite", "tacticality": 9.0, "strategic_value": 2.0, "confidence": 0.90},
            {"name": "Ungrounded Rumor", "tacticality": 3.0, "strategic_value": 7.0, "confidence": 0.40},
        ]
        task = A2ATask(
            task_id="t-b5",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Adjudicate soft quarantine",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"canonical_nodes": canonical, "advisory_nodes": []},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        adjudicated = res["adjudicated_canonical"]
        self.assertFalse(adjudicated[0]["is_quarantined"])
        self.assertTrue(adjudicated[1]["is_quarantined"])
        self.assertEqual(adjudicated[1]["quarantine_reason"], "TACTICAL_LOW_STRATEGIC_VALUE")
        self.assertTrue(adjudicated[2]["is_quarantined"])
        self.assertEqual(adjudicated[2]["quarantine_reason"], "LOW_CONFIDENCE_UNGROUNDED")

    async def test_extraction_coordinator_law1_fork(self) -> None:
        coordinator = ExtractionCoordinator(mock_mode=True)
        task = A2ATask(
            task_id="t-b0-coord",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="Full extraction across compute fork",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "evidence_id": "ev-e2e-100",
                "text": "PartnerAdvisory pod commits to deploy AWS S3 pipeline by 06/16/2026, though IAM permissions remain a blocker.",
                "speaker": "Tony Kablan",
            },
        )
        res = await coordinator.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(res["canonical_count"], 2)
        self.assertGreaterEqual(res["advisory_count"], 2)
        self.assertIn("l1_plan", res)
        l1_plan = res["l1_plan"]
        self.assertEqual(l1_plan.model_binding["model_id"], "gpt-5.6-luna")
        self.assertEqual(l1_plan.model_binding["reasoning_effort"], "high")
        self.assertTrue(l1_plan.sanitization_passed)

    async def test_l1_extraction_cognition_advisory_posture(self) -> None:
        from pipeline_b_extraction.agents import L1ExtractionCognition
        l1 = L1ExtractionCognition()
        advisory_text = "Tony has an unstated career wedge with the VP and plans to leverage asymmetric leverage for promotion."
        plan = l1.plan("ev-adv-01", advisory_text, speaker="Alex")
        self.assertEqual(plan.compute_posture, ComputePosture.INTERNAL_ADVISORY)
        self.assertTrue(plan.has_advisory_leverage)
        self.assertIn("stakeholder_dynamics", plan.active_agents)
        self.assertEqual(plan.model_binding["model_id"], "gpt-5.6-luna")

    async def test_extraction_coordinator_sanitization_fail_closed(self) -> None:
        coordinator = ExtractionCoordinator(mock_mode=True)
        task = A2ATask(
            task_id="t-b0-fail-closed",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="Extract from toxic snippet",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "evidence_id": "ev-toxic",
                "text": "Secret AWS key: AKIA_REDACTED_EXAMPLE used in deployment",
                "speaker": "Dev",
            },
        )
        with self.assertRaises(ValueError) as ctx:
            await coordinator.handle_task(task)
        self.assertIn("L0 ROUTING BLOCKED: L1 extraction sanitization failed", str(ctx.exception))

    async def test_canonical_signal_extractor_single_pass(self) -> None:
        """Verifies unified single-pass extraction across all 4 operational facets."""
        from pipeline_b_extraction.canonical_extractor import CanonicalSignalExtractor
        from storage.database import EvidenceRecord

        extractor = CanonicalSignalExtractor(mock_mode=True)
        evidence = EvidenceRecord(
            evidence_id="ev_unified_001",
            meeting_id="meet_u1",
            speaker="Sarah",
            text="We face a high concurrency latency risk on the enterprise backbone before Q3 milestone.",
            start_timestamp=0.0,
            end_timestamp=5.0,
            is_final=True,
        )

        res = await extractor.extract_async(evidence)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(len(res.canonical_nodes), 3)
        self.assertGreaterEqual(len(res.canonical_edges), 2)
        self.assertGreaterEqual(len(res.assertions), 1)

        types = {n.type for n in res.canonical_nodes}
        self.assertIn("SYSTEM", types)
        self.assertIn("STRATEGIC_COMMITMENT", types)
        self.assertIn("STAKEHOLDER", types)
        self.assertIn("RISK", types)


if __name__ == "__main__":
    unittest.main()


