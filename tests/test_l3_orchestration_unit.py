"""Unit tests for dual_pipeline.l3_orchestration.

Verifies DAG construction, step contract creation, and workflow sealing.
"""
from __future__ import annotations

import unittest
from typing import Any

from dual_pipeline.l3_orchestration import L3Orchestrator


class TestL3OrchestrationUnit(unittest.TestCase):
    def setUp(self) -> None:
        self.route_contract = {
            "artifact_id": "route-test-1234",
            "artifact_type": "route-decision",
            "route": "L3_MANAGED",
        }
        self.orchestrator = L3Orchestrator(self.route_contract)

    def test_initialization(self) -> None:
        self.assertTrue(self.orchestrator.workflow_id.startswith("wf-"))
        self.assertEqual(self.orchestrator.route_contract, self.route_contract)
        self.assertEqual(self.orchestrator.sealed_artifacts, [])

    def test_build_dag(self) -> None:
        dag = self.orchestrator.build_dag()
        self.assertIsInstance(dag, list)
        self.assertGreaterEqual(len(dag), 1)
        self.assertEqual(dag[0]["status"], "PENDING")
        self.assertEqual(dag[0]["capability"], "execute_workflow_step")

    @unittest.mock.patch("dual_pipeline.l3_orchestration.create_artifact")
    def test_execute_workflow(self, mock_create_artifact: unittest.mock.MagicMock) -> None:
        def fake_create(artifact_type: str, artifact_id: str, **kwargs: Any) -> dict[str, Any]:
            return {"artifact_type": artifact_type, "artifact_id": artifact_id, **kwargs}

        mock_create_artifact.side_effect = fake_create

        package = self.orchestrator.execute_workflow()
        self.assertIsInstance(package, dict)
        self.assertEqual(package.get("artifact_type"), "sealed-workflow-package")
        self.assertEqual(package.get("route_id"), "route-test-1234")
        self.assertEqual(len(package.get("steps", [])), 1)
        step = package["steps"][0]
        self.assertEqual(step.get("artifact_type"), "sealed-l2-artifact")
        self.assertEqual(step.get("status"), "SUCCESS")


if __name__ == "__main__":
    unittest.main()
