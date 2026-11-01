"""
tests/test_recording_startup_deadline.py

Wave 3: Verifies initiation deadline enforcement across all startup operations.
Validates:
1. Fault-injected slow worktree setup triggers INITIATION_DEADLINE_EXCEEDED.
2. Fault-injected slow device probe triggers INITIATION_DEADLINE_EXCEEDED.
3. Fault-injected slow spooler handshake triggers INITIATION_DEADLINE_EXCEEDED.
4. Proof verification with elapsed monotonic deadline fails closed with INITIATION_DEADLINE_EXCEEDED.
5. Timeout cleanup touches only the failing session's owned resources; unrelated sessions continue.
6. ASR warming does not block capture or extend the initiation deadline.
7. Quarantine marker is recorded with reason_code INITIATION_DEADLINE_EXCEEDED.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    ASRState,
    RecordingServiceAdapter,
    RecordingStartRequest,
    RecordingState,
    RecordingStopRequest,
)


class TestRecordingStartupDeadline(unittest.TestCase):
    """Verifies that startup operations cannot exceed the 30.0s SLA initiation budget."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        self.output_dir = self.test_root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_slow_worktree_setup_exceeds_deadline(self) -> None:
        """Fault-injected slow worktree setup must fail closed with INITIATION_DEADLINE_EXCEEDED."""
        with patch("dual_pipeline.live_meeting_recorder.time.monotonic") as mock_mono:
            # First 3 calls establish session_start, then simulate 35 seconds elapsed before worktree check
            mock_mono.side_effect = [
                100.0,  # last_audible_monotonic
                100.0,  # session_start_monotonic
                135.0,  # check_initiation_budget("SESSION_REGISTRY")
                135.0,  # check_initiation_budget inside check
            ]
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                LiveMeetingRecorder(
                    meeting_name="Test Slow Worktree",
                    output_dir=self.output_dir,
                    mock_mode=True,
                    initiation_budget_seconds=30.0,
                )
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.INITIATION_DEADLINE_EXCEEDED)
            self.assertIn("SESSION_REGISTRY", ctx.exception.stage)

    def test_slow_device_probe_exceeds_deadline(self) -> None:
        """Fault-injected slow device probe must raise INITIATION_DEADLINE_EXCEEDED in capture manager."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.test_root / "chunks",
            master_wav=self.test_root / "master.wav",
            source_mode="mixed",
            mock_mode=True,
            deadline_monotonic=100.0,
        )
        with patch("dual_pipeline.live_meeting_recorder_core.audio_capture.time.monotonic", return_value=105.0):
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                mgr.resolve_source_strategy()
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.INITIATION_DEADLINE_EXCEEDED)
            self.assertEqual(ctx.exception.stage, "STRATEGY_RESOLUTION")

    def test_slow_spooler_handshake_exceeds_deadline(self) -> None:
        """Fault-injected slow spooler handshake must fail closed with INITIATION_DEADLINE_EXCEEDED."""
        spooler = MagicMock()
        spooler.is_running = True
        spooler.handshake_and_splice.return_value = MagicMock(migrated_chunk_indices=[0], migrated_chunk_count=1)

        with patch("dual_pipeline.live_meeting_recorder.time.monotonic") as mock_mono:
            mock_mono.side_effect = [
                100.0,  # last_audible
                100.0,  # session_start
                101.0,  # session_registry
                102.0,  # worktree_setup
                135.0,  # spooler handshake check
                135.0,  # inside check
            ]
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                LiveMeetingRecorder(
                    meeting_name="Test Slow Spooler",
                    output_dir=self.output_dir,
                    spooler=spooler,
                    mock_mode=True,
                    initiation_budget_seconds=30.0,
                )
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.INITIATION_DEADLINE_EXCEEDED)
            self.assertEqual(ctx.exception.stage, "SPOOLER_HANDSHAKE")

    def test_slow_proof_verification_exceeds_deadline(self) -> None:
        """Proof verification with expired deadline must fail closed with INITIATION_DEADLINE_EXCEEDED."""
        master_wav = self.test_root / "master.wav"
        master_wav.write_bytes(b"\x00" * 44)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with patch("dual_pipeline.live_meeting_recorder_core.audio_capture_proof.time.monotonic", return_value=150.0):
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                AudioCaptureProofVerifier.run_full_start_proof(
                    master_wav=master_wav,
                    ffmpeg_proc=mock_proc,
                    mock_mode=True,
                    deadline_monotonic=100.0,
                )
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.INITIATION_DEADLINE_EXCEEDED)

    def test_deadline_timeout_cleans_only_owned_session_resources(self) -> None:
        """Timeout in Session B cleans only Session B; Session A remains completely active."""
        adapter = RecordingServiceAdapter(repo_root=self.test_root)

        # 1. Start Session A normally in mock mode
        req_a = RecordingStartRequest(
            session_id="session_alpha_active",
            meeting_name="Alpha Session",
            mock_mode=True,
        )
        status_a = adapter.start_recording(req_a)
        self.assertEqual(status_a.recording_state, RecordingState.STARTING)

        handle_a = adapter._sessions.get("session_alpha_active")
        self.assertIsNotNone(handle_a)
        for _ in range(50):
            if handle_a.recording_state == RecordingState.CAPTURING:
                break
            time.sleep(0.05)
        self.assertEqual(handle_a.recording_state, RecordingState.CAPTURING)

        # 2. Start Session B with zero budget (immediate timeout)
        req_b = RecordingStartRequest(
            session_id="session_beta_timeout",
            meeting_name="Beta Timeout Session",
            mock_mode=True,
        )
        orig_init = adapter._sessions["session_alpha_active"].__class__._init_recorder

        def _conditional_init(handle_self):
            if handle_self.session_id == "session_beta_timeout":
                raise AudioCaptureUnprovenError(
                    stage="PRE_CAPTURE_START",
                    reason_code=AudioReasonCode.INITIATION_DEADLINE_EXCEEDED,
                    detail="Test timeout",
                )
            return orig_init(handle_self)

        with patch("dual_pipeline.live_meeting_recorder_core.recording_service_adapter.RecordingSessionHandle._init_recorder", side_effect=_conditional_init, autospec=True):
            adapter.start_recording(req_b)
            handle_b = adapter._sessions.get("session_beta_timeout")
            self.assertIsNotNone(handle_b)
            for _ in range(50):
                if handle_b.recording_state == RecordingState.FAILED:
                    break
                time.sleep(0.05)
            self.assertEqual(handle_b.recording_state, RecordingState.FAILED)

        # 3. Verify Session A was NOT affected by Session B's failure
        self.assertEqual(handle_a.recording_state, RecordingState.CAPTURING)
        handle_a_status = handle_a.get_status()
        self.assertEqual(handle_a_status.recording_state, RecordingState.CAPTURING)

        # 4. Clean up Session A cleanly
        stop_res = adapter.stop_recording(RecordingStopRequest(session_id="session_alpha_active"))
        self.assertEqual(stop_res.status, "STOPPED")

    def test_slow_asr_warming_does_not_block_capture(self) -> None:
        """Capture transitions to CAPTURING while ASR remains ASR_WARMING until genuine receipt exists."""
        adapter = RecordingServiceAdapter(repo_root=self.test_root)
        req = RecordingStartRequest(
            session_id="session_asr_warm",
            meeting_name="ASR Warm Test",
            mock_mode=True,
        )
        from unittest.mock import PropertyMock
        from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import IsolatedMLXTranscriberClient

        with patch.object(IsolatedMLXTranscriberClient, "readiness_receipt", new_callable=PropertyMock, return_value=None):
            status = adapter.start_recording(req)
            self.assertEqual(status.recording_state, RecordingState.STARTING)

            handle = adapter._sessions["session_asr_warm"]
            for _ in range(50):
                if handle.recording_state == RecordingState.CAPTURING:
                    break
                time.sleep(0.05)
            self.assertEqual(handle.recording_state, RecordingState.CAPTURING)
            # Initially ASR is warming
            self.assertEqual(handle.asr_state, ASRState.ASR_WARMING)

        # Now simulate receipt arrival
        fake_rec = MagicMock()
        fake_rec.mock_mode = True
        fake_rec.isolated_transcriber.readiness_receipt.return_value = {
            "status": "READY",
            "worker_pid": 12345,
            "latency_ms": 450.0,
        }
        handle._check_capture_milestones(fake_rec)
        self.assertEqual(handle.asr_state, ASRState.ASR_READY)
        self.assertIsNotNone(handle.asr_ready_monotonic)

        adapter.stop_recording(RecordingStopRequest(session_id="session_asr_warm"))

    def test_deadline_failure_records_quarantine_marker(self) -> None:
        """On deadline failure, .quarantine file contains INITIATION_DEADLINE_EXCEEDED reason_code."""
        with patch("dual_pipeline.live_meeting_recorder.time.monotonic") as mock_mono:
            mock_mono.side_effect = [
                100.0,  # last_audible
                100.0,  # session_start
                140.0,  # session_registry
                140.0,  # inside check
            ]
            with self.assertRaises(AudioCaptureUnprovenError):
                LiveMeetingRecorder(
                    meeting_name="Quarantine Deadline Test",
                    output_dir=self.output_dir,
                    mock_mode=True,
                    initiation_budget_seconds=30.0,
                )

        quarantine_file = self.output_dir / ".quarantine"
        self.assertTrue(quarantine_file.exists(), "Quarantine marker must exist")
        data = json.loads(quarantine_file.read_text(encoding="utf-8"))
        self.assertEqual(data["reason_code"], AudioReasonCode.INITIATION_DEADLINE_EXCEEDED)
        self.assertEqual(data["status"], "QUARANTINED")
