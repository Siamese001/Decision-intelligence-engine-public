"""
tests/test_copilot_engagement_wave5.py

Unit tests for Wave 5: Qualify and release.
Covers:
- Command-line, continuous, and streaming routes preserve scope.
- Rapid session switching and cache invalidation across engagements.
- Concurrency and thread safety without cross-contamination.
- Retaining useful supported advice and permitted comparisons.
- Zero production graph mutation invariant.
- Latency budget compliance.
"""
from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Dict, List

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    check_clause_grounding,
)
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    ScopeBinding,
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


class TestCopilotEngagementWave5Unit(unittest.TestCase):
    def setUp(self) -> None:
        self.graph: InMemoryGraphTopology = build_in_memory_graph()

        # Add evidenced link to Nexus and enterprise policy
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
        self.graph.nodes["policy:identity_governance"] = {
            "canonical_name": "Identity Governance",
            "node_type": "policy",
            "namespace": "ENTERPRISE_POLICY",
            "is_enterprise_policy": True,
            "aliases": ["authentication", "sso", "okta"],
        }
        self.graph.assertions["asrt_policy_sso"] = {
            "assertion_id": "asrt_policy_sso",
            "subject_node_id": "policy:identity_governance",
            "object_literal": "Okta SSO",
            "predicate": "REQUIRES",
            "claim_text": "Enterprise Identity Governance requires centralized Okta SSO authentication.",
            "namespace": "ENTERPRISE_POLICY",
            "is_enterprise_policy": True,
            "canonical_status": "ACTIVE",
        }

    def test_cross_route_scope_preservation_streaming(self) -> None:
        """Verifies streaming route preserves engagement boundary and matches displayed_bytes."""
        raw_json_chunks = [
            '{"has_guidance": true, ',
            '"category": "TECHNICAL", ',
            '"suggested_script": "Enterprise Identity Governance requires centralized Okta SSO authentication. LENS uses JIRA.", ',
            '"supporting_details_bullets": ["Enterprise Identity Governance requires centralized Okta SSO authentication."]}'
        ]
        mock_client = MockOpenAIClient(raw_json_chunks)
        handler = StreamingCopilotHandler(client=mock_client, model_id="gpt-5.6-astra")

        retriever = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:jarvis",
            session_id="session-streaming-w5",
            generation=1,
        )
        assertions = retriever.retrieve_structured_assertions("What authentication is required?")

        tokens_received: List[str] = []
        result = handler.execute_streaming_evaluation(
            call_kwargs={"messages": []},
            on_token=lambda tok: tokens_received.append(tok),
            verified_assertions=assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )

        self.assertIsNotNone(result)
        # Grounded enterprise policy sentence kept
        self.assertIn("centralized Okta SSO authentication", result["suggested_script"])
        # Unrelated LENS claim stripped
        self.assertNotIn("LENS", result["suggested_script"])
        self.assertNotIn("JIRA", result["suggested_script"])
        self.assertEqual(result["displayed_bytes"], result["suggested_script"])

    def test_session_switching_scope_invalidation(self) -> None:
        """Verifies session switching clears cached packets and prevents scope bleeding."""
        retriever_jarvis = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:jarvis",
            session_id="session-jarvis-101",
            generation=1,
        )
        bridge_jarvis = CopilotPrefetchBridge(retriever=retriever_jarvis)
        packet_jarvis = bridge_jarvis.prefetch(
            project_name="Jarvis", engagement_id="project:jarvis", session_id="session-jarvis-101"
        )
        self.assertEqual(packet_jarvis.engagement_id, "project:jarvis")

        # Switch to LENS session
        retriever_lens = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:lens",
            session_id="session-lens-202",
            generation=2,
        )
        bridge_lens = CopilotPrefetchBridge(retriever=retriever_lens)
        packet_lens = bridge_lens.prefetch(
            project_name="Lens", engagement_id="project:lens", session_id="session-lens-202"
        )
        self.assertEqual(packet_lens.engagement_id, "project:lens")

        # Delayed cross-session invalidation check
        self.assertFalse(retriever_jarvis._scope_binding.is_valid("session-lens-202", "project:jarvis", 1))
        retriever_jarvis.session_id = "session-lens-202"
        res_delayed = retriever_jarvis.retrieve_structured_assertions("Metal transcription")
        self.assertEqual(len(res_delayed), 0, "Delayed cross-session query must fail closed")

    def test_concurrency_multi_engagement_isolation(self) -> None:
        """Verifies parallel concurrent queries targeting different engagements remain 100% isolated."""
        def run_query(engagement_id: str, query: str) -> List[str]:
            r = CopilotGraphRetriever(
                topology=self.graph,
                engagement_id=engagement_id,
                session_id=f"session-{engagement_id}",
                generation=1,
            )
            asrt = r.retrieve_structured_assertions(query)
            return [a["assertion_id"] for a in asrt]

        with ThreadPoolExecutor(max_workers=4) as executor:
            fut_jarvis = executor.submit(run_query, "project:jarvis", "What dependencies exist?")
            fut_lens = executor.submit(run_query, "project:lens", "What dependencies exist?")

            ids_jarvis = fut_jarvis.result()
            ids_lens = fut_lens.result()

        # Jarvis assertions must not contain LENS assertions
        self.assertNotIn("assertion-b7cd2f78b3a1c4995bd4cfb1ee31", ids_jarvis)
        # Lens assertions must not contain Jarvis assertions
        self.assertNotIn("asrt_jarvis_nexus_link", ids_lens)

    def test_permitted_comparisons_and_enterprise_policy(self) -> None:
        """Verifies explicitly connected projects and enterprise policies are permitted and groundable."""
        retriever = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:jarvis",
            session_id="session-perm-w5",
            generation=1,
        )
        asrt = retriever.retrieve_structured_assertions("What does Jarvis integrate with?")
        asrt_ids = [a["assertion_id"] for a in asrt]
        self.assertIn("asrt_jarvis_nexus_link", asrt_ids)

        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Engineering Jarvis integrates with Project Nexus for OCR processing.",
            "supporting_details_bullets": ["Integration with Nexus OCR."],
        }
        res = validate_copilot_feedback(
            candidate,
            verified_assertions=asrt,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["label"], "Verified Answer")
        self.assertIn("asrt_jarvis_nexus_link", res["cited_assertion_ids"])

    def test_zero_production_graph_mutation(self) -> None:
        """Verifies that running copilot retrievers and validators causes zero graph mutations."""
        initial_node_count = len(self.graph.nodes)
        initial_assertion_count = len(self.graph.assertions)
        initial_edge_count = len(self.graph.forward_adjacency)

        # Execute multiple queries and validations
        retriever = CopilotGraphRetriever(
            topology=self.graph,
            engagement_id="project:jarvis",
            session_id="session-mutation-check",
            generation=1,
        )
        asrt = retriever.retrieve_structured_assertions("What risks exist?")
        candidate = {
            "has_guidance": True,
            "suggested_script": "Can we verify the technical ground truth for this topic?",
            "supporting_details_bullets": [],
        }
        validate_copilot_feedback(candidate, verified_assertions=asrt, rigor_tier="VERIFIED")

        self.assertEqual(len(self.graph.nodes), initial_node_count)
        self.assertEqual(len(self.graph.assertions), initial_assertion_count)
        self.assertEqual(len(self.graph.forward_adjacency), initial_edge_count)

    def test_latency_budget_compliance(self) -> None:
        """Verifies that clause verification and retrieval filtering add < 50ms overhead."""
        assertions = list(self.graph.assertions.values())
        by_id = {str(a["assertion_id"]): a for a in assertions if "assertion_id" in a}

        clause = "Engineering Jarvis integrates with Project Nexus for OCR processing."
        t0 = time.perf_counter()
        for _ in range(100):
            check_clause_grounding(clause, assertions, by_id)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0 / 100.0

        # Each clause check must average under 5.0ms (well under 50ms budget)
        self.assertLess(elapsed_ms, 5.0)


if __name__ == "__main__":
    unittest.main()
