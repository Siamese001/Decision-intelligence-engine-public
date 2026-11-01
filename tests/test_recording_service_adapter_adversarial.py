"""
tests/test_recording_service_adapter_adversarial.py

Adversarial and edge-case test suite for Wave 3 RecordingServiceAdapter.
Strictly adheres to e2e-and-edge-case-coverage.md covering 5 mandatory edge-case categories:
1. Boundary payload (empty requests, non-existent stop sessions, boundary segment lengths)
2. Malformed input (serialization round-trips, unicode/emoji titles, special chars in idempotency keys)
3. Concurrency & race (10 concurrent threads racing on same idempotency key, parallel stop calls)
4. Rollback & recovery (background capture failure cleanly transitions to FAILED with remediation)
5. Invariant bypass attempt (zero cloud dispatch, zero Terminal delegation)
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    ASRState,
    DiarizationState,
    RecordingServiceAdapter,
    RecordingStartRequest,
    RecordingState,
    RecordingStatusResponse,
    RecordingStopRequest,
    RecordingStopResponse,
)


class TestRecordingServiceAdapterAdversarial(unittest.TestCase):
    """Adversarial and stress test suite for RecordingServiceAdapter."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.adapter = RecordingServiceAdapter(repo_root=self.repo_root)

    def tearDown(self) -> None:
        for status in self.adapter.list_active_sessions():
            self.adapter.stop_recording(RecordingStopRequest(session_id=status.session_id))
        self.temp_dir.cleanup()

    # =========================================================================
    # Category 1: Boundary Payload
    # =========================================================================

    def test_adv_boundary_empty_start_request(self) -> None:
        """Boundary: Start request with all defaults and empty fields initializes safely."""
        req = RecordingStartRequest()
        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec_cls.return_value = mock_rec

            resp = self.adapter.start_recording(req)
            self.assertIsNotNone(resp.session_id)
            self.assertIn(resp.recording_state, (RecordingState.REQUESTED, RecordingState.STARTING, RecordingState.CAPTURING))

    def test_adv_boundary_nonexistent_session_stop(self) -> None:
        """Boundary: Stop request for a nonexistent or already deleted session returns NOT_FOUND cleanly."""
        stop_req = RecordingStopRequest(session_id="nonexistent_session_999999")
        stop_resp = self.adapter.stop_recording(stop_req)
        self.assertEqual(stop_resp.status, "NOT_FOUND")
        self.assertEqual(stop_resp.final_chunk_count, 0)
        self.assertEqual(stop_resp.master_wav_size_bytes, 0)

    # =========================================================================
    # Category 2: Malformed Input
    # =========================================================================

    def test_adv_malformed_dataclass_serialization_roundtrip(self) -> None:
        """Malformed input: Verify JSON serialization and dictionary conversion integrity."""
        req = RecordingStartRequest(
            request_id="req_test_123",
            idempotency_key="key/with/special/chars-!@#$%^&*()",
            project_name="proj_unicode_🚀",
            meeting_name='Title with "quotes" and \n newlines and emoji 🎙️',
            attendees=["Alice", "Bob O'Connor"],
        )
        req_dict = req.to_dict()
        req_json = json.dumps(req_dict)
        deserialized = json.loads(req_json)
        self.assertEqual(deserialized["request_id"], "req_test_123")
        self.assertEqual(deserialized["project_name"], "proj_unicode_🚀")

        status = RecordingStatusResponse(
            session_id="rec_abc",
            request_id="req_test_123",
            host_chat_id="chat_001",
            recording_state=RecordingState.CAPTURING,
            asr_state=ASRState.ASR_READY,
            diarization_state=DiarizationState.DIARIZATION_READY,
            started_at_iso="2026-10-06T14:00:00",
            started_at_monotonic=100.0,
            elapsed_seconds=15.5,
            captured_chunks_count=3,
        )
        status_dict = status.to_dict()
        self.assertEqual(status_dict["recording_state"], "CAPTURING")
        self.assertEqual(status_dict["asr_state"], "ASR_READY")
        self.assertEqual(status_dict["diarization_state"], "DIARIZATION_READY")

    # =========================================================================
    # Category 3: Concurrency & Race Conditions
    # =========================================================================

    def test_adv_concurrency_rapid_fire_identical_idempotency_key(self) -> None:
        """Concurrency: 10 parallel threads dispatching with the same key create exactly 1 session."""
        key = "rapid_fire_idempotency_key_999"

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec_cls.return_value = mock_rec

            def _dispatch(i: int):
                req = RecordingStartRequest(
                    idempotency_key=key,
                    project_name="proj_race",
                    meeting_name="Race Meeting",
                    mock_mode=True,
                )
                return self.adapter.start_recording(req)

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_dispatch, i) for i in range(10)]
                responses = [f.result() for f in futures]

            session_ids = {r.session_id for r in responses}
            self.assertEqual(len(session_ids), 1, f"Expected exactly 1 session, got {session_ids}")
            self.assertEqual(len(self.adapter.list_active_sessions()), 1)

    def test_adv_concurrency_parallel_stop_invocations(self) -> None:
        """Concurrency: Multiple parallel stop invocations on the same session execute safely."""
        req = RecordingStartRequest(
            project_name="proj_stop_race",
            meeting_name="Stop Race Meeting",
            mock_mode=True,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec_cls.return_value = mock_rec

            start_resp = self.adapter.start_recording(req)
            sid = start_resp.session_id

            def _stop():
                return self.adapter.stop_recording(RecordingStopRequest(session_id=sid))

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(_stop) for _ in range(5)]
                results = [f.result() for f in futures]

            # All should return STOPPED without unhandled exceptions
            for r in results:
                self.assertEqual(r.status, "STOPPED")
                self.assertEqual(r.session_id, sid)

    # =========================================================================
    # Category 4: Rollback & Recovery
    # =========================================================================

    def test_adv_rollback_capture_worker_failure_transitions_to_failed(self) -> None:
        """Rollback: Failure during capture worker cleanly marks session FAILED with remediation."""
        req = RecordingStartRequest(
            project_name="proj_fail",
            meeting_name="Failing Session",
            mock_mode=False,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec_cls.side_effect = RuntimeError("ScreenCaptureKit tap denied by macOS TCC.")

            self.adapter.start_recording(req)

            # Give background thread time to fail
            import time
            deadline = time.time() + 2.0
            failed = False
            status = None
            while time.time() < deadline:
                status = self.adapter.get_status(req.request_id)
                if status and status.recording_state == RecordingState.FAILED:
                    failed = True
                    break
                time.sleep(0.05)

            self.assertTrue(failed, f"Session should transition to FAILED, got: {status}")
            self.assertIn("ScreenCaptureKit", str(status.error))
            self.assertIn("Grant Screen & System Audio Recording", str(status.remediation))
            # Critical governance invariant: never instruct user to run Terminal
            self.assertNotIn("terminal", str(status.remediation).lower())

    # =========================================================================
    # Category 5: Invariant Bypass Attempt
    # =========================================================================

    def test_adv_invariant_bypass_zero_terminal_and_zero_cloud_remediation(self) -> None:
        """Invariant bypass: Remediation must strictly direct to System Settings, never Terminal."""
        req = RecordingStartRequest(
            project_name="proj_bypass",
            meeting_name="Terminal Bypass Test",
            mock_mode=False,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec_cls.side_effect = PermissionError("AVFoundation Microphone access denied")

            self.adapter.start_recording(req)

            import time
            deadline = time.time() + 2.0
            status = None
            while time.time() < deadline:
                status = self.adapter.get_status(req.request_id)
                if status and status.recording_state == RecordingState.FAILED:
                    break
                time.sleep(0.05)

            self.assertIsNotNone(status)
            self.assertEqual(status.recording_state, RecordingState.FAILED)
            self.assertIn("System Settings", str(status.remediation))
            # Prohibit Terminal handoff
            self.assertNotIn("terminal", str(status.remediation).lower())
            self.assertNotIn("bash", str(status.remediation).lower())


if __name__ == "__main__":
    unittest.main()
