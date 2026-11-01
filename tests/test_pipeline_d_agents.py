"""Unit and integration tests for Pipeline D specialized agents.

Tests:
- RedTeamRiskAgent (D1): Adversarial risk, fatal flaws, GPT-5.6-sol reasoning_effort=high.
- StrategicFeasibilityAgent (D2): Alignment, velocity, ROI, elegance, GPT-5.6-sol.
- FastPathSyncAgent (D3): Law 3 Untrusted Fast Path, [UNTRUSTED_FAST_PATH] badge, mutation rejection.
- HitlArbiterAgent (D4): 25% margin gate Δ < 0.25, atomic card, Bayesian prior calibration.
- BriefingSynthesisAgent (D5): Law 7 sequencing enforcement, Artifact 02 recap, Figma 4K spec.
- DecisionOrchestrator (D0): Dialectical debate, trade-off matrix, 4 terminal states.
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
from dual_pipeline.decision.agents import (
    FAST_PATH_BADGE,
    BriefingSynthesisAgent,
    DecisionOrchestrator,
    FastPathSyncAgent,
    HitlArbiterAgent,
    L1DecisionCognition,
    Law3ViolationError,
    PipelineSequencingViolationError,
    RedTeamRiskAgent,
    StrategicFeasibilityAgent,
    get_red_team_risk_card,
    get_strategic_feasibility_card,
)


class TestPipelineDAgents(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "test_hitl_ledger.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_red_team_risk_agent(self) -> None:
        """Verifies RedTeamRiskAgent card and adversarial risk analysis."""
        card = get_red_team_risk_card()
        self.assertIn(card.model_binding.model_id, ("gpt-5.6-luna", "gpt-5.6-sol"))
        self.assertIn(card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertEqual(card.compute_posture, ComputePosture.CLIENT_SAFE)

        agent = RedTeamRiskAgent()
        options = [
            {
                "key": "opt-safe",
                "title": "Modular microservices with verified contracts",
                "description": "Clean decoupled service architecture with automated regression gates",
            },
            {
                "key": "opt-risky",
                "title": "Bypass governance gates for speed",
                "description": "Unbounded direct writes without validation to hit deadline",
            },
        ]

        assessments = agent.analyze_risks(options)
        self.assertEqual(len(assessments), 2)

        safe = next(a for a in assessments if a["option_key"] == "opt-safe")
        risky = next(a for a in assessments if a["option_key"] == "opt-risky")

        self.assertFalse(safe["has_fatal_flaw"])
        self.assertTrue(risky["has_fatal_flaw"])
        self.assertGreater(risky["risk_score"], safe["risk_score"])
        self.assertIn(risky["model"], ("gpt-5.6-luna", "gpt-5.6-sol"))
        self.assertIn(risky["reasoning_effort"], ("medium", "high", "max"))

    def test_strategic_feasibility_agent(self) -> None:
        """Verifies StrategicFeasibilityAgent card, velocity, and ROI modeling."""
        card = get_strategic_feasibility_card()
        self.assertIn(card.model_binding.model_id, ("gpt-5.6-luna", "gpt-5.6-sol"))
        self.assertIn(card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertEqual(card.compute_posture, ComputePosture.CLIENT_SAFE)

        agent = StrategicFeasibilityAgent()
        options = [
            {
                "key": "opt-turnkey",
                "title": "Turnkey contract-first modular engine",
                "description": "Modular decoupled architecture with automated deployment and high ROI",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-monolith",
                "title": "Legacy monolith extension",
                "description": "Complex multi-phase refactor into legacy monolith",
                "estimated_roi": 0.40,
            },
        ]

        evals = agent.evaluate_feasibility(options)
        self.assertEqual(len(evals), 2)
        top = evals[0]
        self.assertEqual(top["option_key"], "opt-turnkey")
        self.assertGreater(top["feasibility_score"], evals[1]["feasibility_score"])
        self.assertEqual(top["velocity_impact"], "RAPID_ACCELERATION")
        self.assertIn(top["model"], ("gpt-5.6-luna", "gpt-5.6-sol"))

    def test_fast_path_sync_agent_law3(self) -> None:
        """Verifies Law 3 Untrusted Fast Path isolation, badging, and mutation refusal."""
        agent = FastPathSyncAgent()
        self.assertEqual(agent.card.model_binding.provider, "local")

        # Ingest live turn
        entry = agent.ingest_live_turn(
            speaker="Alice",
            text="We might need to adjust the vector dimension to 1024.",
            timestamp="00:15:30",
        )
        self.assertTrue(entry["is_untrusted_fast_path"])
        self.assertIn(FAST_PATH_BADGE, entry["badged_text"])
        self.assertEqual(entry["verification_status"], "UNTRUSTED_FAST_PATH")
        self.assertFalse(entry["canonical_committed"])

        # Fuse with canonical facts
        canonical_facts = [{"node_id": "node-101", "content": "Baseline dimension is 1024"}]
        fused = agent.fuse_with_canonical_evidence(canonical_facts)
        self.assertEqual(len(fused), 2)
        self.assertFalse(fused[0]["is_untrusted_fast_path"])
        self.assertTrue(fused[1]["is_untrusted_fast_path"])

        # Validate Law 3 rejection of canonical mutations
        with self.assertRaises(Law3ViolationError):
            agent.validate_no_canonical_mutation("INSERT_NODE")
        with self.assertRaises(Law3ViolationError):
            agent.validate_no_canonical_mutation("COMMIT_GRAPH")

    def test_hitl_arbiter_agent_margin_and_calibration(self) -> None:
        """Verifies 25% ambiguity margin rule, atomic card, and Bayesian feedback prior calibration."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)

        # Case 1: Ambiguity margin < 0.25 -> Surface HITL decision
        ambiguous_options = [
            {"key": "opt-a", "label": "Option A: Event Broker", "confidence_score": 0.60, "rationale": "High throughput"},
            {"key": "opt-b", "label": "Option B: Direct Mesh", "confidence_score": 0.50, "rationale": "Low latency"},
        ]
        result_hitl = arbiter.arbitrate_decision(
            decision_id="dec-ambiguous",
            category="architecture",
            question="Select inter-agent transport mechanism",
            options=ambiguous_options,
            margin_threshold=0.25,
        )
        self.assertEqual(result_hitl["status"], "HITL_REQUIRED")
        self.assertTrue(result_hitl["should_surface"])
        self.assertAlmostEqual(result_hitl["margin"], 0.10, places=2)
        self.assertIsNotNone(result_hitl["atomic_card"])
        self.assertIn("(Recommended)", result_hitl["atomic_card"]["options"][0])

        # Case 2: Margin >= 0.25 -> Autonomous approval under audit receipt
        clear_options = [
            {"key": "opt-a", "label": "Option A: Event Broker", "confidence_score": 0.85, "rationale": "High throughput"},
            {"key": "opt-b", "label": "Option B: Direct Mesh", "confidence_score": 0.40, "rationale": "Low latency"},
        ]
        result_auto = arbiter.arbitrate_decision(
            decision_id="dec-clear",
            category="architecture",
            question="Select inter-agent transport mechanism",
            options=clear_options,
            margin_threshold=0.25,
        )
        self.assertEqual(result_auto["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(result_auto["should_surface"])
        self.assertAlmostEqual(result_auto["margin"], 0.45, places=2)
        self.assertIn("audit_receipt", result_auto)

        # Case 3: Record human operator feedback and verify Bayesian prior calibration
        arbiter.record_feedback(
            decision_id="dec-ambiguous",
            category="transport_architecture",
            prompt="Select transport",
            options=ambiguous_options,
            selected_option=ambiguous_options[0],  # Operator chose opt-a
            rationale="Event broker handles spikes better",
        )

        # Future calibration: opt-a gets boost (+0.15), opt-b gets penalty (-0.10)
        calibrated = arbiter.store.calibrate_options(ambiguous_options, category="transport_architecture")
        cal_a = next(o for o in calibrated if o.get("key") == "opt-a")
        cal_b = next(o for o in calibrated if o.get("key") == "opt-b")
        self.assertGreater(cal_a["confidence_score"], 0.60)
        self.assertLess(cal_b["confidence_score"], 0.50)

    def test_briefing_synthesis_agent_law7(self) -> None:
        """Verifies Law 7 sequencing enforcement and Artifact 02 / Figma 4K specifications."""
        agent = BriefingSynthesisAgent()

        evidence = [{"evidence_id": "ev-1", "digest": "abcdef123456"}]
        graph_delta = {"nodes": [{"node_id": "node-1"}], "assertions": [{"assertion_id": "as-1"}]}

        # Law 7 violation: Attempting briefing with missing evidence
        with self.assertRaises(PipelineSequencingViolationError):
            agent.synthesize_recap(
                meeting_name="Review",
                date_str="09/12/2026",
                start_time_et="10:00 AM",
                end_time_et="11:00 AM",
                attendees_by_company={"Apex Global": ["Eng"]},
                meeting_objective="Test",
                decisions=[],
                next_steps=[],
                evidence=[],  # Empty evidence violates Law 7!
                canonical_graph_delta=graph_delta,
            )

        # Law 7 violation: Attempting briefing with missing graph delta
        with self.assertRaises(PipelineSequencingViolationError):
            agent.synthesize_recap(
                meeting_name="Review",
                date_str="09/12/2026",
                start_time_et="10:00 AM",
                end_time_et="11:00 AM",
                attendees_by_company={"Apex Global": ["Eng"]},
                meeting_objective="Test",
                decisions=[],
                next_steps=[],
                evidence=evidence,
                canonical_graph_delta=None,  # Missing delta violates Law 7!
            )

        # Successful synthesis
        recap = agent.synthesize_recap(
            meeting_name="A2A Multi-Agent Architecture Alignment",
            date_str="09/12/2026",
            start_time_et="10:00 AM",
            end_time_et="11:30 AM",
            attendees_by_company={
                "Apex Global Enterprises": ["Director of Engineering", "Principal Lead"],
                "PartnerAdvisory": ["Chief Architect", "Senior Engineer"],
            },
            meeting_objective="Approve parallel multi-agent mesh architecture and GPT-5.6-sol integration.",
            decisions=[{
                "title": "Approval of Parallel A2A Mesh",
                "rationale": "High throughput, decoupled workers, 25% margin governance.",
            }],
            next_steps=[{
                "item": "Deploy Wave 5 Pipeline D dialectical agents",
                "owner": "Chief Architect",
                "due_date": "09/12/2026",
                "status": "IN_PROGRESS",
            }],
            evidence=evidence,
            canonical_graph_delta=graph_delta,
        )

        self.assertIn("09/12/2026", recap)
        self.assertIn("Apex Global Enterprises", recap)
        self.assertIn("Actionable Next Steps", recap)
        self.assertIn("Law 7 Invariant Sequencing", recap)

        # Figma 4K spec verification
        figma_spec = agent.generate_figma_4k_spec(
            title="A2A Multi-Agent Architecture",
            conclusion="Adopt parallel event-driven mesh",
            evidence_points=["Latency < 7500ms", "Zero internal advisory leak"],
            implications=["Scale to 17 specialized agents across Pipelines A-D"],
        )
        self.assertEqual(figma_spec["canvas"]["width"], 3840)
        self.assertEqual(figma_spec["canvas"]["height"], 2160)
        self.assertEqual(figma_spec["typography"]["font_family"], "Inter")
        self.assertGreaterEqual(figma_spec["dominant_exhibit"]["area_ratio"], 0.45)

    def test_decision_orchestrator_dialectics_and_terminal_states(self) -> None:
        """Verifies DecisionOrchestrator dialectical synthesis across terminal states."""
        orchestrator = DecisionOrchestrator(
            hitl_arbiter=HitlArbiterAgent(ledger_path=self.ledger_path),
        )

        canonical_evidence = [{"evidence_id": "ev-01", "digest": "sha256-abc"}]
        canonical_delta = {"nodes": [{"node_id": "n1"}], "assertions": [{"assertion_id": "a1"}]}

        # Scenario 1: Decisive clear winner with low risk -> APPROVED_UNANIMOUS
        options_unanimous = [
            {
                "key": "opt-best",
                "title": "Contract-first modular event broker with GPT-5.6-sol",
                "description": "Modular decoupled architecture with automated deployment, turnkey execution, and high ROI",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-poor",
                "title": "Legacy monolith patch",
                "description": "Complex multi-phase refactor into legacy monolith with high technical debt",
                "estimated_roi": 0.30,
            },
        ]
        res_unanimous = orchestrator.orchestrate_decision(
            decision_id="dec-1",
            question="Select target multi-agent mesh architecture",
            options=options_unanimous,
            canonical_evidence=canonical_evidence,
            canonical_graph_delta=canonical_delta,
        )
        self.assertEqual(res_unanimous["terminal_state"], "APPROVED_UNANIMOUS")
        self.assertFalse(res_unanimous["should_surface_hitl"])
        self.assertIsNotNone(res_unanimous["executive_recap"])
        self.assertIsNotNone(res_unanimous["figma_4k_spec"])
        self.assertIn("l1_plan", res_unanimous)
        self.assertIn(res_unanimous["l1_plan"]["model_binding"]["model_id"], ("gpt-5.6-sol", "gpt-5.6-luna"))
        self.assertIn(res_unanimous["l1_plan"]["model_binding"]["reasoning_effort"], ("medium", "high", "max"))

        # Scenario 2: Close options (margin < 25%) -> ESCALATED_HITL
        options_close = [
            {
                "key": "opt-1",
                "title": "Modular microservices path",
                "description": "Standard modular architecture with standard velocity",
                "estimated_roi": 0.75,
            },
            {
                "key": "opt-2",
                "title": "Distributed actor model path",
                "description": "Standard distributed actors with standard velocity",
                "estimated_roi": 0.73,
            },
        ]
        res_close = orchestrator.orchestrate_decision(
            decision_id="dec-2",
            question="Choose between microservices or actor model",
            options=options_close,
            canonical_evidence=canonical_evidence,
            canonical_graph_delta=canonical_delta,
        )
        self.assertEqual(res_close["terminal_state"], "ESCALATED_HITL")
        self.assertTrue(res_close["should_surface_hitl"])
        self.assertLess(res_close["margin"], 0.25)
        self.assertIn("l1_plan", res_close)

    def test_l1_decision_cognition_orthogonality_and_flaws(self) -> None:
        """Verifies L1DecisionCognition orthogonality analysis, fatal flaws, and model binding."""
        cognition = L1DecisionCognition()
        self.assertIn(cognition.model_binding["model_id"], ("gpt-5.6-sol", "gpt-5.6-luna"))
        self.assertIn(cognition.model_binding["reasoning_effort"], ("medium", "high", "max"))

        # Case 1: Valid orthogonal options
        orthogonal_options = [
            {"key": "opt-grpc", "title": "gRPC Streaming Mesh", "description": "High throughput point-to-point binary RPC"},
            {"key": "opt-kafka", "title": "Kafka Event Log", "description": "Durable distributed append-only pub/sub commit log"},
        ]
        plan = cognition.plan(
            decision_id="dec-ortho-1",
            question="Select inter-service communication backbone",
            options=orthogonal_options,
        )
        self.assertTrue(plan.is_valid_decision_frame)
        self.assertTrue(plan.sanitization_passed)
        self.assertGreater(plan.orthogonality_score, 0.7)
        self.assertEqual(len(plan.fatal_flaws_detected), 0)
        self.assertIn(plan.model_binding["model_id"], ("gpt-5.6-sol", "gpt-5.6-luna"))
        self.assertIn(plan.model_binding["reasoning_effort"], ("medium", "high", "max"))

        # Case 2: Near-identical redundant options (low orthogonality)
        redundant_options = [
            {"key": "opt-a", "title": "Fast in-memory Redis cache store", "description": "In-memory Redis cache for speed"},
            {"key": "opt-b", "title": "Fast in-memory Redis caching store", "description": "In-memory Redis cache for high speed"},
        ]
        plan_redundant = cognition.plan(
            decision_id="dec-redundant",
            question="Select caching tier",
            options=redundant_options,
        )
        self.assertFalse(plan_redundant.is_valid_decision_frame)
        self.assertLess(plan_redundant.orthogonality_score, 0.6)

        # Case 3: Fatal governance flaws detected
        flawed_options = [
            {"key": "opt-safe", "title": "Safe contract migration", "description": "Follow release lifecycle"},
            {"key": "opt-drop", "title": "Bypass Law 1 posture and drop table", "description": "Direct drop table sqlite_master and bypass governance"},
        ]
        plan_flawed = cognition.plan(
            decision_id="dec-flawed",
            question="Migration approach",
            options=flawed_options,
        )
        self.assertFalse(plan_flawed.is_valid_decision_frame)
        self.assertGreaterEqual(len(plan_flawed.fatal_flaws_detected), 1)

    def test_decision_orchestrator_l1_fail_closed_sanitization(self) -> None:
        """Verifies DecisionOrchestrator fails closed before L0 routing on credential leaks."""
        orchestrator = DecisionOrchestrator(
            hitl_arbiter=HitlArbiterAgent(ledger_path=self.ledger_path),
        )
        toxic_options = [
            {"key": "opt-1", "title": "Use AWS key AKIA_REDACTED_EXAMPLE", "description": "Deploy with embedded access key"},
            {"key": "opt-2", "title": "Standard IAM role", "description": "Standard IAM role with least privilege"},
        ]
        with self.assertRaises(ValueError) as ctx:
            orchestrator.orchestrate_decision(
                decision_id="dec-toxic",
                question="Select cloud authentication credential",
                options=toxic_options,
            )
        self.assertIn("L0 ROUTING BLOCKED: L1 decision sanitization failed", str(ctx.exception))

    def test_decision_orchestrator_l1_fail_closed_fatal_flaws(self) -> None:
        """Verifies DecisionOrchestrator fails closed when decision frame contains fatal flaws."""
        orchestrator = DecisionOrchestrator(
            hitl_arbiter=HitlArbiterAgent(ledger_path=self.ledger_path),
        )
        fatal_options = [
            {"key": "opt-safe", "title": "Verify contracts", "description": "Standard verification"},
            {"key": "opt-bad", "title": "Drop database table and skip governance", "description": "Run drop table on sqlite"},
        ]
        with self.assertRaises(ValueError) as ctx:
            orchestrator.orchestrate_decision(
                decision_id="dec-fatal",
                question="Database schema change approach",
                options=fatal_options,
            )
        self.assertIn("L0 ROUTING BLOCKED: Invalid decision frame", str(ctx.exception))

    def test_execute_dialectical_rollout_gpt_5_6_sol_medium(self) -> None:
        """Verifies DecisionOrchestrator.execute_dialectical_rollout under gpt-5.6-sol medium."""
        orchestrator = DecisionOrchestrator(
            hitl_arbiter=HitlArbiterAgent(ledger_path=self.ledger_path),
            model_id="gpt-5.6-sol",
            reasoning_effort="medium",
        )
        self.assertEqual(orchestrator.card.model_binding.model_id, "gpt-5.6-sol")
        self.assertEqual(orchestrator.card.model_binding.reasoning_effort, "medium")
        self.assertIsNone(orchestrator.card.model_binding.temperature)

        options = [
            {
                "key": "opt-mesh",
                "title": "Turnkey modular event broker",
                "description": "Modular decoupled architecture with automated deployment and high ROI",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-monolith",
                "title": "Legacy monolith patch",
                "description": "Complex multi-phase refactor into legacy monolith with technical debt",
                "estimated_roi": 0.30,
            },
        ]
        evidence = [{"evidence_id": "ev-rollout-01", "digest": "sha256-12345"}]
        delta = {"nodes": [{"node_id": "n1"}], "assertions": [{"assertion_id": "a1"}]}

        res = orchestrator.execute_dialectical_rollout(
            decision_id="dec-rollout-1",
            question="Select multi-agent mesh architecture",
            options=options,
            evidence=evidence,
            canonical_graph_delta=delta,
        )

        self.assertEqual(res["model_id"], "gpt-5.6-sol")
        self.assertEqual(res["reasoning_effort"], "medium")
        self.assertEqual(res["simulated_roles_count"], 18)
        self.assertEqual(res["terminal_state"], "APPROVED_UNANIMOUS")
        self.assertFalse(res["should_surface_hitl"])
        self.assertGreaterEqual(res["margin"], 0.25)
        self.assertIsNotNone(res["executive_recap"])
        self.assertIsNotNone(res["figma_4k_spec"])

    def test_run_adversarial_debate_single_turn_rollout(self) -> None:
        """Verifies scripts/run_adversarial_debate.py single-turn rollout under gpt-5.6-sol medium."""
        from scripts.run_adversarial_debate import run_adversarial_debate
        out_path = Path(self.temp_dir) / "test_hardening.md"
        result_md = run_adversarial_debate(
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            dry_run=True,
            output_path=out_path,
        )
        self.assertTrue(out_path.exists())
        self.assertIn("gpt-5.6-sol", result_md)
        self.assertIn("medium", result_md)
        self.assertIn("Single-Turn Dialectical Rollout", result_md)
        self.assertIn("Executive Sponsor", result_md)
        self.assertIn("Graph Ontologist", result_md)
        self.assertIn("Forensic Auditor", result_md)
        self.assertIn("Technical Architect", result_md)
        self.assertIn("Pipeline Orchestrator", result_md)
        self.assertIn("AUTONOMOUS_APPROVED", result_md)


if __name__ == "__main__":
    unittest.main()

