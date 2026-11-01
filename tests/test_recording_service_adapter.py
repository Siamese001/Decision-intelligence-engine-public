"""
tests/test_recording_service_adapter.py

Unit test suite for Wave 3: RecordingServiceAdapter.
Verifies:
1. Fast dispatch (< 250ms) of background capture before model reasoning.
2. Idempotent request matching via idempotency_key and host_chat_id.
3. Decoupled, independent capture, ASR, and diarization state tracking.
4. Real-time status querying and telemetry inspection.
5. Clean, session-bound stop execution.
"""
from __future__ import annotations

import tempfile
import time
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


class TestRecordingServiceAdapter(unittest.TestCase):
    """Unit test suite for RecordingServiceAdapter."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.adapter = RecordingServiceAdapter(repo_root=self.repo_root)

    def tearDown(self) -> None:
        # Halt any active sessions
        for status in self.adapter.list_active_sessions():
            self.adapter.stop_recording(RecordingStopRequest(session_id=status.session_id))
        self.temp_dir.cleanup()

    def test_start_dispatch_latency_under_250ms(self) -> None:
        """Verifies start_recording dispatches in < 250ms without waiting for heavy engines."""
        req = RecordingStartRequest(
            project_name="proj_speed",
            meeting_name="Fast Dispatch Test",
            mock_mode=True,
            segment_seconds=5,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec.output_dir = self.repo_root / "out"
            mock_rec.chunks_dir = self.repo_root / "chunks"
            mock_rec.master_wav = self.repo_root / "master.wav"
            mock_rec_cls.return_value = mock_rec

            t0 = time.perf_counter()
            resp = self.adapter.start_recording(req)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.assertLess(elapsed_ms, 250.0, f"Start dispatch took {elapsed_ms:.2f}ms (target: < 250ms)")
            self.assertIsNotNone(resp.session_id)
            self.assertIn(resp.recording_state, (RecordingState.REQUESTED, RecordingState.STARTING, RecordingState.CAPTURING))

    def test_idempotent_matching_via_idempotency_key(self) -> None:
        """Verifies repeated requests with the same idempotency_key attach to the existing session."""
        key = "idemp_key_12345"
        req1 = RecordingStartRequest(
            idempotency_key=key,
            project_name="proj_idemp",
            meeting_name="Idempotent Session",
            mock_mode=True,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec_cls.return_value = mock_rec

            resp1 = self.adapter.start_recording(req1)
            # Second call with the same idempotency key
            req2 = RecordingStartRequest(
                idempotency_key=key,
                project_name="proj_idemp",
                meeting_name="Idempotent Session",
                mock_mode=True,
            )
            resp2 = self.adapter.start_recording(req2)

            self.assertEqual(resp1.session_id, resp2.session_id)
            self.assertEqual(len(self.adapter.list_active_sessions()), 1)

    def test_idempotent_matching_via_host_chat_binding(self) -> None:
        """Verifies repeated requests in the same chat turn for the same meeting attach to the same session."""
        chat_id = "chat_conv_abc999"
        req1 = RecordingStartRequest(
            host_chat_id=chat_id,
            project_name="proj_chat",
            meeting_name="Standup Meeting",
            mock_mode=True,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec_cls.return_value = mock_rec

            resp1 = self.adapter.start_recording(req1)
            # Subsequent turn in same chat
            req2 = RecordingStartRequest(
                host_chat_id=chat_id,
                project_name="proj_chat",
                meeting_name="Standup Meeting",
                mock_mode=True,
            )
            resp2 = self.adapter.start_recording(req2)

            self.assertEqual(resp1.session_id, resp2.session_id)

    def test_independent_capture_and_asr_states(self) -> None:
        """Verifies capture state, ASR state, and diarization state are decoupled and report independently."""
        req = RecordingStartRequest(
            project_name="proj_states",
            meeting_name="Decoupled States Test",
            mock_mode=False,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec._is_stopped = False
            mock_rec_cls.return_value = mock_rec

            resp = self.adapter.start_recording(req)

            # Non-mock mode starts with ASR_WARMING and DIARIZATION_WARMING
            self.assertEqual(resp.asr_state, ASRState.ASR_WARMING)
            self.assertEqual(resp.diarization_state, DiarizationState.DIARIZATION_WARMING)

    def test_status_inspection_and_clean_stop(self) -> None:
        """Verifies get_status returns accurate metrics and stop_recording halts cleanly."""
        req = RecordingStartRequest(
            project_name="proj_lifecycle",
            meeting_name="Status and Stop Test",
            mock_mode=True,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.running = True
            mock_rec.processed_chunks = {"chunk_00000.wav", "chunk_00001.wav"}
            mock_rec.master_wav = self.repo_root / "master.wav"
            mock_rec.transcript_md = self.repo_root / "transcript.md"
            mock_rec_cls.return_value = mock_rec

            start_resp = self.adapter.start_recording(req)
            sid = start_resp.session_id

            # Poll status
            status = self.adapter.get_status(sid)
            self.assertIsNotNone(status)
            self.assertEqual(status.session_id, sid)
            self.assertGreaterEqual(status.elapsed_seconds, 0.0)

            # Stop session
            stop_resp = self.adapter.stop_recording(RecordingStopRequest(session_id=sid))
            self.assertEqual(stop_resp.status, "STOPPED")
            self.assertEqual(stop_resp.session_id, sid)

            # Post-stop status
            post_status = self.adapter.get_status(sid)
            self.assertEqual(post_status.recording_state, RecordingState.STOPPED)


if __name__ == "__main__":
    unittest.main()
