"""
tests/test_recording_start_dispatch.py

Wave 2: Unit and integration tests for recording start dispatch latency,
host integration boundaries, immediate start receipts, and hardware isolation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.engine import _handle_record_meeting
from dual_pipeline.live_meeting_recorder_core.recording_host_integration import (
    HOST_INTEGRATION_SURFACE,
    ImmediateStartReceipt,
    build_immediate_start_receipt,
    validate_pre_dispatch_intent,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    RecordingServiceAdapter,
    RecordingStartRequest,
    RecordingState,
    RecordingStatusResponse,
    RecordingStopRequest,
)


class TestRecordingStartDispatch(unittest.TestCase):
    """Verifies fast dispatch, structured receipts, and pre-dispatch boundaries."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.adapter = RecordingServiceAdapter(repo_root=self.repo_root)

    def tearDown(self) -> None:
        for status in self.adapter.list_active_sessions():
            self.adapter.stop_recording(RecordingStopRequest(session_id=status.session_id))
        self.temp_dir.cleanup()

    def test_pre_dispatch_intent_rejections(self) -> None:
        """Verifies help, dry-run, and invalid commands are rejected before hardware initialization."""
        # 1. Empty args
        ok, reason = validate_pre_dispatch_intent([])
        self.assertFalse(ok)
        self.assertIn("EMPTY_ARGV", str(reason))

        # 2. Non-recording command
        ok, reason = validate_pre_dispatch_intent(["status", "--json"])
        self.assertFalse(ok)
        self.assertIn("NOT_RECORD_INTENT", str(reason))

        # 3. Help query (-h)
        ok, reason = validate_pre_dispatch_intent(["record-meeting", "-h"])
        self.assertFalse(ok)
        self.assertIn("HELP_INTENT", str(reason))

        # 4. Help query (--help)
        ok, reason = validate_pre_dispatch_intent(["record", "--help"])
        self.assertFalse(ok)
        self.assertIn("HELP_INTENT", str(reason))

        # 5. Dry-run query
        ok, reason = validate_pre_dispatch_intent(["record-meeting", "--meeting", "Test", "--dry-run"])
        self.assertFalse(ok)
        self.assertIn("DRY_RUN_INTENT", str(reason))

    def test_pre_dispatch_intent_valid(self) -> None:
        """Verifies valid start intent passes pre-dispatch validation."""
        ok, reason = validate_pre_dispatch_intent(["record-meeting", "--meeting", "Engineering Sync"])
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_start_dispatch_latency_under_250ms(self) -> None:
        """Verifies start_recording dispatches well under 250ms budget ceiling."""
        req = RecordingStartRequest(
            project_name="proj_test",
            meeting_name="Fast Sync",
            mock_mode=True,
            segment_seconds=5,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec_cls.return_value = mock_rec

            t0 = time.perf_counter()
            status = self.adapter.start_recording(req)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.assertLess(elapsed_ms, HOST_INTEGRATION_SURFACE["budget_ceiling_ms"])
            self.assertIn(status.recording_state, (RecordingState.STARTING, RecordingState.CAPTURING))
            self.assertIsNotNone(status.dispatch_monotonic)

    def test_immediate_start_receipt_generation(self) -> None:
        """Verifies immediate structured start receipt generation with accurate latency calculation."""
        req = RecordingStartRequest(
            project_name="proj_receipt",
            meeting_name="Receipt Verification",
            mock_mode=True,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec.master_wav = Path("/tmp/mock_master.wav")
            mock_rec_cls.return_value = mock_rec

            status, receipt = self.adapter.start_recording_with_receipt(req)

            self.assertIsInstance(receipt, ImmediateStartReceipt)
            self.assertEqual(receipt.status, "PASS")
            self.assertEqual(receipt.session_id, status.session_id)
            self.assertEqual(receipt.request_id, status.request_id)
            self.assertIsNotNone(receipt.dispatched_at_iso)
            self.assertGreaterEqual(receipt.pre_dispatch_latency_ms, 0.0)

            # Receipt dictionary export
            d = receipt.to_dict()
            self.assertEqual(d["session_id"], status.session_id)
            self.assertEqual(d["status"], "PASS")

    def test_cli_dry_run_never_opens_hardware(self) -> None:
        """Verifies CLI execution with --dry-run enforces mock_mode and never opens real hardware."""
        args = argparse.Namespace(
            command="record-meeting",
            meeting="Dry Run Isolation",
            project=None,
            date=None,
            attendees=None,
            device=":0",
            segment_seconds=5,
            whisper_prompt=None,
            backend="auto",
            audio_source="auto",
            whisper_model=None,
            copilot=False,
            no_copilot=True,
            copilot_prefetch=False,
            dry_run=True,
            detach=False,
            session_id=None,
            resume=False,
            immediate_worktree=False,
            fast_start=True,
            reviewer="operator",
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.session_id = "test_dry_run_id"
            mock_manifest = MagicMock()
            mock_manifest.parent_pid = os.getpid()
            mock_rec.session_registry.inspect.return_value = mock_manifest
            mock_rec.meeting_name = "Dry Run Isolation"
            mock_rec.project_name = "UNRESOLVED"
            mock_rec.meeting_date_display = "10-07-2026"
            mock_rec.date_dirname = "10-07-2026"
            mock_rec.output_dir = self.repo_root / "out"
            mock_rec.transcript_md = self.repo_root / "transcript.md"
            mock_rec.attendees = []
            mock_rec.backend = "auto"
            mock_rec.audio_source = "auto"
            mock_rec.enable_copilot = False
            mock_rec_cls.return_value = mock_rec

            res = _handle_record_meeting(args)

            self.assertEqual(res["status"], "PASS")
            self.assertTrue(res["dry_run"])
            # Invariant: mock_mode must be passed as True to LiveMeetingRecorder when dry_run=True
            _, kwargs = mock_rec_cls.call_args
            self.assertTrue(kwargs.get("mock_mode"), "dry_run MUST enforce mock_mode=True to isolate hardware")

    def test_cli_detach_returns_immediate_receipt(self) -> None:
        """Verifies CLI execution with --detach returns immediate start receipt without blocking."""
        args = argparse.Namespace(
            command="record-meeting",
            meeting="Detached Sync",
            project="proj_detach",
            date=None,
            attendees="Alice, Bob",
            device=":0",
            segment_seconds=5,
            whisper_prompt=None,
            backend="auto",
            audio_source="auto",
            whisper_model=None,
            copilot=False,
            no_copilot=True,
            copilot_prefetch=False,
            dry_run=False,
            detach=True,
            session_id="test_detach_session_01",
            resume=False,
            immediate_worktree=False,
            fast_start=True,
            mock=True,
            reviewer="operator",
        )

        with patch.object(self.adapter, "start_recording_with_receipt") as mock_adapter_call:
            mock_status = MagicMock()
            mock_status.session_id = "test_detach_session_01"
            mock_status.recording_state = RecordingState.STARTING
            mock_receipt = ImmediateStartReceipt(
                status="PASS",
                session_id="test_detach_session_01",
                request_id="req_test",
                recording_state=RecordingState.STARTING.value,
                dispatched_at_iso="2026-10-07T12:00:00Z",
                pre_dispatch_latency_ms=12.5,
                master_wav_path=None,
                is_cold_start=False,
                message="Dispatched",
            )
            mock_adapter_call.return_value = (mock_status, mock_receipt)

            with patch("dual_pipeline.live_meeting_recorder_core.recording_service_adapter.get_recording_service_adapter", return_value=self.adapter):
                t0 = time.perf_counter()
                res = _handle_record_meeting(args)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0

                self.assertLess(elapsed_ms, 250.0)
                self.assertEqual(res["status"], "PASS")
                self.assertEqual(res["session_id"], "test_detach_session_01")
                self.assertIn("immediate_start_receipt", res)
                self.assertEqual(res["immediate_start_receipt"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
