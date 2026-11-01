"""
tests/test_dual_track_copilot.py

Unit tests for Track 1 Flash Snap Engine and dual-track copilot execution.
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.flash_snap import FlashSnapEngine


class TestDualTrackCopilot(unittest.TestCase):
    def setUp(self):
        self.engine = FlashSnapEngine()

    def test_flash_snap_scada_nerc_cip_dilemma(self):
        utterance = "Can we connect to real-time SCADA data from outside the center?"
        res = self.engine.evaluate_utterance(utterance, speaker="Aaron", timestamp="[07:30]")
        self.assertIsNotNone(res)
        self.assertTrue(res["is_flash_snap"])
        self.assertEqual(res["track"], "TRACK_1_FLASH_SNAP")
        self.assertEqual(res["category"], "TECHNICAL")
        self.assertEqual(res["snap_cue"], "Decouple via PI Historian replica")
        self.assertIn("CIP-005", res["suggested_script"])
        self.assertLess(res["latency_ms"], 50.0)

    def test_flash_snap_2027_timeline_dilemma(self):
        utterance = "He wants to lean on that and not wait for 2027 for this other system to make it through."
        res = self.engine.evaluate_utterance(utterance, speaker="Drew Oakley", timestamp="[11:15]")
        self.assertIsNotNone(res)
        self.assertEqual(res["category"], "STRATEGIC")
        self.assertEqual(res["snap_cue"], "Anchor to grounded-contract proof spike")
        self.assertIn("grounded-contract spike", res["suggested_script"])

    def test_flash_snap_pano_saas_dilemma(self):
        utterance = "Pano AI detects smoke, but they have like ten technologies and are not really using it."
        res = self.engine.evaluate_utterance(utterance, speaker="Aaron", timestamp="[12:30]")
        self.assertIsNotNone(res)
        self.assertEqual(res["category"], "COMMERCIAL")
        self.assertEqual(res["snap_cue"], "Integrate Pano API, avoid rebuild")

    def test_flash_snap_short_or_chatter_returns_none(self):
        self.assertIsNone(self.engine.evaluate_utterance("Yeah. Okay."))
        self.assertIsNone(self.engine.evaluate_utterance("Good morning everyone."))

    def test_dual_track_execution_dispatches_track_1_immediately(self):
        advisor = LiveCopilotAdvisor(
            project_name="apex_trans",
            meeting_name="APEX_TRANS Internal Discussion",
            mock_mode=True,
            cooldown_seconds=10.0,
        )
        received_feedbacks = []

        def callback(fb):
            received_feedbacks.append(fb)

        utterance = "Can we connect to the real-time SCADA data directly?"
        dispatched = advisor.submit_turn_async(
            speaker="Aaron",
            text=utterance,
            timestamp="[05:00]",
            current_time=100.0,
            on_feedback=callback,
        )
        self.assertTrue(dispatched)
        # Verify Track 1 was delivered synchronously to callback
        self.assertGreaterEqual(len(received_feedbacks), 1)
        track1 = received_feedbacks[0]
        self.assertEqual(track1["track"], "TRACK_1_FLASH_SNAP")
        self.assertEqual(track1["snap_cue"], "Decouple via PI Historian replica")


if __name__ == "__main__":
    unittest.main()
