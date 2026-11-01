"""Unit and Integration Tests for Pipeline B HITL Governance (Wave 2).

Verifies:
1. AdjudicationJudgeAgent model binding under gpt-5.6-luna (high).
2. 20% Ambiguity margin gating on candidate fact confirmation vs soft quarantine.
3. When confidence difference > 20%, surfaces mandatory HITL.
4. When confidence difference <= 20%, autonomously records decision in hitl_decision_ledger.json.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.hitl_governance import HitlSurfacingMode
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
from pipeline_b_extraction.agents.adjudication_judge_agent import (
    AdjudicationJudgeAgent,
    get_adjudication_judge_card,
)


class TestPipelineBHitl(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = Path(self.temp_dir) / "pipeline_b_ledger.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_pipeline_b_gpt_5_6_luna_model_binding(self) -> None:
        """Verifies AdjudicationJudgeAgent card and model binding under gpt-5.6-luna high."""
        card = get_adjudication_judge_card(model_id="gpt-5.6-luna", reasoning_effort="high")
        self.assertEqual(card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(card.model_binding.reasoning_effort, "high")
        self.assertIn("ArbitrateQuarantineHitl", card.skills)

    def test_pipeline_b_fact_adjudication_divergence_surfacing(self) -> None:
        """When confidence difference > 20% (e.g. 85% vs 15% = 70% > 20%), surfaces mandatory HITL."""
        arbiter = HitlArbiterAgent(
            ledger_path=self.ledger_path,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
            margin_threshold=0.20,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        bridge = AntigravityUIBridge(
            arbiter=arbiter,
            mock_mode=True,
        )
        judge = AdjudicationJudgeAgent(
            mock_mode=True,
            hitl_arbiter=arbiter,
            ui_bridge=bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )

        # High confidence fact (0.85 -> CONFIRM=0.85, QUARANTINE=0.15, delta=0.70 > 0.20)
        candidate_fact = {
            "node_id": "fact-verified-arch",
            "name": "BGE-M3 Retrieval Architecture",
            "confidence": 0.85,
        }
        res = judge.arbitrate_disposition(candidate_fact, margin_threshold=0.20)
        self.assertFalse(res["is_quarantined"])
        self.assertIn("adjudication_decision", res)
        self.assertTrue(res["adjudication_decision"]["should_surface"])
        self.assertEqual(res["adjudication_decision"]["status"], "OPERATOR_RESOLVED")

        # Verify decision persisted to ledger
        history = arbiter.store.get_history("fact_adjudication")
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["selected_key"], "CONFIRM")

    def test_pipeline_b_fact_adjudication_auto_ledger_recording(self) -> None:
        """When confidence difference <= 20% (e.g. 55% vs 45% = 10% <= 20%), auto-records in ledger."""
        arbiter = HitlArbiterAgent(
            ledger_path=self.ledger_path,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
            margin_threshold=0.20,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        bridge = AntigravityUIBridge(
            arbiter=arbiter,
            mock_mode=True,
        )
        judge = AdjudicationJudgeAgent(
            mock_mode=True,
            hitl_arbiter=arbiter,
            ui_bridge=bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )

        # Borderline fact (0.55 -> CONFIRM=0.55, QUARANTINE=0.45, delta=0.10 <= 0.20)
        candidate_fact = {
            "node_id": "fact-borderline-claim",
            "name": "Tentative Q4 S3 Architecture",
            "confidence": 0.55,
        }
        res = judge.arbitrate_disposition(candidate_fact, margin_threshold=0.20)
        self.assertIn("adjudication_decision", res)
        self.assertFalse(res["adjudication_decision"]["should_surface"])
        self.assertEqual(res["adjudication_decision"]["status"], "AUTONOMOUS_APPROVED")

        # Verify autonomous resolution was recorded in ledger
        history = arbiter.store.get_history("fact_adjudication")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["selected_key"], "CONFIRM")
        self.assertTrue(history[0]["metadata"].get("autonomous"))


if __name__ == "__main__":
    unittest.main()
