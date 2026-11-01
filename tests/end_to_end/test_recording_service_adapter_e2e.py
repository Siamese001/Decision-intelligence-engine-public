"""
tests/end_to_end/test_recording_service_adapter_e2e.py

End-to-End integration test suite for Wave 3: RecordingServiceAdapter.
Verifies:
1. Multi-turn session survival: capture survives caller turn return, model context switches, and subsequent turns.
2. Idempotent re-entry: repeated start invocations attach seamlessly to the same running session.
3. Fast dispatch SLA: p95 dispatch latency is strictly under 250ms.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    RecordingServiceAdapter,
    RecordingSessionHandle,
    RecordingStartRequest,
    RecordingState,
    RecordingStopRequest,
)


@pytest.mark.e2e
class TestRecordingServiceAdapterE2E(unittest.TestCase):
    """E2E suite verifying background capture lifecycle across simulated chat turns."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.adapter = RecordingServiceAdapter(repo_root=self.repo_root)

    def tearDown(self) -> None:
        for status in self.adapter.list_active_sessions():
            self.adapter.stop_recording(RecordingStopRequest(session_id=status.session_id))
        self.temp_dir.cleanup()

    def test_e2e_multi_turn_session_survival_and_model_switching(self) -> None:
        """E2E: Capture survives across simulated chat turns and model switches."""
        chat_id = "chat_turn_lifecycle_001"

        mock_rec = MagicMock()
        mock_rec.running = True
        mock_rec._is_stopped = False
        mock_rec.processed_chunks = {"chunk_00000.wav"}
        mock_rec.output_dir = self.repo_root / "out"
        mock_rec.chunks_dir = self.repo_root / "chunks"
        mock_rec.master_wav = self.repo_root / "master.wav"

        with patch.object(RecordingSessionHandle, "_init_recorder", return_value=mock_rec):

            # Turn 1: "Start recording" (Model: Gemini Pro)
            turn1_req = RecordingStartRequest(
                host_chat_id=chat_id,
                project_name="proj_e2e",
                meeting_name="Multi-Turn Executive Sync",
                attendees=["Alice", "Bob"],
                mock_mode=True,
            )
            turn1_resp = self.adapter.start_recording(turn1_req)
            sid = turn1_resp.session_id
            self.assertIsNotNone(sid)

            # Turn 2: User asks follow-up, model switched to Flash (no tool call to restart)
            # System inspects status
            turn2_status = self.adapter.get_status(sid)
            self.assertIsNotNone(turn2_status)
            self.assertEqual(turn2_status.session_id, sid)
            self.assertIn(turn2_status.recording_state, (RecordingState.STARTING, RecordingState.CAPTURING))

            # Turn 3: "Stop recording"
            turn3_stop = self.adapter.stop_recording(RecordingStopRequest(session_id=sid, host_chat_id=chat_id))
            self.assertEqual(turn3_stop.status, "STOPPED")
            self.assertEqual(turn3_stop.session_id, sid)

            # Turn 4: Verify session is cleanly stopped
            turn4_status = self.adapter.get_status(sid)
            self.assertEqual(turn4_status.recording_state, RecordingState.STOPPED)

    def test_e2e_idempotent_reentry_attaches_to_active_session(self) -> None:
        """E2E: Retry of start command attaches directly to existing background capture."""
        chat_id = "chat_retry_002"

        mock_rec = MagicMock()
        mock_rec.running = True
        mock_rec._is_stopped = False

        with patch.object(RecordingSessionHandle, "_init_recorder", return_value=mock_rec):

            # Initial start
            req1 = RecordingStartRequest(
                host_chat_id=chat_id,
                project_name="proj_retry",
                meeting_name="Retry Test",
                mock_mode=True,
            )
            resp1 = self.adapter.start_recording(req1)

            # Accidental retry by user or agent
            req2 = RecordingStartRequest(
                host_chat_id=chat_id,
                project_name="proj_retry",
                meeting_name="Retry Test",
                mock_mode=True,
            )
            resp2 = self.adapter.start_recording(req2)

            self.assertEqual(resp1.session_id, resp2.session_id, "Retry must attach to the identical active session.")
            self.assertEqual(len(self.adapter.list_active_sessions()), 1)

    def test_e2e_dispatch_latency_p95_under_250ms(self) -> None:
        """E2E: 20 successive starts measure dispatch latency satisfying the <= 250ms p95 gate."""
        mock_rec = MagicMock()
        mock_rec.running = True
        mock_rec._is_stopped = False

        with patch.object(RecordingSessionHandle, "_init_recorder", return_value=mock_rec):

            latencies = []
            for i in range(20):
                req = RecordingStartRequest(
                    project_name=f"proj_latency_{i}",
                    meeting_name=f"Latency Probe {i}",
                    mock_mode=True,
                )
                t0 = time.perf_counter()
                self.adapter.start_recording(req)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                latencies.append(elapsed_ms)

            latencies.sort()
            p95_idx = int(len(latencies) * 0.95)
            p95_latency = latencies[p95_idx]

            self.assertLess(
                p95_latency,
                250.0,
                f"p95 dispatch latency {p95_latency:.2f}ms exceeds 250ms gate limit.",
            )


if __name__ == "__main__":
    unittest.main()
