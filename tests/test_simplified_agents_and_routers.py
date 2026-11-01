"""Unit tests for simplified agents and consolidated routers (Wave 4)."""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.a2a import A2ATask
from dual_pipeline.decision.agents.decision_orchestrator import DecisionOrchestrator
from dual_pipeline.decision.agents.dialectical_perspective_agent import (
    DialecticalPerspectiveAgent,
    PerspectiveType,
)
from dual_pipeline.query_router_core import determine_project_directory
from dual_pipeline.retrieval_service import RetrievalService


class SimplifiedAgentsAndRoutersTests(unittest.TestCase):
    def test_dialectical_agent_red_team_analysis(self) -> None:
        agent = DialecticalPerspectiveAgent(perspective=PerspectiveType.RED_TEAM)
        self.assertEqual("Red Team Adversarial Risk Analyst", agent.card.role)

        options = [
            {
                "key": "opt-1",
                "title": "Decoupled Architecture",
                "description": "Modular design with strict contracts",
            },
            {
                "key": "opt-2",
                "title": "Legacy Monolith with Governance Bypass",
                "description": "Unbounded bypass of validation gates in monolithic service",
            },
        ]

        risks = agent.analyze_risks(options)
        self.assertEqual(2, len(risks))

        opt1_res = next(r for r in risks if r["option_key"] == "opt-1")
        self.assertFalse(opt1_res["has_fatal_flaw"])
        self.assertEqual(0.0, opt1_res["risk_score"])

        opt2_res = next(r for r in risks if r["option_key"] == "opt-2")
        self.assertTrue(opt2_res["has_fatal_flaw"])
        self.assertGreaterEqual(opt2_res["risk_score"], 0.5)

    def test_dialectical_agent_feasibility_evaluation(self) -> None:
        agent = DialecticalPerspectiveAgent(perspective=PerspectiveType.STRATEGIC_FEASIBILITY)
        self.assertEqual("Strategic Feasibility & Value Analyst", agent.card.role)

        options = [
            {
                "key": "opt-turnkey",
                "title": "Turnkey Modular System",
                "description": "Immediate turnkey rollout with decoupled contract architecture",
                "roi": 0.95,
            },
            {
                "key": "opt-complex",
                "title": "Complex Legacy Rewrite",
                "description": "Multi-phase complex rewrite of legacy monolith",
                "roi": 0.30,
            },
        ]

        evals = agent.evaluate_feasibility(options)
        self.assertEqual(2, len(evals))

        top = evals[0]
        self.assertEqual("opt-turnkey", top["option_key"])
        self.assertGreater(top["feasibility_score"], evals[1]["feasibility_score"])
        self.assertEqual("RAPID_ACCELERATION", top["velocity_impact"])

    async def _run_a2a_task(self, agent: DialecticalPerspectiveAgent, payload: Dict[str, Any]) -> Dict[str, Any]:
        from dual_pipeline.a2a import A2ATaskStatus
        task = A2ATask(
            task_id="test-task-1",
            parent_task_id=None,
            sender_agent_id="test-orchestrator",
            recipient_agent_id=agent.card.agent_id,
            objective="Evaluate candidate options dialectically",
            status=A2ATaskStatus.SUBMITTED,
            input_payload=payload,
        )
        return await agent.handle_task(task)

    def test_dialectical_agent_a2a_task_handling(self) -> None:
        import asyncio

        red_agent = DialecticalPerspectiveAgent(perspective=PerspectiveType.RED_TEAM)
        red_result = asyncio.run(self._run_a2a_task(red_agent, {
            "options": [{"key": "opt-1", "title": "Safe Option"}]
        }))
        self.assertEqual("COMPLETED", red_result["status"])
        self.assertIn("risk_assessments", red_result)

        feas_agent = DialecticalPerspectiveAgent(perspective=PerspectiveType.STRATEGIC_FEASIBILITY)
        feas_result = asyncio.run(self._run_a2a_task(feas_agent, {
            "options": [{"key": "opt-1", "title": "Feasible Option"}]
        }))
        self.assertEqual("COMPLETED", feas_result["status"])
        self.assertIn("evaluations", feas_result)

    def test_decision_orchestrator_integration(self) -> None:
        orchestrator = DecisionOrchestrator()
        self.assertIsInstance(orchestrator.red_team, DialecticalPerspectiveAgent)
        self.assertEqual(PerspectiveType.RED_TEAM, orchestrator.red_team.perspective)
        self.assertIsInstance(orchestrator.feasibility, DialecticalPerspectiveAgent)
        self.assertEqual(PerspectiveType.STRATEGIC_FEASIBILITY, orchestrator.feasibility.perspective)

    def test_consolidated_project_router_known_metadata(self) -> None:
        metadata = {"project": "project_nexus"}
        chosen = determine_project_directory(metadata)
        self.assertEqual("project_nexus", chosen)

    def test_dynamic_router_facade_purged(self) -> None:
        with self.assertRaises(ImportError):
            pass  # type: ignore[import-not-found]
        from dual_pipeline.query_router_core import determine_project_directory as core_fn
        metadata = {"project": "project_nexus"}
        self.assertEqual("project_nexus", core_fn(metadata))

    def test_retrieval_di_bridge_service_exports(self) -> None:
        self.assertTrue(issubclass(RetrievalService, object))
        self.assertTrue(hasattr(RetrievalService, "execute"))
        self.assertTrue(hasattr(RetrievalService, "get_topology"))


if __name__ == "__main__":
    unittest.main()
