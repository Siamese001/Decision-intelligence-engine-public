"""
tests/test_copilot_session_lifecycle.py

Unit tests for Wave 4 Session Lifecycle Integration:
Tests CLI flag parsing (--copilot-prefetch / --no-copilot-prefetch),
session manifest recording of prefetch telemetry, and session staleness monitoring.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.session_context_packet import SessionContextPacket


class TestCopilotSessionLifecycle(unittest.TestCase):
    def test_cli_parser_copilot_prefetch_flags(self):
        parser = build_parser()

        # Default should have copilot_prefetch True
        args_default = parser.parse_args(["record-meeting", "--meeting", "Sync"])
        self.assertTrue(getattr(args_default, "copilot_prefetch", True))

        # Explicit --copilot-prefetch
        args_on = parser.parse_args(["record-meeting", "--meeting", "Sync", "--copilot-prefetch"])
        self.assertTrue(args_on.copilot_prefetch)

        # Explicit --no-copilot-prefetch
        args_off = parser.parse_args(
            ["record-meeting", "--meeting", "Sync", "--no-copilot-prefetch"]
        )
        self.assertFalse(args_off.copilot_prefetch)

    def test_manifest_contains_prefetch_metadata_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "output"
            cache_dir = Path(tmpdir) / "cache"
            out_dir.mkdir(parents=True)
            cache_dir.mkdir(parents=True)

            packet = SessionContextPacket(
                project_scope="apex_trans",
                open_decisions=[{"text": "Lone Star rollout"}],
                open_risks=[{"text": "SCADA airgap"}],
                retrieval_latency_ms=18.4,
            )

            recorder = None
            try:
                recorder = LiveMeetingRecorder(
                    project_name="apex_trans",
                    meeting_name="Prefetch Enabled Session",
                    meeting_date="10-01-2026",
                    mock_mode=True,
                    enable_copilot=True,
                    enable_copilot_prefetch=True,
                    output_dir=out_dir,
                )
                recorder.cache_dir = cache_dir
                recorder.session_manifest = cache_dir / "manifest.json"

                # Inject packet into advisor
                if recorder.copilot_advisor:
                    recorder.copilot_advisor.session_context_packet = packet

                recorder._write_session_manifest(datetime.now())

                self.assertTrue(recorder.session_manifest.is_file())
                with open(recorder.session_manifest, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.assertIn("copilot_prefetch", data)
                prefetch_data = data["copilot_prefetch"]
                self.assertTrue(prefetch_data["enabled"])
                self.assertEqual(prefetch_data["decisions_count"], 1)
                self.assertEqual(prefetch_data["risks_count"], 1)
                self.assertEqual(prefetch_data["latency_ms"], 18.4)
                self.assertTrue(len(prefetch_data["packet_hash"]) == 64)
            finally:
                if recorder and getattr(recorder, "_slug_lock_fd", None):
                    with contextlib.suppress(Exception):
                        recorder._slug_lock_fd.close()

    def test_manifest_shows_disabled_when_prefetch_off(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "output"
            cache_dir = Path(tmpdir) / "cache"
            out_dir.mkdir(parents=True)
            cache_dir.mkdir(parents=True)

            recorder = None
            try:
                recorder = LiveMeetingRecorder(
                    project_name="apex_trans",
                    meeting_name="Prefetch Disabled Session",
                    meeting_date="10-01-2026",
                    mock_mode=True,
                    enable_copilot=False,
                    output_dir=out_dir,
                )
                recorder.cache_dir = cache_dir
                recorder.session_manifest = cache_dir / "manifest.json"

                recorder._write_session_manifest(datetime.now())

                with open(recorder.session_manifest, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.assertIn("copilot_prefetch", data)
                self.assertFalse(data["copilot_prefetch"]["enabled"])
            finally:
                if recorder and getattr(recorder, "_slug_lock_fd", None):
                    with contextlib.suppress(Exception):
                        recorder._slug_lock_fd.close()

    def test_staleness_warning_at_45_minutes(self):
        recorder = None
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "output"
            cache_dir = Path(tmpdir) / "cache"
            try:
                recorder = LiveMeetingRecorder(
                    project_name="apex_trans",
                    meeting_name="Lengthy Executive Review",
                    output_dir=out_dir,
                    cache_dir=cache_dir,
                    mock_mode=True,
                    enable_copilot=True,
                )
                self.assertFalse(recorder._copilot_staleness_warned)

                with patch("dual_pipeline.live_meeting_recorder.logger") as mock_logger:
                    # 30 minutes in -> no warning
                    recorder._handle_speech_turn_copilot(
                        chunk_idx=10,
                        sec=30 * 60,
                        time_str="[30:00]",
                        text="Continuing discussion on charter.",
                        rms=0.02,
                        spk="Mandy",
                    )
                    self.assertFalse(recorder._copilot_staleness_warned)
                    mock_logger.warning.assert_not_called()

                    # 46 minutes in -> warning logged once
                    recorder._handle_speech_turn_copilot(
                        chunk_idx=25,
                        sec=46 * 60,
                        time_str="[46:00]",
                        text="Reviewing budget allocations.",
                        rms=0.02,
                        spk="Mandy",
                    )
                    self.assertTrue(recorder._copilot_staleness_warned)
                    mock_logger.warning.assert_called_once()
                    self.assertIn("stale", mock_logger.warning.call_args[0][0])
            finally:
                if recorder and getattr(recorder, "_slug_lock_fd", None):
                    with contextlib.suppress(Exception):
                        recorder._slug_lock_fd.close()


if __name__ == "__main__":
    unittest.main()
