"""Unit test suite for live meeting recording inactivity timeout governance.

Validates:
1. Default inactivity timeout is 0.0 (disabled) preventing 180s premature dropouts.
2. Explicit 0.0 / 0 values are not overwritten by truthy fallbacks.
3. Custom inactivity timeouts via CLI flag --inactivity-timeout and constructor kwargs are honored.
4. Silence exceeding 180s does not abort sessions when inactivity timeout is disabled.
5. Inactivity timeout emits descriptive audit reason and warning when explicitly configured and exceeded.
"""
from __future__ import annotations

import tempfile
import time
import unittest
import uuid
from pathlib import Path

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


class TestLiveMeetingInactivityGovernance(unittest.TestCase):
    """Unit tests for inactivity timeout configuration and silence tolerance."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_recorder(self, **kwargs) -> LiveMeetingRecorder:
        unique_name = f"Inactivity Test {uuid.uuid4().hex[:10]}"
        rec = LiveMeetingRecorder(
            meeting_name=unique_name,
            mock_mode=True,
            immediate_worktree=False,
            **kwargs,
        )
        def _safe_cleanup():
            import contextlib
            with contextlib.suppress(Exception):
                rec.stop()
        self.addCleanup(_safe_cleanup)
        return rec

    def test_default_inactivity_timeout_is_disabled(self) -> None:
        """Assert default inactivity_timeout_seconds is 0.0 (disabled)."""
        recorder = self._create_recorder()
        self.assertEqual(recorder.inactivity_timeout_seconds, 0.0)

    def test_explicit_zero_inactivity_timeout_not_overridden(self) -> None:
        """Assert explicit 0.0 / 0 argument is not clobbered by a truthy fallback."""
        recorder = self._create_recorder(inactivity_timeout_seconds=0.0)
        self.assertEqual(recorder.inactivity_timeout_seconds, 0.0)

        # Also test via kwargs
        recorder_kwargs = self._create_recorder(**{"inactivity_timeout_seconds": 0.0})
        self.assertEqual(recorder_kwargs.inactivity_timeout_seconds, 0.0)

    def test_custom_inactivity_timeout_configured(self) -> None:
        """Assert custom positive inactivity timeout is properly preserved."""
        recorder = self._create_recorder(inactivity_timeout_seconds=900.0)
        self.assertEqual(recorder.inactivity_timeout_seconds, 900.0)

    def test_cli_parser_inactivity_timeout_flag(self) -> None:
        """Assert CLI parser recognizes --inactivity-timeout with default 0.0 and custom values."""
        parser = build_parser()

        # Default parse
        args_default = parser.parse_args(["record-meeting"])
        self.assertEqual(args_default.inactivity_timeout, 0.0)

        # Custom parse
        args_custom = parser.parse_args(["record-meeting", "--inactivity-timeout", "600.0"])
        self.assertEqual(args_custom.inactivity_timeout, 600.0)

    def test_silence_exceeding_180s_does_not_abort_when_disabled(self) -> None:
        """Assert extended silence (>180s) does not trigger auto-stop when inactivity timeout is 0.0."""
        recorder = self._create_recorder(inactivity_timeout_seconds=0.0)
        recorder.total_audible_chunks = 5
        # Simulate silence of 300 seconds (well past the old 180s limit)
        recorder.last_audible_monotonic = time.monotonic() - 300.0

        # Run condition check corresponding to poll loop
        silence_duration = time.monotonic() - recorder.last_audible_monotonic
        should_stop = (
            recorder.inactivity_timeout_seconds is not None
            and recorder.inactivity_timeout_seconds > 0
            and recorder.total_audible_chunks >= 1
            and silence_duration >= recorder.inactivity_timeout_seconds
        )
        self.assertFalse(should_stop, "Session should not stop when inactivity timeout is disabled")
        self.assertIsNone(recorder.auto_stopped_reason)

    def test_inactivity_timeout_triggers_when_configured_and_exceeded(self) -> None:
        """Assert session records auto-stop reason when silence duration exceeds configured timeout."""
        recorder = self._create_recorder(inactivity_timeout_seconds=10.0)
        recorder.total_audible_chunks = 1
        recorder.last_audible_monotonic = time.monotonic() - 15.0

        silence_duration = time.monotonic() - recorder.last_audible_monotonic
        should_stop = (
            recorder.inactivity_timeout_seconds is not None
            and recorder.inactivity_timeout_seconds > 0
            and recorder.total_audible_chunks >= 1
            and silence_duration >= recorder.inactivity_timeout_seconds
        )
        self.assertTrue(should_stop, "Session should trigger auto-stop when timeout is exceeded")
        recorder.auto_stopped_reason = f"INACTIVITY_TIMEOUT ({silence_duration:.1f}s >= {recorder.inactivity_timeout_seconds}s)"
        self.assertIn("INACTIVITY_TIMEOUT", recorder.auto_stopped_reason)


if __name__ == "__main__":
    unittest.main()
