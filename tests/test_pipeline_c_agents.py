"""Unit and Integration Tests for Pipeline C Retrieval Agents (Wave 4).

Tests:
1. Agent C1 (PolicyGatekeeperAgent) Law 6 pre-retrieval policy & leak blocking.
2. Agent C2 (MultiPerspectiveDispatcher) query expansion across 4 lenses.
3. Agent C3 (HybridFusionRanker) Reciprocal Rank Fusion & Invariant 8.1 score decoupling.
4. Agent C4 (ReleaseGateAuditor) 11 release gates & latency threshold.
5. Agent C0 (RetrievalCoordinator) end-to-end parallel retrieval flow.
"""
from __future__ import annotations

import unittest

from dual_pipeline.a2a import (
    A2ATask,
    A2ATaskStatus,
)
from dual_pipeline.retrieval.agents import (
    HybridFusionRanker,
    MultiPerspectiveDispatcher,
    PolicyGatekeeperAgent,
    ReleaseGateAuditor,
    RetrievalCoordinator,
)


class TestPipelineCAgents(unittest.IsolatedAsyncioTestCase):
    """Verifies Pipeline C specialized retrieval agents and release auditor."""

    async def test_policy_gatekeeper_agent(self) -> None:
        agent = PolicyGatekeeperAgent()
        facts = [
            {"node_id": "c1", "name": "Architecture Fact", "namespace": "CLIENT_SAFE", "is_quarantined": False},
            {"node_id": "c2", "name": "Low Quality Fact", "namespace": "CLIENT_SAFE", "is_quarantined": True},
            {"node_id": "a1", "name": "Secret Veto", "namespace": "INTERNAL_ADVISORY", "is_quarantined": False},
        ]

        # Test stealth mode: advisory fact must be filtered out for CLIENT_SAFE caller
        task = A2ATask(
            task_id="t-c1",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Filter policy before retrieval",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "candidate_facts": facts,
                "stealth_mode": True,
                "caller_namespace": "CLIENT_SAFE",
                "include_quarantined": False,
            },
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        filtered_ids = [f["node_id"] for f in res["filtered_facts"]]
        self.assertIn("c1", filtered_ids)
        self.assertNotIn("c2", filtered_ids)  # Filtered by soft quarantine
        self.assertNotIn("a1", filtered_ids)  # Filtered by stealth mode
        self.assertEqual(res["blocked_leak_count"], 1)

    async def test_multi_perspective_dispatcher(self) -> None:
        agent = MultiPerspectiveDispatcher(mock_mode=True)
        task = A2ATask(
            task_id="t-c2",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Deconstruct query",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"query": "Evaluate cloud migration to AWS S3"},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["perspective_count"], 4)
        perspectives = [sq["perspective"] for sq in res["sub_queries"]]
        self.assertIn("TECHNICAL", perspectives)
        self.assertIn("COMMERCIAL", perspectives)
        self.assertIn("ADOPTION", perspectives)
        self.assertIn("BLOCKER", perspectives)

    async def test_hybrid_fusion_ranker(self) -> None:
        agent = HybridFusionRanker(rrf_k=60)
        list1 = [{"node_id": "n1", "name": "Fact 1"}, {"node_id": "n2", "name": "Fact 2"}]
        list2 = [{"node_id": "n2", "name": "Fact 2"}, {"node_id": "n3", "name": "Fact 3"}]
        task = A2ATask(
            task_id="t-c3",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="RRF Fusion",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"ranked_lists": [list1, list2], "limit": 5},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        fused = res["fused_facts"]
        self.assertEqual(len(fused), 3)
        # Node n2 appears in both lists, so its RRF score must be highest
        self.assertEqual(fused[0]["node_id"], "n2")
        self.assertIn("rrf_retrieval_score", fused[0])
        # Invariant 8.1: retrieval rank is decoupled from option score
        self.assertNotIn("option_score", fused[0])

    async def test_release_gate_auditor(self) -> None:
        agent = ReleaseGateAuditor(mock_mode=True)
        task = A2ATask(
            task_id="t-c4",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Audit release gates",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"max_query_latency_ms": 150.0},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(res["all_gates_passed"])
        self.assertEqual(res["qualification_status"], "DELIVERY_READY")
        self.assertEqual(res["total_gate_count"], 11)

        # Test latency gate failure (>7500ms)
        task_slow = A2ATask(
            task_id="t-c4-slow",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Audit slow latency",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"max_query_latency_ms": 8200.0},
        )
        res_slow = await agent.handle_task(task_slow)
        self.assertFalse(res_slow["all_gates_passed"])
        self.assertEqual(res_slow["qualification_status"], "BLOCKED")

    async def test_retrieval_coordinator_e2e(self) -> None:
        coordinator = RetrievalCoordinator(mock_mode=True)
        facts = [
            {"node_id": "f1", "name": "AWS S3 Pipeline Architecture", "namespace": "CLIENT_SAFE", "is_quarantined": False},
            {"node_id": "f2", "name": "Sprint 1 Milestones", "namespace": "CLIENT_SAFE", "is_quarantined": False},
        ]
        task = A2ATask(
            task_id="t-c0-coord",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="End to end retrieval",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"query": "LENS S3 Deployment", "candidate_facts": facts},
        )
        res = await coordinator.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(res["all_gates_passed"])
        self.assertEqual(len(res["fused_facts"]), 2)
        self.assertLess(res["latency_ms"], 7500.0)
        self.assertIn("l1_plan", res)
        l1_plan = res["l1_plan"]
        self.assertIn(l1_plan.model_binding["model_id"], ("gpt-6.1-sol", "gpt-5.6-luna"))
        self.assertIn(l1_plan.model_binding["reasoning_effort"], ("max", "high"))
        self.assertEqual(l1_plan.primary_intent, "TECHNICAL_ARCHITECTURE")
        self.assertGreater(l1_plan.perspective_weights["TECHNICAL"], l1_plan.perspective_weights["COMMERCIAL"])

    async def test_l1_query_cognition_dynamic_weights(self) -> None:
        from dual_pipeline.retrieval.agents import L1QueryCognition
        l1 = L1QueryCognition()

        # Timeline query should prioritize COMMERCIAL
        plan_timeline = l1.plan("What is the milestone deadline for deployment?")
        self.assertEqual(plan_timeline.primary_intent, "DELIVERY_TIMELINE")
        self.assertGreater(plan_timeline.perspective_weights["COMMERCIAL"], plan_timeline.perspective_weights["BLOCKER"])

        # Risk query should prioritize BLOCKER
        plan_risk = l1.plan("What are the architectural blockers and security risks?")
        self.assertEqual(plan_risk.primary_intent, "RISK_AUDIT")
        self.assertGreater(plan_risk.perspective_weights["BLOCKER"], plan_risk.perspective_weights["TECHNICAL"])

    async def test_retrieval_coordinator_sanitization_fail_closed(self) -> None:
        coordinator = RetrievalCoordinator(mock_mode=True)
        task = A2ATask(
            task_id="t-c0-fail-closed",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="Retrieve with toxic query",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "query": "Find facts matching key: REDACTED_PRIVATE_KEY_HEADER\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----",
                "candidate_facts": [],
            },
        )
        with self.assertRaises(ValueError) as ctx:
            await coordinator.handle_task(task)
        self.assertIn("L0 ROUTING BLOCKED: L1 retrieval sanitization failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

