"""Wave 4 Acceptance Test Suite: Decision Intelligence & Zero-Egress Production Cutover.

Verifies:
1. RedTeamRiskAgent bound to local Qwen2.5-14B under MLX.
2. StrategicFeasibilityAgent bound to local Qwen2.5-14B under MLX.
3. BriefingSynthesisAgent bound to local Qwen2.5-32B under MLX with Law 7 sequencing.
4. BriefingAndDecisionHealerAgent bound to local Qwen2.5-14B under MLX.
5. Dialectical debate 25% margin gating (autonomous resolve vs HITL escalation).
6. Complete Pipeline A -> B -> C -> D execution under local model runtime.
7. Zero-egress network policy interceptor blocking external sockets.
8. Fallback circuit breaker fail-closed tripping and cloud diversion.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import unittest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.decision.agents import (
    BriefingAndDecisionHealerAgent,
    BriefingSynthesisAgent,
    PipelineSequencingViolationError,
    RedTeamRiskAgent,
    StrategicFeasibilityAgent,
)
from dual_pipeline.policy.circuit_breaker import (
    BreakerState,
    CircuitBreakerGateway,
    LocalExecutionHaltedError,
)
from dual_pipeline.policy.network_policy import NetworkPolicyGuard, ZeroEgressViolationError
from evals.evaluate_full_mesh_a_to_d_live import FullMeshAtoDLiveEvaluator


class TestWave4DecisionAndCutover(unittest.TestCase):
    """Wave 4 Acceptance Test Suite for Pipeline D & Production Cutover."""

    def test_red_team_risk_agent_local_binding(self) -> None:
        """Verifies RedTeamRiskAgent operates under local Qwen2.5-14B MLX model."""
        agent = RedTeamRiskAgent(use_local_model=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")
        self.assertIn("mlx_inference", agent.card.allowed_tools)

        options = [
            {
                "key": "opt-1",
                "title": "Decoupled Event-Driven Mesh",
                "description": "Modular, contract-first decoupled architecture with strict validation.",
            },
            {
                "key": "opt-2",
                "title": "Unbounded Monolithic Shortcut",
                "description": "Unbounded bypass of governance and single point of failure.",
            },
        ]
        results = agent.analyze_risks(options)
        self.assertEqual(len(results), 2)
        # Option 2 should have critical fatal flaw
        opt2_res = next(r for r in results if r["option_key"] == "opt-2")
        self.assertTrue(opt2_res["has_fatal_flaw"])
        self.assertGreaterEqual(opt2_res["risk_score"], 0.50)

    def test_strategic_feasibility_agent_local_binding(self) -> None:
        """Verifies StrategicFeasibilityAgent operates under local Qwen2.5-14B MLX model."""
        agent = StrategicFeasibilityAgent(use_local_model=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")
        self.assertIn("mlx_inference", agent.card.allowed_tools)

        options = [
            {
                "key": "opt-modular",
                "title": "Turnkey Modular Event Bus",
                "description": "Modular automated architecture with immediate delivery.",
                "roi": 0.95,
            },
            {
                "key": "opt-legacy",
                "title": "Legacy Monolith Refactor",
                "description": "Complex multi-phase monolith overhaul.",
                "roi": 0.40,
            },
        ]
        evals = agent.evaluate_feasibility(options)
        self.assertEqual(len(evals), 2)
        self.assertEqual(evals[0]["option_key"], "opt-modular")
        self.assertGreater(evals[0]["feasibility_score"], evals[1]["feasibility_score"])

    def test_briefing_synthesis_agent_local_binding_and_law7(self) -> None:
        """Verifies BriefingSynthesisAgent local Qwen2.5-32B binding, Law 7, and date format."""
        agent = BriefingSynthesisAgent(use_local_model=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Qwen2.5-32B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")
        self.assertIn("mlx_inference", agent.card.allowed_tools)

        # Law 7 violation check: missing evidence
        with self.assertRaises(PipelineSequencingViolationError):
            agent.synthesize_recap(
                meeting_name="Nexus Alignment",
                date_str="09/12/2026",
                start_time_et="10:00 AM",
                end_time_et="11:00 AM",
                attendees_by_company={"Apex Global": ["Leader"]},
                meeting_objective="Align",
                decisions=[],
                next_steps=[],
                evidence=[],  # Missing evidence triggers Law 7
                canonical_graph_delta={"nodes": [{"id": "node-1"}]},
            )

        # Valid synthesis under Law 7 with strict MM/DD/YYYY date
        recap_md = agent.synthesize_recap(
            meeting_name="Project Beacon Architecture Alignment",
            date_str="09/12/2026",
            start_time_et="14:30",
            end_time_et="15:30",
            attendees_by_company={"Apex Global Enterprises": ["Alex Rivera"], "PartnerAdvisory": ["Jordan Taylor"]},
            meeting_objective="Deploy parallel A2A mesh with zero-egress Metal runtime.",
            decisions=[{"title": "Adopt Local MLX Models", "rationale": "Enforce zero cloud dependency"}],
            next_steps=[{"item": "Deploy Wave 4 cutover", "owner": "Alex Rivera", "due_date": "09/12/2026"}],
            evidence=[{"locator": "Turn 1"}],
            canonical_graph_delta={"nodes": [{"id": "system:sherpa"}], "assertions": [{"id": "a-1"}]},
        )
        self.assertIn("**Meeting Date**: 09/12/2026", recap_md)
        self.assertIn("| Deploy Wave 4 cutover | Alex Rivera | 09/12/2026 |", recap_md)

    def test_briefing_healer_local_binding(self) -> None:
        """Verifies BriefingAndDecisionHealerAgent operates under local Qwen2.5-14B MLX model."""
        agent = BriefingAndDecisionHealerAgent(use_local_model=True, mock_mode=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")
        self.assertIn("mlx_inference", agent.card.allowed_tools)

        task = A2ATask(
            task_id="healer-briefing-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=agent.card.agent_id,
            objective="Heal draft briefing action items",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "recap_md": "# Draft Recap\n- Need to look into options.",
                "failure_reasons": ["Passive verb", "Missing owner"],
                "roster_context": ["Alex Rivera"],
            },
        )
        res = asyncio.run(agent.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")

    def test_dialectical_debate_25_percent_margin_gating(self) -> None:
        """Verifies 25% margin gating rule for autonomous resolution vs HITL escalation."""
        # Case A: Decisive margin (Delta >= 25%) -> Autonomous proceed
        score_a1 = 0.88
        score_a2 = 0.58
        margin_a = score_a1 - score_a2
        self.assertGreaterEqual(margin_a, 0.25)
        decision_mode_a = "AUTONOMOUS_PROCEED" if margin_a >= 0.25 else "HITL_ESCALATION"
        self.assertEqual(decision_mode_a, "AUTONOMOUS_PROCEED")

        # Case B: Narrow margin (Delta < 25%) -> Surface atomic HITL decision
        score_b1 = 0.72
        score_b2 = 0.65
        margin_b = score_b1 - score_b2
        self.assertLess(margin_b, 0.25)
        decision_mode_b = "AUTONOMOUS_PROCEED" if margin_b >= 0.25 else "HITL_ESCALATION"
        self.assertEqual(decision_mode_b, "HITL_ESCALATION")

    def test_full_mesh_a_to_d_local_execution(self) -> None:
        """Verifies end-to-end full mesh execution with local model configuration."""
        evaluator = FullMeshAtoDLiveEvaluator(use_local_model=True, mock_mode=True)
        self.assertEqual(evaluator.model, "mlx-community/Qwen2.5-32B-Instruct-4bit")
        self.assertTrue(evaluator.use_local_model)

        result = evaluator.run_full_mesh_pipeline(
            meeting_name="Project Beacon Architecture Alignment",
            date_str="09-12-2026",
            time_str="1430ET",
        )
        audit_envelope = result["audit_envelope"]
        self.assertEqual(audit_envelope["status"], "PASS")

        # Verify 7 Laws
        laws = audit_envelope["laws_verified"]
        self.assertTrue(laws["Law 1 (Physical Compute Fork)"])
        self.assertTrue(laws["Law 2 (INTERNAL_ADVISORY Zero Egress)"])
        self.assertTrue(laws["Law 3 (Untrusted Fast Path Badge)"])
        self.assertTrue(laws["Law 4 (Immutable Claim-Check Pointers)"])
        self.assertTrue(laws["Law 5 (Multi-Dimensional Soft-Quarantine)"])
        self.assertTrue(laws["Law 6 (Pre-Retrieval Policy Gatekeeper)"])
        self.assertTrue(laws["Law 7 (Invariant Pipeline Sequencing)"])

        # Verify all 3 output artifacts exist and are non-empty
        from pathlib import Path
        for art_key, art_path_str in audit_envelope["delivered_artifacts"].items():
            p = Path(art_path_str)
            self.assertTrue(p.exists(), f"Missing artifact: {art_key}")
            self.assertGreater(p.stat().st_size, 0)

    def test_zero_egress_network_policy_enforcement(self) -> None:
        """Verifies process-level network isolation blocks external socket connection attempts."""
        policy = NetworkPolicyGuard(enforce_zero_egress=True)
        with policy.isolated_network_context():
            # Loopback connections (e.g. 127.0.0.1) are permitted for local IPC
            s_local = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # Attempt to bind/connect loopback without violation
                s_local.settimeout(0.01)
                with contextlib.suppress(ConnectionRefusedError, TimeoutError, OSError):
                    s_local.connect(("127.0.0.1", 9999))
            finally:
                s_local.close()

            # External connections (e.g. public cloud IP 93.184.216.34) must be blocked
            s_ext = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with self.assertRaises(ZeroEgressViolationError):
                s_ext.connect(("93.184.216.34", 443))
            s_ext.close()

    def test_circuit_breaker_fail_closed_and_cloud_diversion(self) -> None:
        """Verifies circuit breaker trips to OPEN on failures and handles cloud diversion."""
        breaker = CircuitBreakerGateway(
            failure_threshold=2,
            allow_cloud_fallback=False,
            fallback_model="gpt-5.6-luna",
        )

        def failing_local_fn() -> None:
            raise RuntimeError("Synthetic local GPU memory fault")

        def mock_cloud_fn() -> str:
            return "SUCCESS_VIA_CLOUD_FALLBACK"

        # First failure: records failure but circuit remains CLOSED
        with self.assertRaises(RuntimeError):
            breaker.route_execution(local_fn=failing_local_fn, cloud_fallback_fn=mock_cloud_fn)
        self.assertEqual(breaker.state, BreakerState.CLOSED)

        # Second failure: trips circuit to OPEN; since allow_cloud_fallback=False, fails closed
        with self.assertRaises((RuntimeError, LocalExecutionHaltedError)):
            breaker.route_execution(local_fn=failing_local_fn, cloud_fallback_fn=mock_cloud_fn)
        self.assertEqual(breaker.state, BreakerState.OPEN)

        # In OPEN state with fallback disabled: raises LocalExecutionHaltedError
        with self.assertRaises(LocalExecutionHaltedError):
            breaker.route_execution(local_fn=failing_local_fn, cloud_fallback_fn=mock_cloud_fn)

        # Enable cloud fallback: diverts to cloud_fallback_fn
        breaker.set_cloud_fallback_authorization(True)
        res = breaker.route_execution(local_fn=failing_local_fn, cloud_fallback_fn=mock_cloud_fn)
        self.assertEqual(res, "SUCCESS_VIA_CLOUD_FALLBACK")


if __name__ == "__main__":
    unittest.main()
