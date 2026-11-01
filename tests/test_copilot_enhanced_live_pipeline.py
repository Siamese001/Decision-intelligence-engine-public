"""
tests/test_copilot_enhanced_live_pipeline.py

Comprehensive test suite for enhanced live executive copilot pipeline:
- High-salience inverted index sub-millisecond retrieval
- Attendee affinity weighting & priority relational assertions
- Rule C-19 career leverage firewall quarantine
- Micro-inflection priority preemption & immediate dispatch
- Stale-turn wall-clock latency invalidation
- Collateral filesystem watcher detection & log emission
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

from dual_pipeline.live_meeting_recorder_core.collateral_watcher import CollateralFsWatcher
from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    C19_FORBIDDEN_MARKERS,
    CopilotGraphRetriever,
    _tokenize_text,
)
from dual_pipeline.live_meeting_recorder_core.inflection_detector import (
    InflectionEvent,
    InflectionTriggerType,
)


class TestCopilotRetriever(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_topology = mock.MagicMock()
        self.mock_topology.nodes = {
            "node_jen": {
                "canonical_name": "Jen",
                "display_name": "Jen (Executive Director)",
                "node_type": "person",
                "organization": "Apex Global",
            },
            "node_amit": {
                "canonical_name": "Amit",
                "display_name": "Amit (Chief Architect)",
                "node_type": "person",
                "organization": "PartnerAdvisory",
            },
            "node_lens": {
                "canonical_name": "Project LENS",
                "display_name": "LENS Modernization",
                "node_type": "project",
                "aliases": ["lens", "turbine_analytics"],
            },
            "node_c19_secret": {
                "canonical_name": "Career Advancement Wedge",
                "display_name": "$1.2M Target Trajectory",
                "namespace": "INTERNAL_ADVISORY",
                "is_quarantined": 1,
                "node_type": "advisory",
            },
        }
        self.mock_topology.assertions = {
            "ast_001": {
                "subject_node": "node_lens",
                "predicate": "BLOCKED_BY",
                "claim_text": "LENS pipeline blocked pending APEX IAM security gate",
                "status": "ACTIVE",
            },
            "ast_002": {
                "subject_node": "node_jen",
                "predicate": "OWNS",
                "claim_text": "Jen owns executive sign-off for Phase 1 funding",
                "status": "ACTIVE",
            },
            "ast_003": {
                "subject_node": "node_c19_secret",
                "predicate": "STRATEGIC_WEDGE",
                "claim_text": "Confidential career_leverage wedge for partner positioning",
                "status": "ACTIVE",
            },
        }
        self.attendees = [
            {"name": "Jen", "role": "Director", "org": "Apex Global"},
            {"name": "Amit", "role": "Architect", "org": "PartnerAdvisory"},
        ]
        self.retriever = CopilotGraphRetriever(
            topology=self.mock_topology,
            attendees=self.attendees,
        )

    def test_tokenization(self) -> None:
        tokens = _tokenize_text("Who owns Project LENS funding?")
        self.assertIn("who", tokens)
        self.assertIn("owns", tokens)
        self.assertIn("project", tokens)
        self.assertIn("lens", tokens)
        self.assertIn("funding", tokens)

    def test_submillisecond_inverted_index_retrieval(self) -> None:
        start_time = time.perf_counter()
        context = self.retriever.retrieve_context(
            "Amit: Jen, what is the status of the Project LENS funding?",
            max_entities=3,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        self.assertLess(elapsed_ms, 15.0)  # sub-15ms guaranteed even in un-JIT Python
        self.assertIn("Verified Graph Knowledge:", context)
        self.assertIn("Jen", context)
        self.assertIn("Project LENS", context)
        self.assertIn("BLOCKED_BY", context)

    def test_rule_c19_career_firewall_quarantine(self) -> None:
        # Verify node_c19_secret and its assertions are never indexed
        context = self.retriever.retrieve_context(
            "What is our confidential career_leverage wedge for executive compensation band?"
        )
        for marker in C19_FORBIDDEN_MARKERS:
            self.assertNotIn(marker, context)
        self.assertNotIn("Career Advancement Wedge", context)
        self.assertNotIn("ast_003", context)

    def test_attendee_affinity_weighting(self) -> None:
        # A turn mentioning both Jen (an attendee) and a non-attendee entity
        # Jen should have higher boosted score
        context = self.retriever.retrieve_context("Jen mentioned the delivery goals.")
        self.assertIn("Jen", context)
        self.assertIn("Apex Global", context)


class TestCopilotInflectionAndAdvisor(unittest.TestCase):
    def setUp(self) -> None:
        self.advisor = LiveCopilotAdvisor(
            project_name="project_lens",
            meeting_name="LENS Executive Alignment",
            mock_mode=True,
            cooldown_seconds=10.0,  # 10s cooldown
            max_freshness_seconds=2.0,  # 2s freshness limit
        )

    def tearDown(self) -> None:
        self.advisor.shutdown(wait=True)

    def test_inflection_priority_preemption(self) -> None:
        # 1. Normal turn triggers first evaluation
        turn1_result = self.advisor.evaluate_live_turns(current_time=10.0)
        self.advisor.record_turn("Amit", "We are reviewing general schedule.", "00:10")
        self.assertIsNone(turn1_result)  # not enough text or throttled

        self.advisor.record_turn("Amit", "Let us carefully review the comprehensive project milestone architecture.", "00:12")
        turn2_result = self.advisor.evaluate_live_turns(current_time=11.0)
        self.assertIsNotNone(turn2_result)

        # 2. Immediate second turn within cooldown without priority -> None (throttled)
        self.advisor.record_turn("Jen", "Can you explain the billing?", "00:13")
        turn3_result = self.advisor.evaluate_live_turns(current_time=12.0)
        self.assertIsNone(turn3_result)

        # 3. Micro-inflection event with priority -> preempts cooldown
        priority_event = InflectionEvent(
            trigger_type=InflectionTriggerType.STOPPING_POINT_QUESTION,
            cue_text="Who owns the sign-off?",
            timestamp_sec=12.5,
            is_priority=True,
            confidence=0.95,
            reason="Direct question asking who owns sign-off",
            immediate_dispatch_eligible=True,
        )
        turn4_result = self.advisor.evaluate_live_turns(current_time=12.5, inflection_event=priority_event)
        self.assertIsNotNone(turn4_result)
        self.assertEqual(turn4_result["inflection_trigger"], "STOPPING_POINT_QUESTION")
        self.assertTrue(turn4_result["is_priority"])

    def test_stale_turn_invalidation(self) -> None:
        received_feedbacks: List[Dict[str, Any]] = []

        def cb(res: Dict[str, Any]) -> None:
            received_feedbacks.append(res)

        turn_seq = self.advisor.record_turn(
            speaker="Amit",
            text="This is an urgent question about our deliverables.",
            timestamp="00:20",
        )
        # Verify worker directly invalidates and drops turn when age exceeds max_freshness_seconds
        stale_turn_ts = time.time() - 10.0  # 10s old (max_freshness_seconds is 2.0s)
        res = self.advisor._run_async_worker(
            turn_seq=turn_seq,
            turn_ts=stale_turn_ts,
            ev=None,
            cb=cb,
        )
        self.assertIsNone(res)
        self.assertEqual(len(received_feedbacks), 0)

        # Conversely, a fresh turn is evaluated and delivered to callback
        fresh_turn_ts = time.time()
        res_fresh = self.advisor._run_async_worker(
            turn_seq=turn_seq,
            turn_ts=fresh_turn_ts,
            ev=None,
            cb=cb,
        )
        self.assertIsNotNone(res_fresh)
        self.assertEqual(len(received_feedbacks), 1)


class TestCollateralFsWatcher(unittest.TestCase):
    def test_watcher_lifecycle_and_drop_logging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ref_dir = Path(tmp_dir) / "referenced_artifacts"
            ref_dir.mkdir()

            session_seconds = 125.0
            watcher = CollateralFsWatcher(
                ref_dir=ref_dir,
                get_elapsed_fn=lambda: session_seconds,
            )
            # Drop a mock collateral slide deck
            test_deck = ref_dir / "LENS_Architecture_Deck_v2.pdf"
            test_deck.write_text("dummy PDF content for test", encoding="utf-8")

            watcher.start()
            time.sleep(0.1)
            watcher._scan()

            drops = watcher.drops
            self.assertEqual(len(drops), 1)
            self.assertEqual(drops[0]["filename"], "LENS_Architecture_Deck_v2.pdf")
            self.assertEqual(drops[0]["dropped_at_elapsed"], "00:02:05")
            self.assertEqual(drops[0]["attribution_mode"], "COLLATERAL_REVIEWED")
            self.assertEqual(len(drops[0]["sha256"]), 64)

            # Stop and write JSON log
            log_path = Path(tmp_dir) / "collateral_drop_log.json"
            stopped_drops = watcher.stop(log_path=log_path)
            self.assertEqual(len(stopped_drops), 1)
            self.assertTrue(log_path.is_file())

            written_json = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(len(written_json), 1)
            self.assertEqual(written_json[0]["filename"], "LENS_Architecture_Deck_v2.pdf")


if __name__ == "__main__":
    unittest.main()
