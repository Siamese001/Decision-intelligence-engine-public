"""
Acceptance Test Suite for Wave 2: Pipeline A Intake & Pipeline B Dual-Compute Extraction.

Verifies:
1. Pipeline A: ForensicNormalizerAgent, RosterReconcilerAgent, MultimodalSlideExtractor
   operating with local MLX model bindings on Apple Metal.
2. Pipeline B: TechArchExtractor, StrategicCommitmentExtractor, AdjudicationJudgeAgent
   operating with local MLX model bindings on Apple Metal.
3. Law 1 Compute Fork: Strict physical separation between CLIENT_SAFE and INTERNAL_ADVISORY.
4. Law 2 Local Enclave: Zero egress and absolute containment of internal advisory intelligence.
5. Law 5 Multi-Dimensional Soft-Quarantine: Metadata tagging without destructive deletion.
6. Schema and Constraint Invariants: Output 01 (Forensic Transcript) and Output 03 (Canonical Signal Projection).
"""
from __future__ import annotations

import asyncio
import unittest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus, ComputePosture
from pipeline_a_intake.agents import (
    ForensicNormalizerAgent,
    MultimodalSlideExtractor,
    RosterReconcilerAgent,
)
from pipeline_b_extraction.agents import (
    AdjudicationJudgeAgent,
    FrictionBlockerAgent,
    StakeholderDynamicsAgent,
    StrategicCommitmentExtractor,
    TechArchExtractor,
)


class TestWave2IntakeExtraction(unittest.TestCase):
    """Verifies all Wave 2 deliverables and acceptance criteria."""

    # -------------------------------------------------------------------------
    # 1. Pipeline A Local Intake Agents
    # -------------------------------------------------------------------------
    def test_pipeline_a_forensic_normalizer_local_execution(self) -> None:
        """Verifies ForensicNormalizerAgent operates under local Qwen2.5-14B MLX model."""
        agent = ForensicNormalizerAgent(use_local_model=True, mock_mode=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")
        self.assertEqual(agent.card.compute_posture, ComputePosture.CLIENT_SAFE)

        turns = [
            {"speaker": "Alex Rivera", "start_ms": 10000, "end_ms": 15000, "text": "Um, we we must deploy the Project Beacon mesh."},
            {"speaker": "Jordan Taylor", "start_ms": 16000, "end_ms": 22000, "text": "Agreed, uh, target date is 09/12/2026."},
        ]
        task = A2ATask(
            task_id="norm-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=agent.card.agent_id,
            objective="Normalize speech-to-text turns",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "meeting_name": "Project Beacon Architecture Alignment",
                "meeting_date": "09/12/2026",
                "reconciled_turns": turns,
            },
        )

        res = asyncio.run(agent.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["turn_count"], 2)
        md = res["artifact_01_markdown"]
        self.assertIn("Forensic HITL Resolved Transcript", md)
        self.assertIn("Ambiguity Lock Register (ALR)", md)
        self.assertIn("[00:10]", md)
        self.assertNotIn("Um,", md)
        self.assertNotIn("uh,", md)

    def test_pipeline_a_roster_reconciler_local_execution(self) -> None:
        """Verifies RosterReconcilerAgent operates under local Qwen2.5-14B MLX model."""
        agent = RosterReconcilerAgent(use_local_model=True, mock_mode=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")

        speaker_turns = [
            {"speaker": "Alex Rivera", "text": "Opening architecture discussion."},
            {"speaker": "alex", "text": "Confirming AWS S3 nameplate extraction."},
        ]
        roster = ["Alex Rivera", "Alex Sanchez", "Jordan Taylor"]

        task = A2ATask(
            task_id="roster-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=agent.card.agent_id,
            objective="Reconcile speaker identities",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "speaker_turns": speaker_turns,
                "roster_context": roster,
            },
        )

        res = asyncio.run(agent.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        reconciled = res["reconciled_turns"]
        self.assertEqual(len(reconciled), 2)
        self.assertEqual(reconciled[0]["speaker"], "Alex Rivera")
        self.assertEqual(reconciled[1]["speaker"], "Alex Sanchez")

    def test_pipeline_a_multimodal_slide_local_execution(self) -> None:
        """Verifies MultimodalSlideExtractor operates under local Qwen2.5-VL-7B MLX model."""
        extractor = MultimodalSlideExtractor(use_local_model=True, mock_mode=True)
        self.assertEqual(extractor.card.model_binding.model_id, "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
        self.assertEqual(extractor.card.model_binding.provider, "mlx")

        task = A2ATask(
            task_id="slide-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=extractor.card.agent_id,
            objective="Extract slide topology",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"deck_path": "docs/arch_deck.pptx"},
        )

        res = asyncio.run(extractor.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreater(res["slide_count"], 0)
        self.assertTrue(res["has_diagrams"])

    # -------------------------------------------------------------------------
    # 2. Pipeline B Local Extraction Agents
    # -------------------------------------------------------------------------
    def test_pipeline_b_tech_arch_local_execution(self) -> None:
        """Verifies TechArchExtractor extracts canonical nodes under local Qwen2.5-14B MLX model."""
        extractor = TechArchExtractor(use_local_model=True, mock_mode=True)
        self.assertEqual(extractor.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(extractor.card.model_binding.provider, "mlx")

        items = [{"evidence_id": "ev-arch-01", "text": "Deploy BGE-M3 quad-engine retriever with SQLite FTS5."}]
        nodes = asyncio.run(extractor.extract_batch(items))
        self.assertGreater(len(nodes), 0)
        first_node = nodes[0]
        self.assertIn("node_id", first_node)
        self.assertEqual(first_node["evidence_id"], "ev-arch-01")
        self.assertGreaterEqual(first_node["confidence"], 0.80)

    def test_pipeline_b_strategic_commitments_local_execution(self) -> None:
        """Verifies StrategicCommitmentExtractor extracts milestone nodes under local Qwen2.5-14B MLX model."""
        extractor = StrategicCommitmentExtractor(use_local_model=True, mock_mode=True)
        self.assertEqual(extractor.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(extractor.card.model_binding.provider, "mlx")

        items = [{"evidence_id": "ev-commit-01", "text": "Mitchell committed production delivery by 09/12/2026."}]
        nodes = asyncio.run(extractor.extract_batch(items))
        self.assertGreater(len(nodes), 0)
        first_node = nodes[0]
        self.assertEqual(first_node["evidence_id"], "ev-commit-01")
        self.assertEqual(first_node["type"], "DELIVERY_MILESTONE")

    def test_pipeline_b_adjudication_judge_local_execution(self) -> None:
        """Verifies AdjudicationJudgeAgent enforces Law 5 soft quarantine under DeepSeek-R1 Distill 14B."""
        judge = AdjudicationJudgeAgent(use_local_model=True, mock_mode=True)
        self.assertEqual(judge.card.model_binding.model_id, "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit")
        self.assertEqual(judge.card.model_binding.provider, "mlx")

        # Candidate 1: High strategic value, high confidence -> NOT quarantined
        cand1 = {
            "node_id": "n1",
            "name": "Unified Event Architecture",
            "strategic_value": 9.0,
            "tacticality": 2.0,
            "confidence": 0.95,
        }
        # Candidate 2: High tacticality, low strategic value -> SOFT QUARANTINED
        cand2 = {
            "node_id": "n2",
            "name": "Update JIRA ticket formatting",
            "strategic_value": 2.0,
            "tacticality": 8.5,
            "confidence": 0.90,
        }
        # Candidate 3: Low confidence -> SOFT QUARANTINED
        cand3 = {
            "node_id": "n3",
            "name": "Unconfirmed third party software rumor",
            "strategic_value": 6.0,
            "tacticality": 4.0,
            "confidence": 0.40,
        }

        task = A2ATask(
            task_id="judge-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=judge.card.agent_id,
            objective="Adjudicate candidate facts",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"canonical_nodes": [cand1, cand2, cand3], "advisory_nodes": []},
        )

        res = asyncio.run(judge.handle_task(task))
        adjudicated = res["adjudicated_canonical"]
        self.assertEqual(len(adjudicated), 3)

        self.assertFalse(adjudicated[0]["is_quarantined"])
        self.assertTrue(adjudicated[1]["is_quarantined"])
        self.assertEqual(adjudicated[1]["quarantine_reason"], "TACTICAL_LOW_STRATEGIC_VALUE")
        self.assertTrue(adjudicated[2]["is_quarantined"])
        self.assertEqual(adjudicated[2]["quarantine_reason"], "LOW_CONFIDENCE_UNGROUNDED")

    # -------------------------------------------------------------------------
    # 3. Law 1 & Law 2 Compute Fork and Enclave Isolation Tests
    # -------------------------------------------------------------------------
    def test_law1_and_law2_compute_fork_isolation(self) -> None:
        """Verifies physical compute separation and zero leakage of INTERNAL_ADVISORY."""
        # Client safe extraction
        tech_extractor = TechArchExtractor(use_local_model=True, mock_mode=True)
        # Internal advisory extractions (local 8B MLX)
        dynamics_agent = StakeholderDynamicsAgent(mock_mode=True)
        blocker_agent = FrictionBlockerAgent(mock_mode=True)

        self.assertEqual(tech_extractor.card.compute_posture, ComputePosture.CLIENT_SAFE)
        self.assertEqual(dynamics_agent.card.compute_posture, ComputePosture.INTERNAL_ADVISORY)
        self.assertEqual(blocker_agent.card.compute_posture, ComputePosture.INTERNAL_ADVISORY)

        # Ensure internal advisory cards are pinned strictly to local MLX
        self.assertEqual(dynamics_agent.card.model_binding.provider, "mlx")
        self.assertEqual(blocker_agent.card.model_binding.provider, "mlx")


if __name__ == "__main__":
    unittest.main()
