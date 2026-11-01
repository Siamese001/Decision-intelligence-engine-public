"""
tests/test_recording_owner_lifecycle.py

Wave 2: Unit and integration tests for RecordingServiceOwner, session-scoped
process lifecycle, exclusive file locking, concurrent start resolution, and graceful stop.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    RecordingStartRequest,
    RecordingState,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_owner import (
    RecordingServiceOwner,
    SessionOwnerRecord,
    get_recording_service_owner,
)


class TestRecordingOwnerLifecycle(unittest.TestCase):
    """Verifies persistent session owner lifecycle, lock exclusivity, and graceful recovery."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sessions_root = Path(self.temp_dir.name) / "sessions"
        self.owner = RecordingServiceOwner(sessions_root=self.sessions_root)

    def tearDown(self) -> None:
        # Gracefully stop any active sessions created during test
        for s_dir in self.sessions_root.glob("*"):
            if s_dir.is_dir():
                self.owner.stop_session(s_dir.name)
        self.temp_dir.cleanup()

    def test_exclusive_ownership_acquisition(self) -> None:
        """Verifies initial acquisition succeeds and assigns owner lock and state files."""
        session_id = "test_owner_exclusive_01"
        req = RecordingStartRequest(
            project_name="proj_lifecycle",
            meeting_name="Owner Exclusivity Test",
            mock_mode=True,
        )

        record, is_new = self.owner.acquire_session_owner(req, session_id)

        self.assertTrue(is_new)
        self.assertEqual(record.session_id, session_id)
        self.assertEqual(record.pid, os.getpid())
        self.assertEqual(record.status, RecordingState.STARTING.value)
        self.assertTrue(Path(record.lock_file).is_file())
        self.assertTrue(Path(record.state_file).is_file())

    def test_concurrent_acquisition_resolves_to_existing_owner(self) -> None:
        """Verifies duplicate acquisition for an active session returns the existing owner."""
        session_id = "test_owner_concurrent_01"
        req1 = RecordingStartRequest(
            request_id="req_initial",
            session_id=session_id,
            meeting_name="First Owner",
            mock_mode=True,
        )

        record1, is_new1 = self.owner.acquire_session_owner(req1, session_id)
        self.assertTrue(is_new1)

        req2 = RecordingStartRequest(
            request_id="req_second",
            session_id=session_id,
            meeting_name="Concurrent Candidate",
            mock_mode=True,
        )

        record2, is_new2 = self.owner.acquire_session_owner(req2, session_id)
        self.assertFalse(is_new2, "Second acquisition MUST NOT claim new ownership")
        self.assertEqual(record1.pid, record2.pid)
        self.assertEqual(record1.session_id, record2.session_id)

    def test_spawn_session_process_decoupled_from_caller(self) -> None:
        """Verifies background daemon process spawns, writes state, and persists independently."""
        session_id = "test_spawn_daemon_01"
        req = RecordingStartRequest(
            project_name="proj_daemon",
            meeting_name="Daemon Persistence Test",
            mock_mode=True,
        )

        record = self.owner.spawn_session_process(req, session_id)

        self.assertNotEqual(record.pid, os.getpid(), "Spawned process must have unique PID")
        self.assertTrue(self.owner._is_pid_alive(record.pid), "Spawned process must be running")

        # Session state readback
        stored = self.owner.get_session_record(session_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.pid, record.pid)

        # Stop session gracefully and verify process terminates cleanly
        res = self.owner.stop_session(session_id)
        self.assertEqual(res["status"], "STOPPED")
        self.assertFalse(self.owner._is_pid_alive(record.pid), "Process must be terminated after stop")

    def test_stop_session_is_session_scoped_without_broad_kill(self) -> None:
        """Verifies stopping a session affects only the target session and never collateral processes."""
        session1 = "test_scope_session_1"
        session2 = "test_scope_session_2"

        req1 = RecordingStartRequest(project_name="p1", meeting_name="M1", mock_mode=True)
        req2 = RecordingStartRequest(project_name="p2", meeting_name="M2", mock_mode=True)

        rec1 = self.owner.spawn_session_process(req1, session1)
        rec2 = self.owner.spawn_session_process(req2, session2)

        self.assertTrue(self.owner._is_pid_alive(rec1.pid))
        self.assertTrue(self.owner._is_pid_alive(rec2.pid))
        self.assertNotEqual(rec1.pid, rec2.pid)

        # Stop only session 1
        stop_res1 = self.owner.stop_session(session1)
        self.assertEqual(stop_res1["status"], "STOPPED")

        # Session 1 must be dead, Session 2 must remain alive
        self.assertFalse(self.owner._is_pid_alive(rec1.pid))
        self.assertTrue(self.owner._is_pid_alive(rec2.pid), "Session 2 must NOT be impacted by session 1 stop")

        # Cleanup session 2
        self.owner.stop_session(session2)
        self.assertFalse(self.owner._is_pid_alive(rec2.pid))

    def test_recovery_from_dead_pid(self) -> None:
        """Verifies that if an owner process exits abruptly, get_session_record detects it as STOPPED."""
        session_id = "test_dead_pid_recovery"
        s_dir = self.sessions_root / session_id
        s_dir.mkdir(parents=True, exist_ok=True)
        state_file = s_dir / "session_state.json"

        # Write state for a non-existent PID (e.g. 99999999)
        dead_pid = 99999999
        import json
        state_data = {
            "session_id": session_id,
            "request_id": "req_dead",
            "pid": dead_pid,
            "started_at_iso": "2026-10-07T12:00:00Z",
            "started_at_monotonic": 100.0,
            "status": RecordingState.CAPTURING.value,
            "lock_file": str(s_dir / "owner.lock"),
            "state_file": str(state_file),
            "is_mock": True,
        }
        state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")

        record = self.owner.get_session_record(session_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, RecordingState.STOPPED.value, "Dead PID must be transitioned to STOPPED")


if __name__ == "__main__":
    unittest.main()
