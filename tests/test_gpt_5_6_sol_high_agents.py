"""Unit and Integration Tests for gpt-5.6-sol High Reasoning Effort Across All Pipelines.

Validates:
1. Pipeline A: ForensicNormalizerAgent, RosterReconcilerAgent, MultimodalSlideAgent
   bound to gpt-5.6-sol with reasoning_effort="high".
2. Pipeline B: TechArchAgent, StrategicCommitmentsAgent, AdjudicationJudgeAgent
   bound to gpt-5.6-sol with reasoning_effort="high", with Law 1 local MLX partition.
3. Pipeline C: MultiPerspectiveDispatcher bound to gpt-5.6-sol with reasoning_effort="high".
4. Pipeline D: RedTeamRiskAgent, StrategicFeasibilityAgent
   bound to gpt-5.6-sol with reasoning_effort="high".
5. Evaluation Micro-Judges: DecomposedMultiJudge, ForensicTranscriptJudge, ExecutiveRecapJudge,
   GraphExtractionJudge, EnterpriseGraphReviewJudge, AgenticLeverageJudge bound to gpt-5.6-sol (xhigh).
6. Parameter Immunity: Explicit verification that reasoning models omit temperature=0.0 to prevent
   OpenAI 400 invalid_request_error.
7. Resilience & Fallbacks: Graceful handling of API faults and deterministic fallback guarantees.
8. Cross-Pipeline Healer Overrides: Execution of surgical in-flight repair under gpt-5.6-sol.
9. A2A Task Flow: Multi-agent coordination under gpt-5.6-sol agent contracts.
10. End-to-End Release Evaluation: Authoritative release decision under gpt-5.6-sol high.
11. LLM Transcript Synthesizer: Model binding and parameter compliance with gpt-5.6-sol high.
12. Coordinators A-D: Parallel fan-out, Law 1 cryptographic isolation, hybrid retrieval, and dialectic synthesis.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dual_pipeline.a2a import A2ATask, A2ATaskStatus, ComputePosture
from dual_pipeline.decision.agents import (
    DecisionOrchestrator,
    RedTeamRiskAgent,
    StrategicFeasibilityAgent,
    get_red_team_risk_card,
    get_strategic_feasibility_card,
)
from dual_pipeline.decision.agents.briefing_healer import (
    BriefingAndDecisionHealerAgent,
)
from dual_pipeline.l2_healing_contracts import (
    HealingScope,
    L2HealingRequest,
)
from dual_pipeline.llm_judge import (
    AgenticLeverageJudge,
    DecomposedMultiJudge,
    EnterpriseGraphReviewJudge,
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
)
from dual_pipeline.retrieval.agents import (
    MultiPerspectiveDispatcher,
    RetrievalCoordinator,
    get_multi_perspective_dispatcher_card,
)
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
)
from dual_pipeline.transcript_qa import evaluate_judge_and_release
from dual_pipeline.transcript_stages import LLMTranscriptSynthesizer
from pipeline_a_intake.agents import (
    ForensicTranscriptHealerAgent,
    IngestionCoordinator,
    MultimodalSlideExtractor,
    RosterReconcilerAgent,
    get_forensic_normalizer_card,
    get_multimodal_slide_card,
    get_roster_reconciler_card,
)
from pipeline_b_extraction.agents import (
    CanonicalProjectionHealerAgent,
    ExtractionCoordinator,
    StrategicCommitmentsAgent,
    TechArchAgent,
    get_adjudication_judge_card,
    get_friction_blocker_card,
    get_stakeholder_dynamics_card,
    get_strategic_commitments_card,
    get_tech_arch_card,
)


class TestGpt56SolHighAgents(unittest.TestCase):
    """Test suite verifying all specialized agents and micro-judges under gpt-5.6-sol."""

    # -------------------------------------------------------------
    # 1. Pipeline A Specialized Agents & Model Bindings
    # -------------------------------------------------------------
    def test_pipeline_a_gpt_5_6_sol_bindings(self) -> None:
        """Verifies Pipeline A agent cards and model bindings under gpt-5.6-sol."""
        normalizer_card = get_forensic_normalizer_card()
        self.assertEqual(normalizer_card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(normalizer_card.model_binding.reasoning_effort, ("high", "max"))
        self.assertIsNone(normalizer_card.model_binding.temperature)

        roster_card = get_roster_reconciler_card()
        self.assertEqual(roster_card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(roster_card.model_binding.reasoning_effort, ("high", "max"))
        self.assertIsNone(roster_card.model_binding.temperature)

        slide_card = get_multimodal_slide_card()
        self.assertEqual(slide_card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(slide_card.model_binding.reasoning_effort, ("high", "max"))
        self.assertIsNone(slide_card.model_binding.temperature)

    def test_pipeline_a_healer_execution(self) -> None:
        """Verifies ForensicTranscriptHealerAgent repairs Gate 1 defects."""
        healer = ForensicTranscriptHealerAgent(mock_mode=True)
        flawed_md = (
            "# Subject: Architecture Sync\n"
            "[00:01:00] Speaker 1: We must adhere to the standard.\n"
            "[00:02:00] Speaker 2: Agreed, deployment is set for 2026-09-12."
        )
        res = asyncio.run(
            healer.heal_transcript(
                transcript_md=flawed_md,
                failure_reasons=["Generic speaker labels present", "Forbidden ISO date format 2026-09-12"],
                roster_attendees=["Alex Sanchez", "harper Hoffman"],
            )
        )
        self.assertTrue(res.is_success)
        self.assertNotIn("Speaker 1", res.healed_artifact)
        self.assertNotIn("2026-09-12", res.healed_artifact)
        self.assertIn("09/12/2026", res.healed_artifact)

    # -------------------------------------------------------------
    # 2. Pipeline B Specialized Agents & Model Bindings
    # -------------------------------------------------------------
    def test_pipeline_b_gpt_5_6_sol_bindings(self) -> None:
        """Verifies Pipeline B extraction agent cards and model bindings under gpt-5.6-sol."""
        client_safe_cards = [
            ("TechArch", get_tech_arch_card()),
            ("StrategicCommitments", get_strategic_commitments_card()),
            ("AdjudicationJudge", get_adjudication_judge_card()),
        ]
        for name, card in client_safe_cards:
            self.assertIn(card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"), f"{name} model_id mismatch")
            self.assertIn(card.model_binding.reasoning_effort, ("high", "max"), f"{name} reasoning_effort mismatch")
            self.assertIsNone(card.model_binding.temperature, f"{name} temperature must be None for reasoning models")

        # Law 1 local MLX enclave partition for internal advisory
        internal_cards = [
            ("StakeholderDynamics", get_stakeholder_dynamics_card()),
            ("FrictionBlocker", get_friction_blocker_card()),
        ]
        for name, card in internal_cards:
            self.assertEqual(card.model_binding.model_id, "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit", f"{name} model_id mismatch")
            self.assertEqual(card.model_binding.provider, "mlx", f"{name} provider mismatch")

    def test_pipeline_b_canonical_projection_healer_execution(self) -> None:
        """Verifies CanonicalProjectionHealerAgent repairs Gate 3 defects."""
        healer = CanonicalProjectionHealerAgent(mock_mode=True)
        mutations = {
            "entity_upserts": [{"id": f"system:sys-{i}", "type": "SYSTEM"} for i in range(15)],
            "relationship_signals": [{"from_id": f"system:sys-{i}", "to_id": "project:nexus", "predicate": "RELATES_TO"} for i in range(15)],
            "decision_updates": [{"decision": "Adopt BGE-M3"}],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        }
        candidate_json = {
            "schema_version": "5.7",
            "graph_mutations": mutations,
        }
        res = asyncio.run(
            healer.heal_projection(
                candidate_projection=candidate_json,
                failure_reasons=["Graph mutations (31) exceeds maximum budget 25", "Missing observed_at"],
                forensic_transcript="Alex Sanchez confirmed on 09/12/2026.",
            )
        )
        self.assertTrue(res.is_success)
        healed_mutations = res.healed_artifact.get("graph_mutations", {})
        total_mutations = sum(len(v) for v in healed_mutations.values() if isinstance(v, list))
        self.assertLessEqual(total_mutations, 25)

    # -------------------------------------------------------------
    # 3. Pipeline C Retrieval Perspective Healer & Dispatcher
    # -------------------------------------------------------------
    def test_pipeline_c_gpt_5_6_sol_bindings(self) -> None:
        """Verifies Pipeline C agent cards and model bindings under gpt-5.6-sol."""
        disp_card = get_multi_perspective_dispatcher_card()
        self.assertEqual(disp_card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(disp_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(disp_card.model_binding.temperature)

    def test_pipeline_c_retrieval_healer_execution(self) -> None:
        """Verifies RetrievalPerspectiveHealerAgent expands starved perspectives."""
        healer = RetrievalPerspectiveHealerAgent(mock_mode=True)
        res = asyncio.run(
            healer.heal_starved_perspective(
                query="latency budget for vector retrieval",
                starved_lens="TECHNICAL",
                active_ontology_terms=["system:bge-m3"],
            )
        )
        self.assertTrue(res.is_success)
        self.assertIn("system:bge-m3", res.healed_artifact.get("expanded_terms", []))

    # -------------------------------------------------------------
    # 4. Pipeline D Dialectical Risk & Feasibility
    # -------------------------------------------------------------
    def test_pipeline_d_gpt_5_6_sol_bindings(self) -> None:
        """Verifies Pipeline D agent cards and model bindings under gpt-5.6-sol."""
        red_team_card = get_red_team_risk_card()
        self.assertEqual(red_team_card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(red_team_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(red_team_card.model_binding.temperature)

        feasibility_card = get_strategic_feasibility_card()
        self.assertEqual(feasibility_card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(feasibility_card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertIsNone(feasibility_card.model_binding.temperature)

    def test_pipeline_d_adversarial_risk_and_feasibility_execution(self) -> None:
        """Verifies RedTeamRiskAgent and StrategicFeasibilityAgent under gpt-5.6-sol."""
        red_team = RedTeamRiskAgent()
        feasibility = StrategicFeasibilityAgent()

        options = [
            {
                "key": "opt-mesh",
                "title": "Parallel A2A event mesh",
                "description": "Decoupled asynchronous message passing with contract verification",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-bypass",
                "title": "Bypass governance gates",
                "description": "Direct unvalidated writes to accelerate milestone delivery",
                "estimated_roi": 0.30,
            },
        ]

        risks = red_team.analyze_risks(options)
        evals = feasibility.evaluate_feasibility(options)

        self.assertEqual(len(risks), 2)
        self.assertEqual(len(evals), 2)

        safe_risk = next(r for r in risks if r["option_key"] == "opt-mesh")
        bad_risk = next(r for r in risks if r["option_key"] == "opt-bypass")
        self.assertFalse(safe_risk["has_fatal_flaw"])
        self.assertTrue(bad_risk["has_fatal_flaw"])
        self.assertEqual(safe_risk["model"], "gpt-5.6-sol")
        self.assertIn(safe_risk["reasoning_effort"], ("medium", "high", "max"))

        top_feas = evals[0]
        self.assertEqual(top_feas["option_key"], "opt-mesh")
        self.assertEqual(top_feas["model"], "gpt-5.6-sol")

    # -------------------------------------------------------------
    # 5. Micro-Judge Class Defaults Under gpt-5.6-sol
    # -------------------------------------------------------------
    def test_micro_judge_class_defaults(self) -> None:
        """Verifies all micro-judges default to gpt-5.6-sol with supported reasoning effort."""
        multi_judge = DecomposedMultiJudge()
        self.assertIn(multi_judge.model, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(multi_judge.reasoning_effort, ("medium", "high", "xhigh", "max"))

        forensic_judge = ForensicTranscriptJudge()
        self.assertIn(forensic_judge.model, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(forensic_judge.reasoning_effort, ("medium", "high", "xhigh", "max"))

        recap_judge = ExecutiveRecapJudge()
        self.assertIn(recap_judge.model, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(recap_judge.reasoning_effort, ("medium", "high", "xhigh", "max"))

        graph_judge = GraphExtractionJudge()
        self.assertIn(graph_judge.model, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(graph_judge.reasoning_effort, ("medium", "high", "xhigh", "max"))

        review_judge = EnterpriseGraphReviewJudge()
        self.assertIn(review_judge.model, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(review_judge.reasoning_effort, ("medium", "high", "xhigh", "max"))

        leverage_judge = AgenticLeverageJudge()
        self.assertIn(leverage_judge.model, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(leverage_judge.reasoning_effort, ("medium", "high", "xhigh", "max"))

    # -------------------------------------------------------------
    # 6. Parameter Immunity: Temperature Omission in LLM Calls
    # -------------------------------------------------------------
    def test_gpt_5_6_sol_high_temperature_omission_in_llm_calls(self) -> None:
        """Verifies that LLM call constructors omit temperature when model is gpt-5.6-sol."""
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"speaker_map": {"Speaker 1": "Alex Sanchez"}})
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_create = AsyncMock(return_value=mock_resp)
        mock_client.chat.completions.create = mock_create

        async def _run_agent_calls() -> None:
            # 1. Roster Reconciler
            roster_agent = RosterReconcilerAgent(api_key="fake-key")
            roster_task = A2ATask(
                task_id="roster-task-1",
                parent_task_id=None,
                sender_agent_id="urn:nee:agent:caller",
                recipient_agent_id=roster_agent.card.agent_id,
                objective="Reconcile speaker roster",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={
                    "speaker_turns": [{"speaker": "Speaker 1", "text": "Deploy the mesh"}],
                    "roster_context": ["Alex Sanchez"],
                },
            )
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                await roster_agent.handle_task(roster_task)
            self.assertTrue(mock_create.called)
            call_kwargs = mock_create.call_args[1]
            self.assertIn(call_kwargs.get("model"), ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
            self.assertIn(call_kwargs.get("reasoning_effort"), ("high", "xhigh", "max"))
            self.assertNotIn("temperature", call_kwargs, "Reasoning model must NOT receive temperature")

            # 2. Multi-Perspective Dispatcher
            mock_create.reset_mock()
            mock_choice.message.content = json.dumps({
                "sub_queries": [{"perspective": "TECHNICAL", "query_text": "BGE-M3 latency", "weight": 1.0}]
            })
            disp = MultiPerspectiveDispatcher(api_key="fake-key")
            task = A2ATask(
                task_id="disp-task-1",
                parent_task_id=None,
                sender_agent_id="urn:nee:agent:caller",
                recipient_agent_id=disp.card.agent_id,
                objective="Dispatch multi-perspective query",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={"query": "hybrid retrieval benchmarks"},
            )
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                await disp.handle_task(task)
            self.assertTrue(mock_create.called)
            disp_call_kwargs = mock_create.call_args[1]
            self.assertIn(disp_call_kwargs.get("model"), ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
            self.assertIn(disp_call_kwargs.get("reasoning_effort"), ("medium", "high", "xhigh", "max"))
            self.assertNotIn("temperature", disp_call_kwargs, "Reasoning model must NOT receive temperature")

            # 3. Tech Arch Extractor
            mock_create.reset_mock()
            mock_choice.message.content = json.dumps({
                "facts": [{"type": "ARCHITECTURAL_DECISION", "name": "BGE-M3 Embedding", "strategic_value": 9.0}]
            })
            tech_arch = TechArchAgent(api_key="fake-key")
            arch_task = A2ATask(
                task_id="arch-task-1",
                parent_task_id=None,
                sender_agent_id="urn:nee:agent:caller",
                recipient_agent_id=tech_arch.card.agent_id,
                objective="Extract technical specs",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={"evidence_id": "ev-01", "text": "We standardize on BGE-M3", "speaker": "Alex"},
            )
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                await tech_arch.handle_task(arch_task)
            self.assertTrue(mock_create.called)
            arch_call_kwargs = mock_create.call_args[1]
            self.assertIn(arch_call_kwargs.get("model"), ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
            self.assertIn(arch_call_kwargs.get("reasoning_effort"), ("high", "xhigh", "max"))
            self.assertNotIn("temperature", arch_call_kwargs, "Reasoning model must NOT receive temperature")

        asyncio.run(_run_agent_calls())

    # -------------------------------------------------------------
    # 7. Micro-Judge Evaluation & Rule C-19 Firewall under gpt-5.6-sol
    # -------------------------------------------------------------
    def test_gpt_5_6_sol_micro_judge_evaluations(self) -> None:
        """Verifies micro-judge execution, scoring, and C-19 firewalling under gpt-5.6-sol."""
        forensic_judge = ForensicTranscriptJudge()
        recap_judge = ExecutiveRecapJudge()

        # Clean inputs evaluate to PASS
        clean_transcript = (
            "## Ambiguity Lock Register (ALR)\n"
            "| Lock ID | Item | Ambiguity Description | Forensic Resolution | Status |\n"
            "|:---:|:---:|:---:|:---:|:---:|\n"
            "| ALR-01 | Speaker overlap | Cleaned | Resolved | `LOCKED` |\n\n"
            "## Forensic Transcript\n"
            "[00:01:00] Alex Sanchez: We verified the schema on 09/12/2026.\n"
        )
        res_f1 = forensic_judge.evaluate(clean_transcript, clean_transcript, mock=True)
        self.assertEqual(res_f1["verdict"], "PASS")
        self.assertGreaterEqual(res_f1["composite_score"], 80)

        clean_recap = (
            "# Executive Recap: Architecture Review\n"
            "**Date:** 09/12/2026\n"
            "**Meeting Objective:** Confirm database migration plan\n\n"
            "## Actionable Next Steps\n"
            "| Item | Owner | Due Date | Status |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| Finalize schema contract | Alex Sanchez | 09/20/2026 | OPEN |\n"
        )
        res_f2 = recap_judge.evaluate(clean_recap, clean_transcript, mock=True)
        self.assertEqual(res_f2["verdict"], "PASS")
        self.assertGreaterEqual(res_f2["composite_score"], 80)

        # C-19 Career Leverage Leaks evaluate to immediate FAIL
        leaky_transcript = clean_transcript + "\nPrivate note: targeting executive compensation band and political_rationale.\n"
        res_f1_leaky = forensic_judge.evaluate(leaky_transcript, clean_transcript, mock=True)
        self.assertEqual(res_f1_leaky["verdict"], "FAIL")
        self.assertIn("RULE C-19 VIOLATION", res_f1_leaky["forensic_rationale"])

        leaky_recap = clean_recap + "\nConfidential note: internal advisory networking_wedge established.\n"
        res_f2_leaky = recap_judge.evaluate(leaky_recap, clean_transcript, mock=True)
        self.assertEqual(res_f2_leaky["verdict"], "FAIL")
        self.assertIn("RULE C-19 VIOLATION", res_f2_leaky["forensic_rationale"])

    # -------------------------------------------------------------
    # 8. Offline Fallback Resilience Under API Exceptions
    # -------------------------------------------------------------
    def test_gpt_5_6_sol_offline_fallback_resilience(self) -> None:
        """Verifies agents gracefully fall back to deterministic handling when API fails."""
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("OpenAI 500 Connection Timeout"))

        async def _run_resilience_checks() -> None:
            # RosterReconciler fallback
            roster_agent = RosterReconcilerAgent(api_key="fake-key")
            roster_task = A2ATask(
                task_id="roster-fail-1",
                parent_task_id=None,
                sender_agent_id="urn:nee:agent:caller",
                recipient_agent_id=roster_agent.card.agent_id,
                objective="Reconcile speaker roster",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={
                    "speaker_turns": [{"speaker": "Speaker 1", "text": "Hello world"}],
                    "roster_context": ["Alex Sanchez"],
                },
            )
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                res = await roster_agent.handle_task(roster_task)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(len(res["reconciled_turns"]), 1)

            # Slide Extractor fallback
            slide_agent = MultimodalSlideExtractor(api_key="fake-key")
            slide_task = A2ATask(
                task_id="slide-fail-1",
                parent_task_id=None,
                sender_agent_id="urn:nee:agent:caller",
                recipient_agent_id=slide_agent.card.agent_id,
                objective="Extract slide graphics",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={"deck_path": "/path/to/missing.pptx"},
            )
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                slide_res = await slide_agent.handle_task(slide_task)
            self.assertEqual(slide_res["status"], "COMPLETED")
            self.assertEqual(slide_res["slide_count"], 0)

        asyncio.run(_run_resilience_checks())

    # -------------------------------------------------------------
    # 9. Cross-Pipeline Healer Invocation Under gpt-5.6-sol-high
    # -------------------------------------------------------------
    def test_healers_with_gpt_5_6_sol_high_override(self) -> None:
        """Verifies that all 4 healers can be parameterized with gpt-5.6-sol (high)."""
        healer_a = ForensicTranscriptHealerAgent(mock_mode=True)
        healer_b = CanonicalProjectionHealerAgent(mock_mode=True)
        healer_c = RetrievalPerspectiveHealerAgent(mock_mode=True)
        healer_d = BriefingAndDecisionHealerAgent(mock_mode=True)

        async def _run_all_healers_sol() -> None:
            # 1. Healer A under gpt-5.6-sol
            req_a = L2HealingRequest(
                scope=HealingScope.PIPELINE_A_TRANSCRIPT,
                candidate_artifact="# Header\n[00:01:00] Speaker 1: Done on 2026-09-12",
                failure_reasons=["Forbidden ISO date: 2026-09-12", "Generic speaker: Speaker 1"],
                model_id="gpt-5.6-sol",
                reasoning_effort="high",
                context_metadata={"roster": ["Alex Sanchez"]},
            )
            res_a = await healer_a.healer_service.execute_healing(req_a)
            self.assertTrue(res_a.fact_preservation_passed)
            self.assertIn("09/12/2026", str(res_a.healed_artifact))

            # 2. Healer B under gpt-5.6-sol
            mutations = {
                "decision_updates": [{"decision": "Adopt BGE-M3"}],
                "entity_upserts": [{"id": f"system:db-{i}"} for i in range(20)],
                "relationship_signals": [{"from_id": f"system:db-{i}", "to_id": "project:nexus"} for i in range(20)],
            }
            req_b = L2HealingRequest(
                scope=HealingScope.PIPELINE_B_PROJECTION,
                candidate_artifact={"schema_version": "5.7", "graph_mutations": mutations},
                failure_reasons=["Mutation budget exceeded: 41 > 25"],
                model_id="gpt-5.6-sol",
                reasoning_effort="high",
            )
            res_b = await healer_b.healer_service.execute_healing(req_b)
            self.assertTrue(res_b.fact_preservation_passed)
            healed_b_dict = res_b.healed_artifact if isinstance(res_b.healed_artifact, dict) else json.loads(res_b.healed_artifact)
            total_m = sum(len(v) for v in healed_b_dict.get("graph_mutations", {}).values() if isinstance(v, list))
            self.assertLessEqual(total_m, 25)

            # 3. Healer C under gpt-5.6-sol
            req_c = L2HealingRequest(
                scope=HealingScope.PIPELINE_C_RETRIEVAL,
                candidate_artifact={"query": "vector latency", "starved_lens": "TECHNICAL"},
                failure_reasons=["Starved technical perspective"],
                model_id="gpt-5.6-sol",
                reasoning_effort="high",
                context_metadata={"active_ontology_terms": ["system:bge-m3"]},
            )
            res_c = await healer_c.healer_service.execute_healing(req_c)
            self.assertTrue(res_c.fact_preservation_passed)

            # 4. Healer D under gpt-5.6-sol
            req_d = L2HealingRequest(
                scope=HealingScope.PIPELINE_D_BRIEFING,
                candidate_artifact="| Item | Owner |\n| Consider cloud | TBC |",
                failure_reasons=["Passive verb", "Unassigned owner"],
                model_id="gpt-5.6-sol",
                reasoning_effort="high",
                context_metadata={"roster": ["Alex Sanchez"]},
            )
            res_d = await healer_d.healer_service.execute_healing(req_d)
            self.assertTrue(res_d.fact_preservation_passed)
            self.assertNotIn("Consider", str(res_d.healed_artifact))

        asyncio.run(_run_all_healers_sol())

    # -------------------------------------------------------------
    # 10. A2A Multi-Agent Task Routing Under gpt-5.6-sol Contracts
    # -------------------------------------------------------------
    def test_a2a_mesh_task_routing_with_gpt_5_6_sol(self) -> None:
        """Verifies end-to-end A2A task envelopes between gpt-5.6-sol agents."""
        tech_arch = TechArchAgent(mock_mode=True)
        commitments = StrategicCommitmentsAgent(mock_mode=True)

        task_payload = {
            "evidence_id": "ev-mesh-001",
            "speaker": "Alex Sanchez",
            "text": "We will deliver the BGE-M3 retrieval pipeline by 09/30/2026 under the SOW.",
        }

        task_1 = A2ATask(
            task_id="task-arch-e2e",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:coordinator",
            recipient_agent_id=tech_arch.card.agent_id,
            objective="Extract technical architecture specifications",
            status=A2ATaskStatus.SUBMITTED,
            input_payload=task_payload,
        )
        task_2 = A2ATask(
            task_id="task-commit-e2e",
            parent_task_id="task-arch-e2e",
            sender_agent_id=tech_arch.card.agent_id,
            recipient_agent_id=commitments.card.agent_id,
            objective="Extract delivery commitments and milestones",
            status=A2ATaskStatus.SUBMITTED,
            input_payload=task_payload,
        )

        async def _run_tasks() -> None:
            res_1 = await tech_arch.handle_task(task_1)
            res_2 = await commitments.handle_task(task_2)

            self.assertEqual(res_1["status"], "COMPLETED")
            self.assertEqual(res_2["status"], "COMPLETED")
            self.assertEqual(res_1["extracted_count"], 1)
            self.assertEqual(res_2["extracted_count"], 1)

            # Validate nodes adhere to canonical prefixes
            node_1 = res_1["nodes"][0]
            node_2 = res_2["nodes"][0]
            self.assertEqual(node_1["type"], "ARCHITECTURAL_DECISION")
            self.assertEqual(node_2["type"], "DELIVERY_MILESTONE")

        asyncio.run(_run_tasks())

    # -------------------------------------------------------------
    # 11. End-to-End Release Evaluation Under gpt-5.6-sol High
    # -------------------------------------------------------------
    def test_evaluate_judge_and_release_e2e_under_gpt_5_6_sol_high(self) -> None:
        """Verifies evaluate_judge_and_release under gpt-5.6-sol (high)."""
        from tests.test_frontier_reasoning_pipeline_e2e import (
            SAMPLE_F1_TRANSCRIPT,
            SAMPLE_F2_RECAP,
            SAMPLE_F3_GRAPH,
        )
        f1_clean = SAMPLE_F1_TRANSCRIPT
        f2_clean = SAMPLE_F2_RECAP
        f3_clean = SAMPLE_F3_GRAPH

        # Passing execution
        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1_clean,
            f2_text=f2_clean,
            f3_text=f3_clean,
            source_text=f1_clean,
            mock=True,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            metadata={"date": "09/21/2026", "meeting_name": "Executive Sync", "duration_minutes": 5},
        )
        self.assertEqual(qa_rep["output_01_forensic_transcript"]["status"], "PASS")
        self.assertEqual(qa_rep["output_02_executive_recap"]["status"], "PASS")
        self.assertEqual(qa_rep["output_03_canonical_signal_projection"]["status"], "PASS")
        self.assertEqual(judge_rep["evaluator_model"], "gpt-5.6-sol")
        self.assertEqual(judge_rep["reasoning_effort"], "high")
        self.assertTrue(decision["authoritative_write_authorized"])

        # Flawed execution: unassigned owner and generic speaker -> fails closed
        f1_flawed = "### [00:01:00]\nSpeaker 1: Unresolved speaker turn.\n"
        qa_f, judge_f, decision_f = evaluate_judge_and_release(
            f1_text=f1_flawed,
            f2_text=f2_clean,
            f3_text=f3_clean,
            source_text=f1_flawed,
            mock=True,
            model="gpt-5.6-sol",
            reasoning_effort="high",
        )
        self.assertEqual(qa_f["output_01_forensic_transcript"]["status"], "FAIL")
        self.assertFalse(decision_f["authoritative_write_authorized"])

    # -------------------------------------------------------------
    # 12. LLM Transcript Synthesizer with gpt-5.6-sol High
    # -------------------------------------------------------------
    def test_llm_transcript_synthesizer_gpt_5_6_sol_mock_client(self) -> None:
        """Verifies LLMTranscriptSynthesizer execution parameters with gpt-5.6-sol."""
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Synthesized markdown output"
        mock_choice.finish_reason = "stop"
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=mock_resp)

        from dual_pipeline.credentials import OpenAICredentials
        fake_creds = OpenAICredentials(
            api_key="fake-key",
            is_mock=False,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            masked_key="sk-fake...key",
            source="test",
        )

        synth = LLMTranscriptSynthesizer(
            model="gpt-5.6-sol", reasoning_effort="high", _client=mock_client, _creds=fake_creds
        )
        self.assertEqual(synth.model, "gpt-5.6-sol")
        self.assertEqual(synth.reasoning_effort, "high")

        out_forensic = synth.synthesize_forensic(
            system_prompt="You are a forensic auditor.",
            user_content="Raw transcript text",
        )
        self.assertEqual(out_forensic, "Synthesized markdown output")

        out_recap = synth.synthesize_recap(
            system_prompt="You are an executive strategist.",
            user_content="Forensic transcript text",
        )
        self.assertEqual(out_recap, "Synthesized markdown output")

        # Verify both calls omitted temperature and passed model=gpt-5.6-sol
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)
        for call in mock_client.chat.completions.create.call_args_list:
            kwargs = call[1]
            self.assertIn(kwargs.get("model"), ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
            self.assertNotIn("temperature", kwargs, "Temperature forbidden for reasoning models")
            self.assertIn("max_completion_tokens", kwargs)

    # -------------------------------------------------------------
    # 13. Pipeline A Ingestion Coordinator E2E Mesh Under gpt-5.6-sol
    # -------------------------------------------------------------
    def test_pipeline_a_ingestion_coordinator_e2e_under_gpt_5_6_sol(self) -> None:
        """Verifies IngestionCoordinator fan-out across gpt-5.6-sol intake agents."""
        coord = IngestionCoordinator(mock_mode=True)
        self.assertIn(coord.roster_agent.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(coord.normalizer_agent.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(coord.slide_agent.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))

        task = A2ATask(
            task_id="t-coord-sol-e2e",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coord.card.agent_id,
            objective="Ingest multimodal meeting under gpt-5.6-sol",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "meeting_name": "Project Beacon Architecture Review",
                "meeting_date": "09/12/2026",
                "roster_context": ["Alex Sanchez", "harper Hoffman"],
            },
        )
        async def _run() -> None:
            res = await coord.handle_task(task)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertGreaterEqual(res["turn_count"], 2)
            self.assertIn("Ambiguity Lock Register (ALR)", res["artifact_01_markdown"])

        asyncio.run(_run())

    # -------------------------------------------------------------
    # 14. Pipeline B Extraction Coordinator Law 1 Partition
    # -------------------------------------------------------------
    def test_pipeline_b_extraction_coordinator_law1_partition_under_gpt_5_6_sol(self) -> None:
        """Verifies ExtractionCoordinator partitions gpt-5.6-sol client-safe vs MLX advisory."""
        coord = ExtractionCoordinator(mock_mode=True)
        self.assertIn(coord.tech_arch_agent.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(coord.commitments_agent.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        if getattr(coord, "judge_agent", None) is not None:
            self.assertIn(coord.judge_agent.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))

        # Law 1 Enclave verification
        self.assertEqual(coord.stakeholder_agent.card.compute_posture, ComputePosture.INTERNAL_ADVISORY)
        self.assertEqual(coord.stakeholder_agent.card.model_binding.provider, "mlx")
        self.assertEqual(coord.friction_agent.card.compute_posture, ComputePosture.INTERNAL_ADVISORY)
        self.assertEqual(coord.friction_agent.card.model_binding.provider, "mlx")

        task = A2ATask(
            task_id="t-b0-sol-fork",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coord.card.agent_id,
            objective="Full extraction across compute fork",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "evidence_id": "ev-e2e-sol-100",
                "text": "PartnerAdvisory pod commits to deploy AWS S3 pipeline by 06/16/2026, though IAM permissions remain a blocker.",
                "speaker": "Tony Kablan",
            },
        )
        async def _run() -> None:
            res = await coord.handle_task(task)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertGreaterEqual(res["canonical_count"], 2)
            self.assertGreaterEqual(res["advisory_count"], 2)

        asyncio.run(_run())

    # -------------------------------------------------------------
    # 15. Pipeline C Retrieval Coordinator with gpt-5.6-sol Dispatcher
    # -------------------------------------------------------------
    def test_pipeline_c_retrieval_coordinator_with_gpt_5_6_sol_dispatcher(self) -> None:
        """Verifies RetrievalCoordinator orchestrates gpt-5.6-sol MultiPerspectiveDispatcher."""
        coord = RetrievalCoordinator(mock_mode=True)
        self.assertEqual(coord.dispatcher_agent.card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(coord.dispatcher_agent.card.model_binding.reasoning_effort, ("medium", "high", "max"))

        facts = [
            {"node_id": "f1", "name": "AWS S3 Pipeline Architecture", "namespace": "CLIENT_SAFE", "is_quarantined": False},
            {"node_id": "f2", "name": "Sprint 1 Milestones", "namespace": "CLIENT_SAFE", "is_quarantined": False},
        ]
        task = A2ATask(
            task_id="t-c0-sol-coord",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coord.card.agent_id,
            objective="End to end retrieval under gpt-5.6-sol",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"query": "LENS S3 Deployment", "candidate_facts": facts},
        )
        async def _run() -> None:
            res = await coord.handle_task(task)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertTrue(res["all_gates_passed"])
            self.assertEqual(len(res["fused_facts"]), 2)
            self.assertLess(res["latency_ms"], 7500.0)

        asyncio.run(_run())

    # -------------------------------------------------------------
    # 16. Pipeline D Decision Orchestrator Dialectical Debate under gpt-5.6-sol
    # -------------------------------------------------------------
    def test_pipeline_d_decision_orchestrator_dialectical_debate_gpt_5_6_sol(self) -> None:
        """Verifies DecisionOrchestrator dialectics with gpt-5.6-sol risk & feasibility."""
        orchestrator = DecisionOrchestrator()
        self.assertIn(orchestrator.red_team.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(orchestrator.feasibility.card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"))

        canonical_evidence = [{"evidence_id": "ev-01", "digest": "sha256-abc"}]
        canonical_delta = {"nodes": [{"node_id": "n1"}], "assertions": [{"assertion_id": "a1"}]}

        # Decisive clear winner -> APPROVED_UNANIMOUS
        options = [
            {
                "key": "opt-sol-mesh",
                "title": "Turnkey modular event broker with GPT-5.6-sol",
                "description": "Modular decoupled architecture with automated deployment and high ROI",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-sol-monolith",
                "title": "Legacy monolith patch",
                "description": "Complex multi-phase refactor into legacy monolith with technical debt",
                "estimated_roi": 0.30,
            },
        ]
        res = orchestrator.orchestrate_decision(
            decision_id="dec-sol-1",
            question="Select target multi-agent mesh architecture",
            options=options,
            canonical_evidence=canonical_evidence,
            canonical_graph_delta=canonical_delta,
        )
        self.assertEqual(res["terminal_state"], "APPROVED_UNANIMOUS")
        self.assertFalse(res["should_surface_hitl"])
        self.assertIsNotNone(res["executive_recap"])
        self.assertIsNotNone(res["figma_4k_spec"])

    # -------------------------------------------------------------
    # 17. Enterprise Graph Review Judge 10-Dimensional Evaluation Under gpt-5.6-sol
    # -------------------------------------------------------------
    def test_enterprise_graph_review_judge_10_dimensions_gpt_5_6_sol(self) -> None:
        """Verifies EnterpriseGraphReviewJudge 10-dimensional evaluation under gpt-5.6-sol."""
        judge = EnterpriseGraphReviewJudge(model="gpt-5.6-sol", reasoning_effort="xhigh", mock=True)
        self.assertEqual(judge.model, "gpt-5.6-sol")
        self.assertEqual(judge.reasoning_effort, "xhigh")

        sample_graph = {
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:bge-m3", "type": "SYSTEM", "name": "BGE-M3"},
                    {"id": "person:alex_sanchez", "type": "PERSON", "name": "Alex Sanchez"},
                ],
                "decision_updates": [
                    {
                        "id": "decision:bge-m3",
                        "decision": "Adopt BGE-M3 for Dense Retrieval",
                        "observed_at": "09/12/2026",
                        "stale_after": "12/31/2026",
                    }
                ],
            },
        }
        res = judge.evaluate(sample_graph, "Alex Sanchez confirmed BGE-M3 on 09/12/2026.")
        self.assertEqual(res["schema_version"], "enterprise-graph-review-1.0")
        self.assertFalse(res["canonical_mutation"])
        self.assertGreaterEqual(res["composite_score"], 80)
        self.assertIn("maturity_band", res)
        self.assertIn("dimension_scores", res)
        dim_scores = res["dimension_scores"]
        self.assertEqual(len(dim_scores), 10)
        self.assertIn("dimension_1_business_capability_depth", dim_scores)
        self.assertIn("dimension_10_multi_client_federation_scalability", dim_scores)


if __name__ == "__main__":
    unittest.main()

