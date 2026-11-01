"""
tests/adversarial/test_copilot_prefetch_adversarial.py

Adversarial Edge-Case Test Suite for Copilot Graph Pre-Fetching:
Covers 5 mandatory governance categories:
1. Boundary Payloads: Empty graph (0 nodes, 0 assertions), near-zero timeout, null project metadata.
2. Malformed Input: Missing keys, None types in node/assertion dicts, integer-typed strings, invalid UTF-8 snippets.
3. Concurrency & Race: Multi-threaded simultaneous pre-fetch calls and concurrent packet formatting.
4. Overflow & Exhaustion: Thousands of oversized assertions attempting to blow the prompt context budget.
5. Invariant Bypass Attempts: Malicious injection of Rule C-19 internal advisory / career leverage markers across all fields.
"""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    C19_FORBIDDEN_MARKERS,
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.prefetch_bridge import CopilotPrefetchBridge
from dual_pipeline.live_meeting_recorder_core.session_context_packet import (
    SessionContextPacket,
    _is_c19_violating,
)


class TestCopilotPrefetchAdversarial(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. Boundary Payloads
    # -------------------------------------------------------------------------
    def test_boundary_empty_graph_and_none_retriever(self):
        empty_topo = SimpleNamespace(
            nodes={}, assertions={}, forward_adjacency={}, reverse_adjacency={}
        )
        bridge = CopilotPrefetchBridge(
            in_memory_graph=empty_topo, retriever=None, timeout_seconds=5.0
        )

        packet = bridge.prefetch(project_name="", meeting_name="", attendees=[])
        self.assertTrue(packet.is_empty)
        self.assertEqual(packet.total_items_count, 0)
        self.assertEqual(packet.format_for_copilot_prompt(), "")
        self.assertTrue(len(packet.packet_hash) == 64)

    def test_boundary_sub_millisecond_timeout(self):
        # Even with an enormous graph, an aggressive 0.0000001s timeout should never crash or hang
        large_nodes = {f"node_{i}": {"canonical_name": f"Node {i}"} for i in range(1000)}
        large_assertions = {
            f"ass_{i}": {
                "subject_node_id": f"node_{i}",
                "predicate": "DECISION",
                "object_literal": f"Decision {i}",
            }
            for i in range(1000)
        }
        topo = SimpleNamespace(
            nodes=large_nodes,
            assertions=large_assertions,
            forward_adjacency={},
            reverse_adjacency={},
        )
        bridge = CopilotPrefetchBridge(in_memory_graph=topo, timeout_seconds=0.0000001)

        packet = bridge.prefetch(project_name="apex_trans", meeting_name="Heavy Graph Stress")
        self.assertIsInstance(packet, SessionContextPacket)

    # -------------------------------------------------------------------------
    # 2. Malformed Input
    # -------------------------------------------------------------------------
    def test_malformed_graph_nodes_and_assertions(self):
        # Nodes and assertions containing None values, wrong types, missing fields
        corrupt_nodes = {
            "n1": None,  # None node entry
            "n2": {"canonical_name": None, "node_type": 12345},  # None string, int type
            "n3": {},  # completely empty dict
            "n4": {"canonical_name": "Valid Node", "description": None},
        }
        corrupt_assertions = {
            "a1": None,
            "a2": {"subject_node_id": None, "predicate": None, "object_literal": None},
            "a3": "Not a dict at all",
            "a4": {"subject_node_id": "n4", "predicate": 9999, "object_literal": 8888},
            "a5": {
                "subject_node_id": "n4",
                "predicate": "DECISION",
                "object_literal": "Valid Decision",
            },
        }
        topo = SimpleNamespace(
            nodes=corrupt_nodes,
            assertions=corrupt_assertions,
            forward_adjacency={},
            reverse_adjacency={},
        )
        retriever = CopilotGraphRetriever(topo)
        bridge = CopilotPrefetchBridge(
            in_memory_graph=topo, retriever=retriever, timeout_seconds=5.0
        )

        # Must not raise exceptions
        packet = bridge.prefetch(
            project_name="Valid",
            meeting_name=None,  # type: ignore
            attendees=[None, {}, {"name": 123}],  # type: ignore
        )
        self.assertIsInstance(packet, SessionContextPacket)
        self.assertFalse(packet.is_empty)
        self.assertEqual(len(packet.open_decisions), 1)

    # -------------------------------------------------------------------------
    # 3. Concurrency & Race
    # -------------------------------------------------------------------------
    def test_concurrency_multi_threaded_prefetch_and_format(self):
        nodes = {f"n_{i}": {"canonical_name": f"APEX_TRANS Entity {i}"} for i in range(50)}
        assertions = {
            f"a_{i}": {
                "subject_node_id": f"n_{i}",
                "predicate": "DECISION" if i % 2 == 0 else "RISK",
                "object_literal": f"Strategic choice {i} for APEX_TRANS project",
            }
            for i in range(50)
        }
        topo = SimpleNamespace(
            nodes=nodes, assertions=assertions, forward_adjacency={}, reverse_adjacency={}
        )
        retriever = CopilotGraphRetriever(topo)
        bridge = CopilotPrefetchBridge(
            in_memory_graph=topo, retriever=retriever, timeout_seconds=5.0
        )

        results = []
        errors = []

        def worker(idx):
            try:
                pkt = bridge.prefetch(
                    project_name="apex_trans",
                    meeting_name=f"Concurrent Session {idx}",
                    attendees=[{"name": f"Attendee {idx}"}],
                )
                formatted = pkt.format_for_copilot_prompt(max_chars=1000)
                results.append((len(formatted), pkt.packet_hash))
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, i) for i in range(24)]
            for f in futures:
                f.result()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 24)
        # All workers saw deterministic hashes
        sample_hash = results[0][1]
        self.assertTrue(all(h == sample_hash for _, h in results))

    # -------------------------------------------------------------------------
    # 4. Overflow & Exhaustion
    # -------------------------------------------------------------------------
    def test_overflow_massive_payload_truncation(self):
        # Create assertions with massive text blocks attempting to blow LLM token budget
        massive_text = "OVERFLOW_ATTEMPT_" * 500  # ~8,500 characters
        overflow_packet = SessionContextPacket(
            project_scope="stress_project",
            open_decisions=[{"text": massive_text} for _ in range(20)],
            open_risks=[{"text": massive_text} for _ in range(20)],
            open_action_items=[{"text": massive_text} for _ in range(20)],
            recent_assertions=[{"text": massive_text} for _ in range(20)],
        )

        formatted = overflow_packet.format_for_copilot_prompt(max_chars=2500)
        # Must enforce strict length truncation
        self.assertLessEqual(len(formatted), 2600)
        self.assertIn("Truncated", formatted)

    # -------------------------------------------------------------------------
    # 5. Invariant Bypass Attempts (Rule C-19 Career Leverage Firewall)
    # -------------------------------------------------------------------------
    def test_c19_firewall_adversarial_injection(self):
        for marker in C19_FORBIDDEN_MARKERS:
            # Direct text check
            self.assertTrue(_is_c19_violating(f"Discussion about {marker} in meeting"))
            self.assertTrue(_is_c19_violating(marker.upper()))
            self.assertTrue(_is_c19_violating(marker.lower()))

        # Attempt to inject C-19 items into SessionContextPacket constructor
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=[
                {"text": "Safe decision 1"},
                {"text": "Bypass attempt with $1.2M budget"},
                {"text": "Bypass attempt with internal_advisory note"},
            ],
            open_risks=[
                {"text": "Safe risk 1"},
                {"text": "Risk regarding compensation_trajectory and political_rationale"},
            ],
            open_action_items=[
                {"text": "Safe action 1"},
                {"text": "Action using networking_wedge for career_leverage"},
            ],
            recent_assertions=[
                {"text": "Safe fact 1"},
                {"text": "Sneaky $1.2m transaction"},
            ],
        )

        # Every malicious item must have been dropped
        self.assertEqual(len(packet.open_decisions), 1)
        self.assertEqual(packet.open_decisions[0]["text"], "Safe decision 1")

        self.assertEqual(len(packet.open_risks), 1)
        self.assertEqual(packet.open_risks[0]["text"], "Safe risk 1")

        self.assertEqual(len(packet.open_action_items), 1)
        self.assertEqual(packet.open_action_items[0]["text"], "Safe action 1")

        self.assertEqual(len(packet.recent_assertions), 1)
        self.assertEqual(packet.recent_assertions[0]["text"], "Safe fact 1")

        # Formatted prompt must be completely free of C-19 markers
        formatted = packet.format_for_copilot_prompt()
        for marker in C19_FORBIDDEN_MARKERS:
            self.assertNotIn(marker.lower(), formatted.lower())


if __name__ == "__main__":
    unittest.main()
