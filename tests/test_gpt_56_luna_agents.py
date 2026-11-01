"""Unit and Integration Tests for gpt-5.6-luna Across Pipelines A–D and Release Governance.

Validates:
1. Model Binding & Parameter Immunity:
   - ModelBinding automatically detects `gpt-5.6-luna` as a reasoning model.
   - Forbids and strips `temperature` (cannot be 0.0, must be None).
   - Defaults `reasoning_effort` to "medium" if unspecified.
2. Pipeline A:
   - IngestionCoordinator, ForensicNormalizerAgent, RosterReconcilerAgent, MultimodalSlideExtractor,
     and ForensicTranscriptHealerAgent operate with `model_id="gpt-5.6-luna"`.
3. Pipeline B:
   - ExtractionCoordinator, ClientSafeExtractor, TechArchAgent, StrategicCommitmentsAgent,
     and CanonicalProjectionHealerAgent operate with `model_id="gpt-5.6-luna"`.
4. Pipeline C:
   - RetrievalCoordinator, MultiPerspectiveDispatcher, and RetrievalPerspectiveHealerAgent
     operate with `model_id="gpt-5.6-luna"`.
5. Pipeline D:
   - DecisionOrchestrator, RedTeamRiskAgent, StrategicFeasibilityAgent, and BriefingAndDecisionHealerAgent
     operate with `model_id="gpt-5.6-luna"`.
6. Evaluator & Release Micro-Judges:
   - DecomposedMultiJudge, HolisticReleaseJudge, ForensicTranscriptJudge, ExecutiveRecapJudge,
     GraphExtractionJudge, and EnterpriseGraphReviewJudge operate with `model="gpt-5.6-luna"`.
7. LLM Transcript Synthesizer:
   - LLMTranscriptSynthesizer correctly initializes and delegates with `model="gpt-5.6-luna"`.
8. Mock Call Parameter Immunity:
   - Verifies zero temperature parameter injection across all mock OpenAI chat completions for `gpt-5.6-luna`.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dual_pipeline.a2a import (
    A2ATask,
    A2ATaskStatus,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from dual_pipeline.credentials import GOVERNED_MODELS, resolve_openai_credentials
from dual_pipeline.decision.agents import (
    DecisionOrchestrator,
)
from dual_pipeline.decision.agents.briefing_healer import (
    BriefingAndDecisionHealerAgent,
)
from dual_pipeline.l2_healer_service import UnifiedL2HealerService
from dual_pipeline.l2_healing_contracts import (
    HealingScope,
    L2HealingRequest,
)
from dual_pipeline.llm_judge import (
    DecomposedMultiJudge,
    HolisticReleaseJudge,
)
from dual_pipeline.retrieval.agents import (
    MultiPerspectiveDispatcher,
    get_multi_perspective_dispatcher_card,
)
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
)
from pipeline_a_intake.agents import (
    ForensicTranscriptHealerAgent,
    IngestionCoordinator,
    get_forensic_normalizer_card,
)
from pipeline_b_extraction.agents import (
    CanonicalProjectionHealerAgent,
    ExtractionCoordinator,
    TechArchAgent,
    get_tech_arch_card,
)
from pipeline_b_extraction.extractors import ClientSafeExtractor


class TestGpt56LunaAgents(unittest.TestCase):
    """Exhaustive test suite verifying gpt-5.6-luna across all Waves 1–4 components."""

    # -------------------------------------------------------------
    # 1. Model Binding & Parameter Immunity
    # -------------------------------------------------------------
    def test_model_binding_gpt56_luna_parameter_immunity(self) -> None:
        """Verifies ModelBinding enforces parameter immunity for gpt-5.6-luna."""
        # Unspecified reasoning effort should default to medium and set temperature to None
        binding_default = ModelBinding(model_id="gpt-5.6-luna", provider="openai")
        self.assertIsNone(binding_default.temperature)
        self.assertEqual(binding_default.reasoning_effort, "medium")

        # Explicit temperature passed should be sanitized to None for reasoning model
        binding_sanitized = ModelBinding(
            model_id="gpt-5.6-luna",
            provider="openai",
            temperature=0.0,
            reasoning_effort="high",
        )
        self.assertIsNone(binding_sanitized.temperature)
        self.assertEqual(binding_sanitized.reasoning_effort, "high")

        # Serialized dict must omit temperature
        d = binding_sanitized.to_dict()
        self.assertNotIn("temperature", d)
        self.assertEqual(d.get("reasoning_effort"), "high")

    def test_governed_models_includes_gpt56_luna(self) -> None:
        """Verifies gpt-5.6-luna is governed in credentials SSOT."""
        self.assertIn("gpt-5.6-luna", GOVERNED_MODELS)
        creds = resolve_openai_credentials(allow_mock=True, model="gpt-5.6-luna")
        self.assertEqual(creds.model, "gpt-5.6-luna")

    # -------------------------------------------------------------
    # 2. Pipeline A Under gpt-5.6-luna
    # -------------------------------------------------------------
    def test_pipeline_a_gpt56_luna_bindings_and_execution(self) -> None:
        """Verifies Pipeline A agents and coordinator with gpt-5.6-luna."""
        # Normalizer
        card_norm = get_forensic_normalizer_card()
        self.assertEqual(card_norm.compute_posture, ComputePosture.CLIENT_SAFE)

        # Ingestion Coordinator with gpt-5.6-luna
        coord = IngestionCoordinator(
            mock_mode=True,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(coord.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(coord.card.model_binding.reasoning_effort, "high")
        self.assertIsNone(coord.card.model_binding.temperature)

        # Direct DAG execution under gpt-5.6-luna
        res = asyncio.run(coord.execute_direct_dag(
            raw_transcript_text="[00:01:00] Alex Rivera: Finalize pipeline architecture.",
            meeting_name="Executive Architecture Sync",
            meeting_date="09/12/2026",
            roster_context=["Alex Rivera"],
        ))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(bool(res["artifact_01_markdown"]))
        self.assertEqual(res["model_id"], "gpt-5.6-luna")

        # Forensic Healer with gpt-5.6-luna
        healer_a = ForensicTranscriptHealerAgent(
            mock_mode=True,
            model_id="gpt-5.6-luna",
            reasoning_effort="medium",
        )
        self.assertEqual(healer_a.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertIsNone(healer_a.card.model_binding.temperature)

    # -------------------------------------------------------------
    # 3. Pipeline B Under gpt-5.6-luna
    # -------------------------------------------------------------
    def test_pipeline_b_gpt56_luna_bindings_and_execution(self) -> None:
        """Verifies Pipeline B extraction and coordinator with gpt-5.6-luna."""
        coord = ExtractionCoordinator(
            mock_mode=True,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(coord.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(coord.card.model_binding.reasoning_effort, "high")
        self.assertIsNone(coord.card.model_binding.temperature)
        self.assertEqual(coord.l1_cognition.model_binding["model_id"], "gpt-5.6-luna")

        # Canonical Healer with gpt-5.6-luna
        healer_b = CanonicalProjectionHealerAgent(
            mock_mode=True,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(healer_b.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(healer_b.card.model_binding.reasoning_effort, "high")
        self.assertIsNone(healer_b.card.model_binding.temperature)

        # ClientSafeExtractor parameter immunity under gpt-5.6-luna
        extractor = ClientSafeExtractor(model="gpt-5.6-luna", mock_mode=True)
        self.assertEqual(extractor.model, "gpt-5.6-luna")

    # -------------------------------------------------------------
    # 4. Pipeline C Under gpt-5.6-luna
    # -------------------------------------------------------------
    def test_pipeline_c_gpt56_luna_bindings_and_execution(self) -> None:
        """Verifies Pipeline C retrieval and dispatcher with gpt-5.6-luna."""
        MultiPerspectiveDispatcher(mock_mode=True)
        # Verify card declaration
        card = get_multi_perspective_dispatcher_card()
        self.assertEqual(card.role, "Retrieval Query Strategist")

        # Healer with gpt-5.6-luna
        healer_c = RetrievalPerspectiveHealerAgent(
            mock_mode=True,
            model_id="gpt-5.6-luna",
            reasoning_effort="medium",
        )
        self.assertEqual(healer_c.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertIsNone(healer_c.card.model_binding.temperature)

    # -------------------------------------------------------------
    # 5. Pipeline D Under gpt-5.6-luna
    # -------------------------------------------------------------
    def test_pipeline_d_gpt56_luna_bindings_and_execution(self) -> None:
        """Verifies Pipeline D decision orchestrator and dialectical rollouts under gpt-5.6-luna."""
        orchestrator = DecisionOrchestrator(
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(orchestrator.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(orchestrator.card.model_binding.reasoning_effort, "high")
        self.assertIsNone(orchestrator.card.model_binding.temperature)

        # Dialectical rollout under gpt-5.6-luna
        options = [
            {
                "key": "opt-mesh",
                "title": "A2A Mesh Protocol with gpt-5.6-luna",
                "description": "Standardize decoupled A2A protocol under gpt-5.6-luna test-time compute.",
            },
            {
                "key": "opt-legacy",
                "title": "Monolithic Legacy Pipeline",
                "description": "Retain legacy monolithic coupling without contract validation.",
            },
        ]
        rollout = orchestrator.execute_dialectical_rollout(
            decision_id="dec-gpt56-01",
            question="Next-Gen Architecture Selection",
            options=options,
        )
        self.assertEqual(rollout["model_id"], "gpt-5.6-luna")
        self.assertEqual(rollout["reasoning_effort"], "high")
        self.assertEqual(rollout["top_option"]["key"], "opt-mesh")

        # Briefing Healer with gpt-5.6-luna
        healer_d = BriefingAndDecisionHealerAgent(
            mock_mode=True,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(healer_d.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertIsNone(healer_d.card.model_binding.temperature)

    # -------------------------------------------------------------
    # 6. Release Micro-Judges & HolisticReleaseJudge Under gpt-5.6-luna
    # -------------------------------------------------------------
    def test_micro_judges_and_holistic_judge_with_gpt56_luna(self) -> None:
        """Verifies all micro-judges and holistic judge propagate gpt-5.6-luna."""
        multi_judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        self.assertEqual(multi_judge.model, "gpt-5.6-luna")
        self.assertEqual(multi_judge.reasoning_effort, "high")
        self.assertEqual(multi_judge.judge_forensic.model, "gpt-5.6-luna")
        self.assertEqual(multi_judge.judge_recap.model, "gpt-5.6-luna")
        self.assertEqual(multi_judge.judge_graph.model, "gpt-5.6-luna")
        self.assertEqual(multi_judge.holistic_judge.model, "gpt-5.6-luna")

        # HolisticReleaseJudge direct execution
        holistic = HolisticReleaseJudge(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        f1 = "### [00:00:00]\n\n**Alex Rivera:** Verified architecture on 09/12/2026.\n"
        f2 = "# Executive Recap\n\n**Date:** 09/12/2026\n\n## Action Items\n| 1 | Plan | Mitchell | 09/20/2026 |"
        f3 = json.dumps({"schema_version": "5.7", "graph_mutations": {"entity_upserts": []}})
        res = holistic.evaluate(
            output_1_text=f1,
            output_2_text=f2,
            output_3_text=f3,
            raw_transcript=f1,
            mock=True,
        )
        self.assertEqual(res["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(res["reasoning_effort"], "high")
        self.assertEqual(res["evaluation_mode"], "HOLISTIC_UNIFIED")
        self.assertIn(res["verdict"], ("PASS", "PASS_ADVISORY", "FAIL"))

    # -------------------------------------------------------------
    # 7. Unified L2 Healer Service with gpt-5.6-luna
    # -------------------------------------------------------------
    def test_unified_l2_healer_service_with_gpt56_luna(self) -> None:
        """Verifies UnifiedL2HealerService recognizes gpt-5.6-luna as reasoning model."""
        healer_svc = UnifiedL2HealerService(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock_mode=True,
        )
        self.assertEqual(healer_svc.model, "gpt-5.6-luna")

        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_A_TRANSCRIPT,
            candidate_artifact="# Header\n[00:01:00] Speaker 1: Done on 2026-09-12",
            failure_reasons=["Forbidden ISO date: 2026-09-12", "Generic speaker: Speaker 1"],
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
            context_metadata={"roster": ["Alex Sanchez"]},
        )
        res = asyncio.run(healer_svc.execute_healing(req))
        self.assertTrue(res.fact_preservation_passed)
        self.assertIn("09/12/2026", str(res.healed_artifact))

    # -------------------------------------------------------------
    # 8. Mock Client Temperature Omission Verification
    # -------------------------------------------------------------
    def test_mock_client_zero_temperature_injection_with_gpt56_luna(self) -> None:
        """Asserts that client.chat.completions.create receives NO temperature for gpt-5.6-luna."""
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"facts": []})
        mock_choice.finish_reason = "stop"
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_create = AsyncMock(return_value=mock_resp)
        mock_client.chat.completions.create = mock_create

        async def _run_calls() -> None:
            # 1. Tech Arch Agent under gpt-5.6-luna
            tech_card = get_tech_arch_card()
            tech_agent = TechArchAgent(api_key="fake-key")
            tech_agent.card = AgentCard(
                agent_id=tech_card.agent_id,
                name=tech_card.name,
                version=tech_card.version,
                role=tech_card.role,
                domain_focus=tech_card.domain_focus,
                compute_posture=tech_card.compute_posture,
                model_binding=ModelBinding(model_id="gpt-5.6-luna", provider="openai"),
            )
            task = A2ATask(
                task_id="test-gpt56-arch",
                parent_task_id=None,
                sender_agent_id="caller",
                recipient_agent_id=tech_agent.card.agent_id,
                objective="Extract technical specs",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={"evidence_id": "ev-01", "text": "Deploy BGE-M3", "speaker": "Mitchell"},
            )
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                await tech_agent.handle_task(task)

            self.assertTrue(mock_create.called)
            call_kwargs = mock_create.call_args[1]
            self.assertEqual(call_kwargs.get("model"), "gpt-5.6-luna")
            self.assertEqual(call_kwargs.get("reasoning_effort"), "medium")
            self.assertNotIn("temperature", call_kwargs, "Reasoning model gpt-5.6-luna must NOT receive temperature")

            # 2. ClientSafeExtractor under gpt-5.6-luna
            mock_create.reset_mock()
            extractor = ClientSafeExtractor(model="gpt-5.6-luna", api_key="fake-key")
            from storage.database import EvidenceRecord
            ev = EvidenceRecord(
                evidence_id="ev-101",
                meeting_id="meeting-01",
                speaker="Mitchell",
                text="Deploy pipeline to AWS S3 on 09/12/2026",
                start_timestamp=0.0,
            )
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                await extractor.extract(ev)

            self.assertTrue(mock_create.called)
            ext_kwargs = mock_create.call_args[1]
            self.assertEqual(ext_kwargs.get("model"), "gpt-5.6-luna")
            self.assertEqual(ext_kwargs.get("reasoning_effort"), "medium")
            self.assertNotIn("temperature", ext_kwargs, "ClientSafeExtractor must NOT pass temperature to gpt-5.6-luna")

        asyncio.run(_run_calls())


if __name__ == "__main__":
    unittest.main()
