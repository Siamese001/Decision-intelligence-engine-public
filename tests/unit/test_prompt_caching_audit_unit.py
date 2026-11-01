"""End-to-end integration and regression test suite for prompt caching audit hardening.
Verifies that all audited call sites properly pass cache_namespace, separate system prompts
from dynamic user content, and generate deterministic prompt cache keys through guarded clients.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config.credentials import _guard_client_completions
from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
from dual_pipeline.llm_judge_core.judges.recap_transport import generate_client_recap
from dual_pipeline.llm_judge_core.judges.graph_transport import generate_client_graph
from dual_pipeline.llm_judge_core.judges.enterprise import EnterpriseGraphReviewJudge
from dual_pipeline.llm_judge_core.enterprise import EnterpriseGraphReviewJudge as EnterpriseGraphReviewJudgeFacade
from dual_pipeline.llm_judge_core.batch_judge import BatchMicroJudgeEvaluator
from dual_pipeline.l2_healer_service import UnifiedL2HealerService, L2HealingRequest, HealingScope
from dual_pipeline.speaker_disambiguation_core.shield import _process_single_llm_batch
from dual_pipeline.speaker_disambiguation_core.disambiguator import SpeakerDisambiguator
from dual_pipeline.speaker_disambiguation_core.models import RoomMicConfig, AttendeeContext
from dual_pipeline.retrieval.agents.multi_perspective_dispatcher import MultiPerspectiveDispatcher
from dual_pipeline.transcript_stages_core.synthesizers import _resolve_cache_namespace
from pipeline_a_intake.agents.multimodal_slide_agent import MultimodalSlideExtractor
from pipeline_a_intake.agents.roster_reconciler_agent import RosterReconcilerAgent
from pipeline_b_extraction.agents.strategic_commitments_agent import StrategicCommitmentsAgent
from pipeline_b_extraction.agents.tech_arch_agent import TechArchAgent
from pipeline_b_extraction.extractors import ClientSafeExtractor
from storage.models import EvidenceRecord
from dual_pipeline.a2a import A2ATask, A2ATaskStatus


class MockCompletionResponse:
    def __init__(self, content: str, model: str = "gpt-5.6-luna", cached_tokens: int = 256):
        self.id = "mock-resp-id-audit"
        self.model = model
        self.choices = [
            MagicMock(
                message=MagicMock(content=content),
                finish_reason="stop",
            )
        ]
        self.usage = MagicMock(
            prompt_tokens=500,
            completion_tokens=100,
            prompt_tokens_details=MagicMock(cached_tokens=cached_tokens),
            completion_tokens_details=MagicMock(reasoning_tokens=50),
        )


class TestPromptCachingAuditE2E(unittest.IsolatedAsyncioTestCase):

    def test_enterprise_facade_import(self):
        """Verify backward-compatible enterprise judge import facade."""
        self.assertIs(EnterpriseGraphReviewJudge, EnterpriseGraphReviewJudgeFacade)

    def test_synthesizer_cache_namespaces(self):
        """Verify dedicated cache namespaces in synthesizers."""
        self.assertEqual(_resolve_cache_namespace("Stage01 Forensic Normalization"), "stage01_forensic")
        self.assertEqual(_resolve_cache_namespace("Stage02 Executive Recap"), "stage02_recap")
        self.assertEqual(_resolve_cache_namespace("Stage03 Graph Extraction"), "stage03_projection")
        self.assertEqual(_resolve_cache_namespace("L1 Signal Triage"), "signal_triage")
        self.assertEqual(_resolve_cache_namespace("L2-AgenticHunter Leverage Extraction"), "agentic_leverage")
        self.assertEqual(_resolve_cache_namespace("Preflight Metadata Extraction"), "preflight_metadata")
        self.assertEqual(_resolve_cache_namespace("Unknown Stage"), "synthesizer")

    def test_forensic_judge_caching(self):
        """Verify ForensicTranscriptJudge passes cache_namespace and derives prompt_cache_key."""
        raw_client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse(json.dumps({
            "status": "PASS",
            "composite_score": 95,
            "forensic_verdict": "PASS",
            "hallucination_rate": 0.0,
            "speaker_accuracy": 1.0,
            "fidelity_score": 95,
        })))
        raw_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_client, "gpt-5.6-luna")

        judge = ForensicTranscriptJudge(model="gpt-5.6-luna", use_local_model=False)
        judge.evaluate(
            f1_text="09/12/2026 Alex Sanchez: Discussion on architecture.",
            raw_source_text="09/12/2026 Alex Sanchez: Discussion on architecture.",
            client=guarded_client,
            mock=False,
        )

        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:judge_forensic:gpt-5.6-luna:"))
        self.assertNotIn("cache_namespace", call_kwargs)

    def test_recap_transport_caching(self):
        """Verify generate_client_recap passes cache_namespace and derives prompt_cache_key."""
        raw_client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse(json.dumps({
            "status": "PASS",
            "composite_score": 92,
            "action_items_score": 90,
            "executive_takeaways_score": 95,
        })))
        raw_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_client, "gpt-5.6-luna")

        recorder = MagicMock()
        res = generate_client_recap(
            client=guarded_client,
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            system_prompt="Executive Recap Evaluation Rubric",
            user_content="Evaluate Candidate Recap",
            recorder=recorder,
        )
        self.assertIn("composite_score", res)
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:judge_recap:gpt-5.6-luna:"))

    def test_graph_transport_caching(self):
        """Verify generate_client_graph passes cache_namespace and derives prompt_cache_key."""
        raw_client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse(json.dumps({
            "status": "PASS",
            "composite_score": 90,
            "entity_resolution_score": 92,
        })))
        raw_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_client, "gpt-5.6-luna")

        recorder = MagicMock()
        res = generate_client_graph(
            client=guarded_client,
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            system_prompt="Graph Extraction Evaluation Rubric",
            user_content="Evaluate Candidate Graph",
            recorder=recorder,
            max_completion_tokens=4000,
            assign_verdict=lambda d: d,
            transient=lambda e: False,
        )
        self.assertIn("composite_score", res)
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:judge_graph:gpt-5.6-luna:"))

    def test_enterprise_judge_caching_and_separation(self):
        """Verify EnterpriseGraphReviewJudge separates system rubric from graph content and caches."""
        raw_client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse(json.dumps({
            "schema_version": "enterprise-graph-review-1.0",
            "composite_score": 88,
            "maturity_band": "Level 4 (Strategic Co-Pilot)",
            "dimension_scores": {},
            "executive_summary": "Strong strategic graph.",
            "strategic_recommendations": [],
        })))
        raw_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_client, "gpt-5.6-luna")

        judge = EnterpriseGraphReviewJudge(model="gpt-5.6-luna", mock=False)
        judge.evaluate(
            canonical_graph_data={"nodes": [{"id": "sys:core"}], "edges": []},
            context_text="Sample meeting context",
            client=guarded_client,
            mock=False,
        )

        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:enterprise_graph_review_judge:gpt-5.6-luna:"))
        messages = call_kwargs.get("messages", [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Executive Exoskeleton Review Prompt", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("CANONICAL GRAPH PROJECTION:", messages[1]["content"])

    def test_batch_micro_judge_caching(self):
        """Verify BatchMicroJudgeEvaluator passes cache_namespace and derives prompt_cache_key."""
        raw_client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse(json.dumps({
            "evaluations": [
                {
                    "item_id": "item_1",
                    "political_materiality": 9,
                    "networking_target_identified": True,
                    "is_actionable_wedge": True,
                    "pass_gate": True,
                    "rejection_reason": None,
                }
            ]
        })))
        raw_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_client, "gpt-5.6-luna")

        evaluator = BatchMicroJudgeEvaluator(model="gpt-5.6-luna", mock=False)
        evaluator.evaluate_batch(
            nodes=[{"political_rationale": "High-value enterprise alignment", "networking_wedge": "Direct C-suite access", "key_players": ["VP Engineering"]}],
            reference_text="Transcript reference",
            client=guarded_client,
            mock=False,
        )

        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:batch_micro_judge:gpt-5.6-luna:"))

    async def test_l2_healer_caching(self):
        """Verify UnifiedL2HealerService passes cache_namespace and derives prompt_cache_key."""
        raw_async_client = MagicMock()
        mock_create = AsyncMock(return_value=MockCompletionResponse(
            json.dumps({"graph_mutations": {"decision_updates": [{"observed_at": "09/12/2026", "stale_after": "12/31/2026"}]}})
        ))
        raw_async_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_async_client, "gpt-5.6-luna")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-live-key"}):
            with patch("dual_pipeline.credentials.ensure_ssot_env_loaded"), \
                 patch("dual_pipeline.l2_healer_service.resolve_openai_credentials", return_value=MagicMock(is_mock=False, api_key="sk-test-live-key", model="gpt-5.6-luna", reasoning_effort="high")):
                healer = UnifiedL2HealerService(mock_mode=False)
                request = L2HealingRequest(
                    scope=HealingScope.PIPELINE_B_PROJECTION,
                    failure_reasons=["Missing temporal metadata"],
                    candidate_artifact={"graph_mutations": {"decision_updates": [{}]}},
                )

                with patch("dual_pipeline.l2_healer_service.CredentialManager.get_async_openai_client", return_value=(guarded_client, MagicMock(is_mock=False))), \
                     patch("dual_pipeline.l2_healer_service.verify_fact_preservation", return_value=True):
                    res = await healer.execute_healing(request)

        self.assertEqual(res.status, "HEALED")
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:l2_healer_pipeline_b_projection:"))

    def test_speaker_disambiguation_shield_caching(self):
        """Verify speaker disambiguation passes cache_namespace and derives prompt_cache_key."""
        raw_client = MagicMock()
        mock_create = MagicMock(return_value=MockCompletionResponse(json.dumps({
            "attributions": [
                {
                    "turn_index": 1,
                    "assigned_speaker": "Alex Sanchez",
                    "confidence": 0.95,
                    "attribution_mode": "LLM_SEMANTIC",
                    "rationale": "Clear conversational flow match.",
                }
            ]
        })))
        raw_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_client, "gpt-5.6-luna")

        disambiguator = SpeakerDisambiguator(client=guarded_client, model="gpt-5.6-luna")
        room_cfg = RoomMicConfig(
            mic_label="room_mic_1",
            display_label="Conference Room A",
            in_room_attendees=[AttendeeContext(name="Alex Sanchez", person_id="person:alex_sanchez", role="Architect", company="Acme")],
        )
        batch = [{"line_index": 1, "speaker": "Speaker 1", "timestamp": "00:01:00", "text": "Let's review the architecture."}]
        all_turns = batch

        _process_single_llm_batch(
            disambiguator=disambiguator,
            b_idx=0,
            batch=batch,
            all_turns=all_turns,
            system_prompt="Disambiguate room mic audio turns",
            total_batches=1,
            room_config=room_cfg,
        )

        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:speaker_disambiguation:gpt-5.6-luna:"))

    async def test_multimodal_slide_agent_caching_and_separation(self):
        """Verify MultimodalSlideExtractor separates system prompt and passes cache_namespace."""
        raw_async_client = MagicMock()
        mock_create = AsyncMock(return_value=MockCompletionResponse(json.dumps({
            "slides": [{"slide_index": 1, "title": "System Arch"}],
            "slide_count": 1,
            "has_diagrams": False,
        })))
        raw_async_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_async_client, "gpt-5.6-luna")

        agent = MultimodalSlideExtractor(api_key="sk-test-live-key", mock_mode=False)
        task = A2ATask(
            task_id="task-slide-1",
            parent_task_id=None,
            sender_agent_id="coordinator",
            recipient_agent_id="multimodal_slide_agent",
            objective="Extract slide contents",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"deck_path": "tests/fixtures/sample_deck.pdf"},
        )

        with patch("pipeline_a_intake.agents.multimodal_slide_agent.CredentialManager.get_async_openai_client", return_value=(guarded_client, MagicMock(is_mock=False))), \
             patch("pathlib.Path.is_file", return_value=True):
            res = await agent.handle_task(task)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith(f"nee:multimodal_slide_agent:{agent.card.model_binding.model_id}:"))
        messages = call_kwargs.get("messages", [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Presentation Deck and Architecture Diagram Analyzer", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")

    async def test_roster_reconciler_agent_caching_and_separation(self):
        """Verify RosterReconcilerAgent separates system prompt and passes cache_namespace."""
        raw_async_client = MagicMock()
        mock_create = AsyncMock(return_value=MockCompletionResponse(json.dumps({
            "Speaker 1": {"canonical_name": "Alex Sanchez", "entity_id": "person:alex_sanchez", "confidence": 0.95}
        })))
        raw_async_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_async_client, "gpt-5.6-luna")

        agent = RosterReconcilerAgent(api_key="sk-test-live-key", mock_mode=False)
        task = A2ATask(
            task_id="task-roster-1",
            parent_task_id=None,
            sender_agent_id="coordinator",
            recipient_agent_id="roster_reconciler",
            objective="Reconcile roster",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "speaker_turns": [{"speaker": "Speaker 1", "text": "Hello"}],
                "roster_context": [{"name": "Alex Sanchez", "canonical_name": "Alex Sanchez", "entity_id": "person:alex_sanchez"}],
            },
        )

        with patch("pipeline_a_intake.agents.roster_reconciler_agent.CredentialManager.get_async_openai_client", return_value=(guarded_client, MagicMock(is_mock=False))):
            res = await agent.handle_task(task)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith(f"nee:roster_reconciler:{agent.card.model_binding.model_id}:"))
        messages = call_kwargs.get("messages", [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Speaker Disambiguation and Roster Reconciliation Agent", messages[0]["content"])

    async def test_strategic_commitments_agent_caching_and_separation(self):
        """Verify StrategicCommitmentsAgent separates system prompt and passes cache_namespace."""
        raw_async_client = MagicMock()
        mock_create = AsyncMock(return_value=MockCompletionResponse(json.dumps({
            "facts": [{"evidence_id": "ev-1", "type": "DELIVERY_MILESTONE", "name": "Milestone A", "properties": {}, "strategic_value": 8.0, "tacticality": 3.0}]
        })))
        raw_async_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_async_client, "gpt-5.6-luna")

        agent = StrategicCommitmentsAgent(api_key="sk-test-live-key", mock_mode=False)
        with patch("pipeline_b_extraction.agents.strategic_commitments_agent.CredentialManager.get_async_openai_client", return_value=(guarded_client, MagicMock(is_mock=False))):
            facts = await agent.extract_batch([{"evidence_id": "ev-1", "speaker": "Alex", "text": "Commitment to deliver by Q3."}])

        self.assertEqual(len(facts), 1)
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith(f"nee:strategic_commitments_agent:{agent.card.model_binding.model_id}:"))
        messages = call_kwargs.get("messages", [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Principal Program Delivery Executive", messages[0]["content"])

    async def test_tech_arch_agent_caching_and_separation(self):
        """Verify TechArchAgent separates system prompt and passes cache_namespace."""
        raw_async_client = MagicMock()
        mock_create = AsyncMock(return_value=MockCompletionResponse(json.dumps({
            "facts": [{"evidence_id": "ev-1", "type": "ARCHITECTURAL_DECISION", "name": "Kafka Event Bus", "properties": {}, "strategic_value": 9.0, "tacticality": 2.0}]
        })))
        raw_async_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_async_client, "gpt-5.6-luna")

        agent = TechArchAgent(api_key="sk-test-live-key", mock_mode=False)
        with patch("pipeline_b_extraction.agents.tech_arch_agent.CredentialManager.get_async_openai_client", return_value=(guarded_client, MagicMock(is_mock=False))):
            facts = await agent.extract_batch([{"evidence_id": "ev-1", "speaker": "Alex", "text": "We decided to adopt Kafka."}])

        self.assertEqual(len(facts), 1)
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith(f"nee:tech_arch_agent:{agent.card.model_binding.model_id}:"))
        messages = call_kwargs.get("messages", [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Principal Enterprise Systems Architect", messages[0]["content"])

    async def test_client_safe_extractor_caching_and_separation(self):
        """Verify ClientSafeExtractor separates system prompt and passes cache_namespace."""
        raw_async_client = MagicMock()
        mock_create = AsyncMock(return_value=MockCompletionResponse(json.dumps([
            {"type": "ARCHITECTURAL_DECISION", "name": "GraphQL Federation", "properties": {}, "strategic_value": 8.5, "tacticality": 2.5}
        ])))
        raw_async_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_async_client, "gpt-5.6-luna")

        with patch.dict(os.environ, {"ALLOW_CLOUD_TEXT_EGRESS": "1"}):
            extractor = ClientSafeExtractor(model="gpt-5.6-luna", api_key="sk-test-live-key", mock_mode=False)
            evidence = EvidenceRecord(
                evidence_id="ev-100",
                meeting_id="mtg-100",
                speaker="Lead Architect",
                text="We are standardizing on Apollo GraphQL federation for client queries.",
                start_timestamp=10.0,
            )

            with patch("pipeline_b_extraction.extractors.CredentialManager.get_async_openai_client", return_value=(guarded_client, MagicMock(is_mock=False))):
                nodes = await extractor.extract(evidence)

        self.assertEqual(len(nodes), 1)
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith("nee:client_safe_extractor:gpt-5.6-luna:"))
        messages = call_kwargs.get("messages", [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Principal Enterprise Systems Architect", messages[0]["content"])

    async def test_multi_perspective_dispatcher_caching_and_separation(self):
        """Verify MultiPerspectiveDispatcher separates system prompt and preserves cache_namespace."""
        raw_async_client = MagicMock()
        mock_create = AsyncMock(return_value=MockCompletionResponse(json.dumps({
            "sub_queries": [
                {"perspective": "TECHNICAL", "query_text": "kafka streaming contracts", "weight": 0.3},
                {"perspective": "COMMERCIAL", "query_text": "licensing and SOW commitments", "weight": 0.3},
            ]
        })))
        raw_async_client.chat.completions.create = mock_create
        guarded_client = _guard_client_completions(raw_async_client, "gpt-5.6-luna")

        dispatcher = MultiPerspectiveDispatcher(api_key="sk-test-live-key", mock_mode=False)
        task = A2ATask(
            task_id="task-disp-1",
            parent_task_id=None,
            sender_agent_id="test_runner",
            recipient_agent_id="MultiPerspectiveDispatcher",
            objective="Decompose query",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"query": "What are our Kafka commitments?"},
        )

        with patch("config.credentials.get_async_openai_client", return_value=(guarded_client, MagicMock(is_mock=False))):
            res = await dispatcher.handle_task(task)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(mock_create.called)
        call_kwargs = mock_create.call_args[1]
        self.assertIn("prompt_cache_key", call_kwargs)
        self.assertTrue(call_kwargs["prompt_cache_key"].startswith(f"nee:multi_perspective_dispatcher:{dispatcher.card.model_binding.model_id}:"))
        messages = call_kwargs.get("messages", [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Principal Enterprise Retrieval Architect", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
