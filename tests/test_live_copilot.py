"""
tests/test_live_copilot.py

Unit tests for LiveCopilotAdvisor graph context integration and async evaluation.
"""
from __future__ import annotations

import unittest
from unittest import mock

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor


class TestLiveCopilotAdvisor(unittest.TestCase):
    def setUp(self):
        self.advisor = LiveCopilotAdvisor(
            project_name="project_lens",
            meeting_name="discuss LENS SOW",
            attendees=[{"name": "Amit", "role": "Architect", "org": "PartnerAdvisory"}],
            mock_mode=True,
            cooldown_seconds=0.1,
        )

    def tearDown(self):
        self.advisor.shutdown(wait=True)

    def test_mock_evaluation_without_graph(self):
        self.advisor.record_turn("Amit", "We are reviewing the scope and delivery milestones for the project.", "00:10")
        res = self.advisor._evaluate_dialogue_sync()
        self.assertIsNotNone(res)
        self.assertTrue(res["has_guidance"])
        self.assertEqual(res["category"], "STRATEGIC")

    def test_lookup_graph_context_with_mock_graph(self):
        # Create a mock InMemoryGraphTopology
        mock_graph = mock.MagicMock()
        mock_graph.nodes = {
            "node_lens_sow": {
                "canonical_name": "LENS SOW Scope",
                "display_name": "LENS SOW",
                "node_type": "project",
            }
        }
        mock_graph.forward_adjacency = {
            "node_lens_sow": [
                {
                    "relationship_family": "governed_by",
                    "to_node_name": "Apex Global Engineering",
                    "edge_type": "GOVERNANCE",
                }
            ]
        }
        mock_graph.assertions = {
            "assertion_lens_gov": {
                "subject_node_id": "node_lens_sow",
                "predicate": "governed_by",
                "object_literal": "Apex Global Engineering",
                "status": "ACTIVE",
            }
        }
        self.advisor.in_memory_graph = mock_graph

        dialogue = "00:10 Amit: Let us review the LENS SOW details with Mandy."
        context = self.advisor._lookup_graph_context(dialogue)
        self.assertIn("Verified Graph Knowledge:", context)
        self.assertIn("LENS SOW Scope", context)
        self.assertIn("Apex Global Engineering", context)

    def test_async_graph_lookup(self):
        mock_graph = mock.MagicMock()
        mock_graph.nodes = {
            "node_rule": {
                "canonical_name": "Rule Engine",
                "display_name": "Rule Engine",
                "node_type": "system",
            }
        }
        mock_graph.forward_adjacency = {
            "node_rule": [
                {
                    "relationship_family": "depends_on",
                    "to_node_name": "OCR Service",
                    "edge_type": "DEPENDENCY",
                }
            ]
        }
        self.advisor.in_memory_graph = mock_graph

        received_feedback = []

        def on_feedback(res):
            received_feedback.append(res)

        # Submit turn asynchronously
        success = self.advisor.submit_turn_async(
            speaker="Amit",
            text="How does the Rule Engine interact with our OCR components?",
            timestamp="00:15",
            current_time=100.0,
            on_feedback=on_feedback,
        )
        self.assertTrue(success)

        # Wait for async thread to complete
        import time
        time.sleep(0.3)

        self.assertEqual(len(received_feedback), 1)
        fb = received_feedback[0]
        self.assertIn("graph_context", fb)
        self.assertIn("Rule Engine", fb["graph_context"])

    def test_bidirectional_assertion_indexing_and_acronyms(self):
        """Verifies bidirectional assertion indexing, acronym extraction, and IDF scoring."""
        mock_graph = mock.MagicMock()
        mock_graph.nodes = {
            "workstream_lens": {
                "canonical_name": "LENS Workstream",
                "display_name": "LENS",
                "node_type": "workstream",
            },
            "system_qa_gate": {
                "canonical_name": "QA Testing Engine",
                "display_name": "QA Engine",
                "node_type": "system",
            },
            "stakeholder_alex": {
                "canonical_name": "Alex Sanchez",
                "display_name": "Alex",
                "node_type": "stakeholder",
                "organization": "PartnerAdvisory",
            },
        }
        # Inbound and outbound assertions using SQLite schema keys
        mock_graph.assertions = {
            "asrt_1": {
                "subject_node_id": "stakeholder_alex",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "workstream_lens",
                "object_literal": "Alex Sanchez leads technical delivery for LENS",
                "canonical_status": "ACTIVE",
            },
            "asrt_2": {
                "subject_node_id": "workstream_lens",
                "predicate": "BLOCKED_BY",
                "object_node_id": "system_qa_gate",
                "object_literal": "LENS blocked by QA sign-off",
                "canonical_status": "ACTIVE",
            },
        }
        mock_graph.forward_adjacency = {}
        self.advisor.in_memory_graph = mock_graph
        self.advisor.retriever.update_topology(mock_graph)

        # 1. Query by acronym 'QA'
        ctx_qa = self.advisor.retriever.retrieve_context("What is the status of the QA gate?")
        self.assertIn("QA Testing Engine", ctx_qa)
        # Bidirectional check: system_qa_gate is the object of BLOCKED_BY
        self.assertIn("BLOCKED_BY", ctx_qa)

        # 2. Query by Alex: should surface both outbound OWNS_OR_LEADS and workstream_lens
        ctx_alex = self.advisor.retriever.retrieve_context("Who is Alex and what does he lead?")
        self.assertIn("Alex Sanchez", ctx_alex)
        self.assertIn("OWNS_OR_LEADS", ctx_alex)
        self.assertIn("Alex Sanchez leads technical delivery for LENS", ctx_alex)

        # 3. Query by LENS: should surface inbound ownership by Alex and outbound blocker by QA
        ctx_lens = self.advisor.retriever.retrieve_context("Are there blockers on LENS?")
        self.assertIn("LENS Workstream", ctx_lens)
        self.assertIn("BLOCKED_BY", ctx_lens)


if __name__ == "__main__":
    unittest.main()
