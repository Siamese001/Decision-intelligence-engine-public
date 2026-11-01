"""Tests for Pipeline D (Decision Intelligence) HITL governance and 20% margin gating under gpt-5.6-luna.

Verifies:
1. DecisionOrchestrator AgentCard binds gpt-5.6-luna (reasoning_effort="high").
2. Mandatory HITL surfaced in UI Antigravity with clickable cards when margin > 20% (Delta > 0.20).
3. Autonomous decision recorded in ledger when margin <= 20% (Delta <= 0.20) to calibrate priors.
4. Terminal state transitions: ESCALATED_HITL vs APPROVED_UNANIMOUS / APPROVED_WITH_CONDITIONS.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.a2a import (
    ComputePosture,
)
from dual_pipeline.decision.agents.decision_orchestrator import (
    DecisionOrchestrator,
    get_decision_orchestrator_card,
)
from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.hitl_governance import HitlSurfacingMode
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge


class TestPipelineDHitl(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "test_hitl_ledger.json")
        self.arbiter = HitlArbiterAgent(
            ledger_path=self.ledger_path,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.ui_bridge = AntigravityUIBridge(arbiter=self.arbiter)
        self.orchestrator = DecisionOrchestrator(
            hitl_arbiter=self.arbiter,
            ui_bridge=self.ui_bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_decision_orchestrator_gpt_5_6_luna_bindings(self) -> None:
        """Verifies DecisionOrchestrator binds gpt-5.6-luna or gpt-5.6-sol with reasoning_effort in ('high', 'max')."""
        card = get_decision_orchestrator_card()
        self.assertIn(card.model_binding.model_id, ("gpt-5.6-luna", "gpt-5.6-sol"))
        self.assertIn(card.model_binding.reasoning_effort, ("high", "max"))
        self.assertEqual(card.compute_posture, ComputePosture.CLIENT_SAFE)
        self.assertIn("PlanL1DecisionCognition", card.skills)
        self.assertIn("EnforceAmbiguityMarginGate", card.skills)

        self.assertIn(self.orchestrator.card.model_binding.model_id, ("gpt-5.6-luna", "gpt-5.6-sol"))
        self.assertIn(self.orchestrator.card.model_binding.reasoning_effort, ("high", "max"))

    def test_orchestrator_divergent_margin_surfaces_hitl_ui(self) -> None:
        """When dialectical options diverge by > 20% (Delta > 0.20), mandatory HITL with UI cards surfaces."""
        options_divergent = [
            {
                "key": "opt-turnkey",
                "title": "Turnkey modular event broker",
                "description": "Modular decoupled architecture with automated deployment, turnkey execution, and high ROI",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-legacy",
                "title": "Legacy monolith patch",
                "description": "Complex multi-phase refactor into legacy monolith with technical debt",
                "estimated_roi": 0.30,
            },
        ]
        canonical_evidence = [{"evidence_id": "ev-01", "digest": "sha256-abc"}]
        canonical_delta = {"nodes": [{"node_id": "n1"}], "assertions": [{"assertion_id": "a1"}]}

        res = self.orchestrator.orchestrate_decision(
            decision_id="dec-divergent-1",
            question="Select multi-agent mesh architecture",
            options=options_divergent,
            canonical_evidence=canonical_evidence,
            canonical_graph_delta=canonical_delta,
            margin_threshold=0.20,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
        )

        self.assertEqual(res["terminal_state"], "ESCALATED_HITL")
        self.assertTrue(res["should_surface_hitl"])
        self.assertGreater(res["margin"], 0.20)
        self.assertIsNotNone(res["atomic_card"])
        self.assertIn("(Recommended)", res["atomic_card"]["options"][0])

    def test_orchestrator_close_margin_auto_records_in_ledger(self) -> None:
        """When dialectical options differ by <= 20% (Delta <= 0.20), auto-records in ledger and resolves."""
        options_close = [
            {
                "key": "opt-micro",
                "title": "Modular microservices path",
                "description": "Standard modular architecture with standard velocity",
                "estimated_roi": 0.75,
            },
            {
                "key": "opt-actor",
                "title": "Distributed actor model path",
                "description": "Standard distributed actors with standard velocity",
                "estimated_roi": 0.73,
            },
        ]
        canonical_evidence = [{"evidence_id": "ev-01", "digest": "sha256-abc"}]
        canonical_delta = {"nodes": [{"node_id": "n1"}], "assertions": [{"assertion_id": "a1"}]}

        res = self.orchestrator.orchestrate_decision(
            decision_id="dec-close-1",
            question="Choose between microservices or actor model",
            options=options_close,
            canonical_evidence=canonical_evidence,
            canonical_graph_delta=canonical_delta,
            margin_threshold=0.20,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
        )

        self.assertEqual(res["terminal_state"], "APPROVED_UNANIMOUS")
        self.assertFalse(res["should_surface_hitl"])
        self.assertLessEqual(res["margin"], 0.20)
        self.assertIsNotNone(res["ledger_record"])

        # Verify ledger persistence
        ledger_file = Path(self.ledger_path)
        self.assertTrue(ledger_file.exists())
        history = self.arbiter.store.get_history(category="strategic_architecture")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["selected_key"], "opt-micro")
        self.assertEqual(history[0]["operator_id"], "autonomous_engine")


if __name__ == "__main__":
    unittest.main()
