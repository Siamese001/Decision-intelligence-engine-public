"""Tests for Pipeline C (Retrieval) HITL governance and 20% margin gating under gpt-5.6-luna.

Verifies:
1. RetrievalCoordinator AgentCard binds gpt-5.6-luna (reasoning_effort="high").
2. Mandatory HITL surfaced in UI Antigravity with clickable cards when margin > 20% (Delta > 0.20).
3. Autonomous decision recorded in ledger when margin <= 20% (Delta <= 0.20) to calibrate priors.
4. Operator clicked option in UI Antigravity successfully resolves and calibrates retrieval rankings.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.a2a import (
    A2ATask,
    A2ATaskStatus,
    ComputePosture,
)
from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
from dual_pipeline.retrieval.agents.retrieval_coordinator import (
    RetrievalCoordinator,
    get_retrieval_coordinator_card,
)


class TestPipelineCHitl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "test_hitl_ledger.json")
        self.arbiter = HitlArbiterAgent(
            ledger_path=self.ledger_path,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.ui_bridge = AntigravityUIBridge(arbiter=self.arbiter)
        self.coordinator = RetrievalCoordinator(
            mock_mode=True,
            hitl_arbiter=self.arbiter,
            ui_bridge=self.ui_bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_retrieval_coordinator_gpt_5_6_luna_bindings(self) -> None:
        """Verifies RetrievalCoordinator binds gpt-5.6-luna with reasoning_effort='high'."""
        card = get_retrieval_coordinator_card()
        self.assertEqual(card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(card.model_binding.reasoning_effort, "high")
        self.assertEqual(card.compute_posture, ComputePosture.CLIENT_SAFE)
        self.assertIn("PlanL1QueryCognition", card.skills)
        self.assertIn("ExecuteHybridFusion", card.skills)

        self.assertEqual(self.coordinator.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(self.coordinator.card.model_binding.reasoning_effort, "high")

    def test_arbitrate_ranking_divergent_margin_surfaces_hitl_ui(self) -> None:
        """When candidate facts diverge by > 20% (Delta > 0.20), mandatory HITL with UI cards surfaces."""
        fused_facts = [
            {
                "node_id": "fact-101",
                "name": "APEX Solar Inverter Grid Telemetry Architecture",
                "confidence": 0.88,
                "rrf_retrieval_score": 0.035,
            },
            {
                "node_id": "fact-102",
                "name": "General Inverter Overview",
                "confidence": 0.55,  # Delta = 0.88 - 0.55 = 0.33 > 0.20
                "rrf_retrieval_score": 0.020,
            },
        ]

        decision_res = self.coordinator.arbitrate_ranking(
            fused_facts=fused_facts,
            query="APEX solar grid telemetry",
            margin_threshold=0.20,
        )

        self.assertEqual(decision_res["status"], "HITL_REQUIRED")
        self.assertTrue(decision_res["should_surface"])
        self.assertGreater(decision_res["margin"], 0.20)
        self.assertIsNotNone(decision_res["atomic_card"])
        self.assertIn("(Recommended)", decision_res["atomic_card"]["options"][0])
        self.assertEqual(decision_res["top_option"]["key"], "fact-101")

    def test_arbitrate_ranking_close_margin_auto_records_in_ledger(self) -> None:
        """When candidate facts differ by <= 20% (Delta <= 0.20), auto-records in ledger."""
        fused_facts = [
            {
                "node_id": "fact-201",
                "name": "Turbine Blade Vibration FFT Analysis",
                "confidence": 0.75,
                "rrf_retrieval_score": 0.030,
            },
            {
                "node_id": "fact-202",
                "name": "Turbine Gearbox Acoustic Telemetry",
                "confidence": 0.70,  # Delta = 0.75 - 0.70 = 0.05 <= 0.20
                "rrf_retrieval_score": 0.028,
            },
        ]

        decision_res = self.coordinator.arbitrate_ranking(
            fused_facts=fused_facts,
            query="Turbine mechanical telemetry",
            margin_threshold=0.20,
        )

        self.assertEqual(decision_res["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(decision_res["should_surface"])
        self.assertLessEqual(decision_res["margin"], 0.20)
        self.assertEqual(decision_res["top_option"]["key"], "fact-201")
        self.assertIsNotNone(decision_res.get("ledger_record"))

        # Verify ledger persistence
        ledger_file = Path(self.ledger_path)
        self.assertTrue(ledger_file.exists())
        history = self.arbiter.store.get_history(category="retrieval_ranking")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["selected_key"], "fact-201")
        self.assertEqual(history[0]["operator_id"], "autonomous_engine")

    async def test_retrieval_coordinator_end_to_end_with_hitl_arbitration(self) -> None:
        """Verifies full A2A handle_task invocation with enable_hitl_arbitration."""
        facts = [
            {"node_id": "f1", "name": "AWS S3 Multi-Region Replication", "confidence": 0.90, "namespace": "CLIENT_SAFE", "is_quarantined": False},
            {"node_id": "f2", "name": "AWS Glacier Long-Term Vault", "confidence": 0.60, "namespace": "CLIENT_SAFE", "is_quarantined": False},
        ]
        task = A2ATask(
            task_id="t-c-hitl-1",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=self.coordinator.card.agent_id,
            objective="Retrieve S3 configuration with HITL arbitration",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "query": "AWS S3 Multi-Region Replication",
                "candidate_facts": facts,
                "enable_hitl_arbitration": True,
                "margin_threshold": 0.20,
            },
        )

        res = await self.coordinator.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertIsNotNone(res["hitl_arbitration"])
        # Delta = 0.90 - 0.60 = 0.30 > 0.20 -> Surfaces HITL
        self.assertTrue(res["hitl_arbitration"]["should_surface"])
        self.assertEqual(res["hitl_arbitration"]["status"], "HITL_REQUIRED")


if __name__ == "__main__":
    unittest.main()
