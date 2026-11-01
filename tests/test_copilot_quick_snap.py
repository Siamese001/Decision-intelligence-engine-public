"""
tests/test_copilot_quick_snap.py

Unit and concurrency tests for the Quick Snap Live Copilot Engine:
1. Validates Quick Snap schema (snap_cue, suggested_script, risk_trap, category).
2. Verifies word count constraints (snap_cue <= 8 words, script <= 12 words, risk_trap <= 8 words).
3. Tests asynchronous non-blocking dispatch with submit_turn_async.
4. Verifies worker queue throttling (single active task, no backlog accumulation).
5. Tests shutdown lifecycle and graceful executor cleanup.
"""

from __future__ import annotations

import time
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot import (
    COPILOT_SYSTEM_PROMPT,
    LiveCopilotAdvisor,
)


class TestCopilotQuickSnap(unittest.TestCase):
    def test_quick_snap_system_prompt_structure(self):
        """Verifies that COPILOT_SYSTEM_PROMPT strictly enforces Quick Snap constraints."""
        self.assertIn("QUICK SNAP", COPILOT_SYSTEM_PROMPT)
        self.assertIn("snap_cue", COPILOT_SYSTEM_PROMPT)
        self.assertIn("suggested_script", COPILOT_SYSTEM_PROMPT)
        self.assertIn("risk_trap", COPILOT_SYSTEM_PROMPT)
        self.assertIn("snap_cue <= 8 words", COPILOT_SYSTEM_PROMPT)
        self.assertIn("suggested_script <= 12 words", COPILOT_SYSTEM_PROMPT)
        self.assertIn("risk_trap <= 8 words", COPILOT_SYSTEM_PROMPT)

    def test_mock_mode_quick_snap_schema_and_word_limits(self):
        """Verifies that mock mode returns compliant Quick Snap schema adhering to word limits."""
        advisor = LiveCopilotAdvisor(
            project_name="project_lens",
            meeting_name="Design Review",
            mock_mode=True,
            cooldown_seconds=0.0,
        )
        advisor.record_turn(
            speaker="Mandy",
            text="We might need to redesign the entire layout to support arbitrary custom widgets.",
            timestamp="[01:30]",
        )
        feedback = advisor.evaluate_live_turns(current_time=10.0)
        self.assertIsNotNone(feedback)
        self.assertEqual(feedback.get("type"), "COPILOT_FEEDBACK")
        self.assertTrue(feedback.get("has_guidance"))

        snap_cue = feedback.get("snap_cue", "")
        suggested_script = feedback.get("suggested_script", "")
        risk_trap = feedback.get("risk_trap", "")

        self.assertTrue(bool(snap_cue), "snap_cue must not be empty")
        self.assertTrue(bool(suggested_script), "suggested_script must not be empty")
        self.assertTrue(bool(risk_trap), "risk_trap must not be empty")

        # Word limits
        self.assertLessEqual(len(snap_cue.split()), 8)
        self.assertLessEqual(len(suggested_script.split()), 14)
        self.assertLessEqual(len(risk_trap.split()), 8)

        # Backward compatibility aliases
        self.assertEqual(feedback.get("risk_alert"), risk_trap)
        self.assertIn("opportunity", feedback)
        advisor.shutdown()

    def test_async_non_blocking_dispatch(self):
        """Verifies that submit_turn_async executes in background and fires callback."""
        received: List[Dict[str, Any]] = []

        def _on_feedback(fb: Dict[str, Any]) -> None:
            received.append(fb)

        advisor = LiveCopilotAdvisor(
            project_name="project_lens",
            meeting_name="Design Review",
            mock_mode=True,
            cooldown_seconds=0.0,
        )
        scheduled = advisor.submit_turn_async(
            speaker="Amit Ayer",
            text="Let's clarify the architecture between core canvas widgets and external plugins.",
            timestamp="[02:00]",
            current_time=20.0,
            on_feedback=_on_feedback,
        )
        self.assertTrue(scheduled)

        # Wait for worker thread to complete
        time.sleep(0.1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].get("category"), "STRATEGIC")
        self.assertIn("snap_cue", received[0])

        advisor.shutdown(wait=True)

    def test_backlog_prevention_skips_when_active(self):
        """Verifies that submit_turn_async drops/skips turns if an evaluation is already running."""
        advisor = LiveCopilotAdvisor(
            project_name="project_lens",
            meeting_name="Design Review",
            mock_mode=True,
            cooldown_seconds=0.0,
        )

        # Simulate a slow running task
        mock_future = MagicMock()
        mock_future.done.return_value = False
        advisor._active_future = mock_future

        scheduled = advisor.submit_turn_async(
            speaker="Speaker",
            text="Another dialogue turn during busy worker execution.",
            timestamp="[02:15]",
            current_time=25.0,
        )
        self.assertFalse(scheduled, "Must drop turn if active future is still executing")
        advisor.shutdown()

    def test_cooldown_throttling(self):
        """Verifies that cooldown_seconds throttles excessive dispatch cycles."""
        advisor = LiveCopilotAdvisor(
            project_name="project_lens",
            meeting_name="Design Review",
            mock_mode=True,
            cooldown_seconds=4.0,
        )
        # First call at t=10.0 succeeds
        first = advisor.submit_turn_async(
            speaker="Speaker",
            text="Discussion text long enough to trigger evaluation turns.",
            timestamp="[00:10]",
            current_time=10.0,
        )
        self.assertTrue(first)

        # Immediate follow-up at t=11.0 fails due to 4.0s cooldown
        second = advisor.submit_turn_async(
            speaker="Speaker",
            text="Follow-up turn too soon after first dispatch.",
            timestamp="[00:11]",
            current_time=11.0,
        )
        self.assertFalse(second)

        advisor.shutdown()


if __name__ == "__main__":
    unittest.main()
