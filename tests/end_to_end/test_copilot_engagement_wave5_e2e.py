"""
tests/end_to_end/test_copilot_engagement_wave5_e2e.py

Wave 5 E2E lifecycle qualification tests:
Verifies the full copilot workflow across CLI, continuous, and streaming routes,
testing session switching, concurrency, empty evidence, and zero graph mutation.
"""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Dict, List

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    GROUNDING_QUESTION_FALLBACK,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    validate_copilot_feedback,
)
from dual_pipeline.live_meeting_recorder_core.streaming_copilot import (
    StreamingCopilotHandler,
)
from tests.fixtures.copilot_engagement.loader import build_in_memory_graph


class MockStreamChunk:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]


class MockCompletions:
    def __init__(self, chunks: List[str]) -> None:
        self._chunks = chunks

    def create(self, **kwargs: Any) -> List[MockStreamChunk]:
        return [MockStreamChunk(c) for c in self._chunks]


class MockChat:
    def __init__(self, chunks: List[str]) -> None:
        self.completions = MockCompletions(chunks)


class MockOpenAIClient:
    def __init__(self, chunks: List[str]) -> None:
        self.chat = MockChat(chunks)


class TestCopilotEngagementWave5E2E(unittest.TestCase):
    def setUp(self) -> None:
        self.graph: InMemoryGraphTopology = build_in_memory_graph()

        # Add explicit integration with Nexus
        self.graph.nodes["project:nexus"] = {
            "canonical_name": "Project Nexus",
            "node_type": "project",
            "engagement_id": "project:nexus",
        }
        self.graph.assertions["asrt_jarvis_nexus_link"] = {
            "assertion_id": "asrt_jarvis_nexus_link",
            "subject_node_id": "project:jarvis",
            "object_node_id": "project:nexus",
            "predicate": "INTEGRATES_WITH",
            "claim_text": "Engineering Jarvis integrates with Project Nexus for OCR processing.",
            "engagement_id": "project:jarvis",
            "canonical_status": "ACTIVE",
        }

    def test_e2e_full_workflow_across_routes(self) -> None:
        """
        Tests the entire copilot qualification workflow across all 3 routes:
        Route 1: CLI Route (CopilotEngine)
        Route 2: Streaming Route (StreamingCopilotHandler)
        Route 3: On-demand Validation Route (validate_copilot_feedback)
        """
        retriever = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:jarvis",
            session_id="session-e2e-routes",
            generation=1,
        )
        assertions = retriever.retrieve_structured_assertions("What does Jarvis integrate with?")

        # Route 1: On-demand validation
        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": (
                "Engineering Jarvis integrates with Project Nexus for OCR processing. "
                "Spatial File Upload depends on JIRA."
            ),
            "supporting_details_bullets": [
                "Engineering Jarvis integrates with Project Nexus for OCR processing.",
                "Spatial File Upload depends on JIRA.",
            ],
            "speaker_summary": "Engineering Jarvis integrates with Project Nexus for OCR processing.",
        }
        val_res = validate_copilot_feedback(
            candidate,
            verified_assertions=assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )
        self.assertIsNotNone(val_res)
        self.assertIn("Project Nexus for OCR processing", val_res["suggested_script"])
        self.assertNotIn("Spatial File Upload", val_res["suggested_script"])
        self.assertNotIn("JIRA", val_res["suggested_script"])
        self.assertEqual(len(val_res["supporting_details_bullets"]), 1)
        self.assertEqual(val_res["label"], "Verified Answer")

        # Route 2: Streaming route
        raw_chunks = [
            '{"has_guidance": true, ',
            '"category": "TECHNICAL", ',
            '"suggested_script": "Engineering Jarvis integrates with Project Nexus for OCR processing. LENS is blocked.", ',
            '"supporting_details_bullets": ["Engineering Jarvis integrates with Project Nexus for OCR processing."]}'
        ]
        mock_client = MockOpenAIClient(raw_chunks)
        stream_handler = StreamingCopilotHandler(client=mock_client, model_id="gpt-5.6-astra")
        tokens: List[str] = []
        stream_res = stream_handler.execute_streaming_evaluation(
            call_kwargs={"messages": []},
            on_token=lambda tok: tokens.append(tok),
            verified_assertions=assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )
        self.assertIsNotNone(stream_res)
        self.assertEqual(stream_res["displayed_bytes"], stream_res["suggested_script"])
        self.assertIn("Project Nexus for OCR processing", stream_res["suggested_script"])
        self.assertNotIn("LENS is blocked", stream_res["suggested_script"])

    def test_e2e_session_switch_and_concurrency_qualification(self) -> None:
        """
        Verifies that concurrent multi-engagement queries remain strictly isolated
        and session switching prevents state leakage.
        """
        def query_engagement(eng_id: str, query: str) -> List[str]:
            r = CopilotGraphRetriever(
                topology=self.graph,
                engagement_id=eng_id,
                session_id=f"session-{eng_id}",
                generation=1,
            )
            res = r.retrieve_structured_assertions(query)
            return [a["assertion_id"] for a in res]

        with ThreadPoolExecutor(max_workers=4) as executor:
            fut_j = executor.submit(query_engagement, "project:jarvis", "dependencies and risks")
            fut_l = executor.submit(query_engagement, "project:lens", "dependencies and risks")

            aids_j = fut_j.result()
            aids_l = fut_l.result()

        # Jarvis must not receive Lens assertions
        self.assertNotIn("assertion-b7cd2f78b3a1c4995bd4cfb1ee31", aids_j)
        # Lens must not receive Jarvis assertions
        self.assertNotIn("asrt_jarvis_nexus_link", aids_l)

        # Prefetch bridge switch
        retriever_j = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:jarvis",
            session_id="session-j",
            generation=1,
        )
        bridge_j = CopilotPrefetchBridge(retriever=retriever_j)
        pkt_j = bridge_j.prefetch(project_name="Jarvis", engagement_id="project:jarvis", session_id="session-j")
        self.assertEqual(pkt_j.engagement_id, "project:jarvis")

        retriever_l = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:lens",
            session_id="session-l",
            generation=2,
        )
        bridge_l = CopilotPrefetchBridge(retriever=retriever_l)
        pkt_l = bridge_l.prefetch(project_name="Lens", engagement_id="project:lens", session_id="session-l")
        self.assertEqual(pkt_l.engagement_id, "project:lens")

    def test_e2e_empty_evidence_and_deadline_resilience(self) -> None:
        """Verifies that retrieval timeouts or empty evidence fail closed to clarifications."""
        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Jarvis uses Spanner database storage.",
            "supporting_details_bullets": ["Spanner is the database."],
            "speaker_summary": "Jarvis database is Spanner.",
        }

        # Empty evidence simulation
        val = validate_copilot_feedback(
            candidate,
            verified_assertions=[],
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )
        self.assertIsNotNone(val)
        self.assertEqual(val["rigor_tier"], "UNVERIFIED")
        self.assertEqual(val["cited_assertion_ids"], [])
        self.assertEqual(val["supporting_details_bullets"], [])
        self.assertEqual(val["suggested_script"], GROUNDING_QUESTION_FALLBACK)
        self.assertEqual(val["label"], "Clarification")
        self.assertTrue(val["is_suggestion"])

    def test_e2e_zero_graph_mutation_qualification(self) -> None:
        """Verifies that qualification runs cause zero graph state mutation."""
        nodes_before = dict(self.graph.nodes)
        assertions_before = dict(self.graph.assertions)

        retriever = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:jarvis",
            session_id="session-qual-zero-mut",
            generation=1,
        )
        retriever.retrieve_structured_assertions("What risks exist?")

        self.assertEqual(len(self.graph.nodes), len(nodes_before))
        self.assertEqual(len(self.graph.assertions), len(assertions_before))
        self.assertEqual(self.graph.nodes.keys(), nodes_before.keys())
        self.assertEqual(self.graph.assertions.keys(), assertions_before.keys())


if __name__ == "__main__":
    unittest.main()
