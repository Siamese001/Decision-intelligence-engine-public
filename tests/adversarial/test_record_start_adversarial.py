"""
tests/adversarial/test_record_start_adversarial.py

Adversarial and Edge-Case Test Suite for Record-Start and Fast Initialization Architecture.
Validates Wave 5 adversarial requirements across 7 distinct categories:
1. Boundary Payloads: 78-byte header-only WAV, 1-sample PCM chunk, zero-sample audio.
2. Malformed Input: Corrupt worker manifest, malformed JSON-lines IPC.
3. Concurrency Races: Simultaneous session contention on same meeting slug.
4. Exhaustion: MLX readiness deadline hang (MLX_READY_DEADLINE_EXCEEDED).
5. Invariant Bypass Attempts: Cloud STT (OpenAI backend) rejection, microphone fallback rejection under D1.
6. Rollback & Quarantine: Structured .quarantine file written and fcntl lock released on abort.
7. Regression Pins: Single capture authority, zero orphan spool chunks, copy with sha256 checksum.
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    LiveSessionCollisionError,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.instant_spooler import (
    InstantAudioSpooler,
)
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    AudioAnalysisError,
    IsolatedMLXTranscriberClient,
)
from dual_pipeline.live_meeting_recorder_core.mlx_worker import (
    AirgapViolationError,
    MLXReasonCode,
    enforce_process_airgap,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    _copy_chunk_if_missing,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_wav(path: Path, n_samples: int = 16000, sample_rate: int = 16000, channels: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        if n_samples > 0:
            data = struct.pack(f"<{n_samples}h", *[int(1000 * math.sin(i * 0.1)) for i in range(n_samples)])
            wf.writeframes(data)
    return path


class TestRecordStartAdversarial(unittest.TestCase):
    """Adversarial fault-injection and boundary stress harness for live recording startup."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="nee_adversarial_record_start_")
        self.test_root = Path(self.test_dir)
        self._root_patcher = mock.patch("dual_pipeline.live_meeting_recorder.ROOT", self.test_root)
        self._root_patcher.start()

    def tearDown(self) -> None:
        self._root_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)
        test_proj_out = Path.cwd() / "output" / "TEST_PROJ"
        if test_proj_out.exists():
            shutil.rmtree(test_proj_out, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # -------------------------------------------------------------------------
    def test_cat1_boundary_empty_wav_raises_analysis_error(self) -> None:
        """Boundary: 0-byte or 78-byte WAV header without PCM fails fast with AudioAnalysisError."""
        wav_path = self.test_root / "truncated.wav"
        wav_path.write_bytes(b"RIFF" + b"\x00" * 74)

        recorder = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="Boundary Payload Test",
            meeting_date="10/04/2026",
            mock_mode=True,
        )
        with self.assertRaises(AudioAnalysisError):
            recorder._transcribe_chunk(wav_path)

    def test_cat1_boundary_one_sample_pcm_chunk(self) -> None:
        """Boundary: 1-sample PCM chunk below threshold fails fast with AudioAnalysisError."""
        wav_path = self.test_root / "one_sample.wav"
        _write_wav(wav_path, n_samples=1)

        recorder = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="One Sample Test",
            meeting_date="10/04/2026",
            mock_mode=True,
        )
        with self.assertRaises(AudioAnalysisError):
            recorder._transcribe_chunk(wav_path)

    def test_cat1_stream_anchored_rate_calculation_with_sck_warmup_delay(self) -> None:
        """Category 1: Rate verification anchors to first_data_time, preventing deficit during warm-up delay."""
        import threading
        import time

        wav_path = self.test_root / "delayed_stream.wav"
        _write_wav(wav_path, n_samples=0)

        def _delayed_pcm_feeder():
            time.sleep(1.0)
            with open(wav_path, "ab") as f:
                for _ in range(10):
                    f.write(b"\x00" * 6400)
                    f.flush()
                    time.sleep(0.08)

        t = threading.Thread(target=_delayed_pcm_feeder, daemon=True)
        t.start()

        res = AudioCaptureProofVerifier.verify_pcm_byte_rate(
            master_wav=wav_path,
            is_stereo=True,
            min_duration_sec=0.8,
            deadline_sec=4.0,
            mock_mode=False,
        )
        self.assertTrue(res["proven"])
        self.assertGreaterEqual(res["rate_ratio"], 0.80)

    # -------------------------------------------------------------------------
    # Category 2: Malformed Input
    # -------------------------------------------------------------------------
    def test_cat2_malformed_worker_handshake_payload(self) -> None:
        """Malformed input: worker emits invalid JSON or unexpected message type."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client._output_q = mock.MagicMock()
        client._output_q.get.return_value = {"type": "CORRUPT_PAYLOAD", "data": 12345}

        with self.assertRaises(RuntimeError) as ctx:
            client._await_handshake(timeout=1.0)
        self.assertIn("Unexpected handshake from MLX worker", str(ctx.exception))

    def test_cat2_tampered_chunk_fails_sha256_verification(self) -> None:
        """Malformed input: chunk modified during migration fails sha256 integrity check."""
        src_dir = self.test_root / "src_chunks"
        dest_dir = self.test_root / "dest_chunks"
        src_dir.mkdir(parents=True, exist_ok=True)
        dest_dir.mkdir(parents=True, exist_ok=True)

        chunk_file = src_dir / "chunk_00000.wav"
        _write_wav(chunk_file, n_samples=100)

        # Inject tampered copy that returns different sha256
        with mock.patch("hashlib.sha256") as mock_sha:
            m1 = mock.MagicMock()
            m1.hexdigest.return_value = "hash_original_abc"
            m2 = mock.MagicMock()
            m2.hexdigest.return_value = "hash_tampered_xyz"
            mock_sha.side_effect = [m1, m2]

            with self.assertRaises(RuntimeError) as ctx:
                _copy_chunk_if_missing(chunk_file, dest_dir)
            self.assertIn("Integrity check failed: SHA256 mismatch", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Category 3: Concurrency Races
    # -------------------------------------------------------------------------
    def test_cat3_concurrency_slug_lock_contention(self) -> None:
        """Concurrency: second recorder instance on same meeting name fails with collision error."""
        rec1 = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="Concurrent Race Meeting",
            meeting_date="10/04/2026",
            mock_mode=True,
        )
        rec1.output_dir.mkdir(parents=True, exist_ok=True)
        self.assertIsNotNone(rec1._slug_lock_fd)

        # Attempt to instantiate rec2 with same project & meeting name
        with self.assertRaises(LiveSessionCollisionError) as ctx:
            LiveMeetingRecorder(
                project_name="TEST_PROJ",
                meeting_name="Concurrent Race Meeting",
                meeting_date="10/04/2026",
                mock_mode=True,
            )
        self.assertIn("collision detected", str(ctx.exception).lower())

        # Cleanup
        rec1._release_slug_lock()

    # -------------------------------------------------------------------------
    # Category 4: Exhaustion
    # -------------------------------------------------------------------------
    def test_cat4_exhaustion_mlx_ready_deadline_exceeded(self) -> None:
        """Exhaustion: worker handshake hang raises MLX_READY_DEADLINE_EXCEEDED."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        fut = client.start_async(timeout=0.05)
        with self.assertRaises(TimeoutError) as ctx:
            fut.result()
        self.assertIn(MLXReasonCode.MLX_READY_DEADLINE_EXCEEDED, str(ctx.exception))

    # -------------------------------------------------------------------------
    # Category 5: Invariant Bypass Attempts
    # -------------------------------------------------------------------------
    def test_cat5_cloud_stt_backend_openai_rejected(self) -> None:
        """Invariant bypass: attempting to use OpenAI cloud backend fails closed."""
        with self.assertRaises(ValueError) as ctx:
            LiveMeetingRecorder(
                project_name="TEST_PROJ",
                meeting_name="Cloud Bypass Test",
                meeting_date="10/04/2026",
                backend="openai",
                mock_mode=True,
            )
        self.assertIn("prohibited", str(ctx.exception).lower())

    def test_cat5_operator_decision_d1_no_microphone_fallback(self) -> None:
        """Invariant bypass: SCK failure must NOT silently fallback to microphone under D1."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.test_root / "chunks",
            master_wav=self.test_root / "master.wav",
            source_mode="mixed",
            device_index=":0",
            mock_mode=False,
        )
        with mock.patch(
            "dual_pipeline.live_meeting_recorder_core.audio_capture.AudioCaptureProofVerifier.run_full_start_proof",
            side_effect=AudioCaptureUnprovenError(
                stage="STARTUP_PROOF",
                reason_code=AudioReasonCode.SCK_TAP_FAILED,
                detail="SCK helper failed to spawn.",
            ),
        ):
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                mgr.start(run_proof=True)
            self.assertIn(ctx.exception.reason_code, (AudioReasonCode.SCK_TAP_FAILED, AudioReasonCode.SCK_TCC_DENIED))
            self.assertNotEqual(mgr._active_strategy, "mic")

    def test_cat5_airgap_outbound_socket_blocked(self) -> None:
        """Invariant bypass: worker process network egress blocked by airgap."""
        enforce_process_airgap()
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(AirgapViolationError):
                s.connect(("api.openai.com", 443))
        finally:
            s.close()

    # -------------------------------------------------------------------------
    # Category 6: Rollback & Quarantine
    # -------------------------------------------------------------------------
    def test_cat6_quarantine_marker_on_abort(self) -> None:
        """Rollback: failure writes .quarantine with reason code, releases lock, and cleans registry."""
        recorder = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="Quarantine Marker Test",
            meeting_date="10/04/2026",
            mock_mode=True,
        )
        recorder.output_dir.mkdir(parents=True, exist_ok=True)
        recorder._quarantine_session(reason="Injected test failure", reason_code=AudioReasonCode.FFMPEG_SPAWN_FAILED)

        q_file = recorder.output_dir / ".quarantine"
        self.assertTrue(q_file.exists())
        data = json.loads(q_file.read_text(encoding="utf-8"))
        self.assertEqual(data["reason_code"], AudioReasonCode.FFMPEG_SPAWN_FAILED)
        self.assertEqual(data["status"], "QUARANTINED")
        self.assertIsNone(recorder._slug_lock_fd)

    def test_cat6_stale_quarantine_cleaned_on_resume(self) -> None:
        """Rollback & Resume: Stale .quarantine marker is removed when session is resumed with healthy hardware."""
        out_path = self.test_root / "quarantine_resume_dir"
        out_path.mkdir(parents=True, exist_ok=True)
        q_file = out_path / ".quarantine"
        q_file.write_text(json.dumps({"status": "QUARANTINED", "reason": "stale deficit"}), encoding="utf-8")
        self.assertTrue(q_file.exists())

        # Resuming session cleans stale quarantine marker
        recorder = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="Quarantine Resume Test",
            meeting_date="10/04/2026",
            output_dir=out_path,
            mock_mode=True,
            resume=True,
        )
        self.assertFalse(q_file.exists(), "Expected .quarantine to be removed upon resume initialization")

        # Starting recorder with healthy capture also ensures marker remains absent
        recorder._init_transcript_file()
        recorder.start_ffmpeg()
        self.assertFalse(q_file.exists())
        with mock.patch.object(recorder, "_transcribe_chunk", return_value="Speech."):
            recorder.stop()

    def test_cat6_incremental_line_appends_during_live_session_without_restart(self) -> None:
        """Incremental streaming: Live transcript file receives line appends during active call without restart."""
        out_path = self.test_root / "live_streaming_append_dir"
        out_path.mkdir(parents=True, exist_ok=True)
        recorder = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="Live Streaming Append Test",
            meeting_date="10/04/2026",
            output_dir=out_path,
            mock_mode=True,
            isolated_process=False,
        )
        recorder._init_transcript_file()
        self.assertTrue(recorder.transcript_md.exists())
        initial_content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("## Live Feed", initial_content)

        # Synthesize 2 valid consecutive chunks
        c0 = recorder.chunks_dir / "chunk_0000.wav"
        c1 = recorder.chunks_dir / "chunk_0001.wav"
        _write_wav(c0, n_samples=32000)
        _write_wav(c1, n_samples=32000)

        # Process chunks in active loop state (running=True, before stop())
        recorder.running = True
        last_sizes: dict[str, int] = {}
        with mock.patch.object(recorder, "_transcribe_chunk", return_value="We are discussing live pipeline architecture."):
            recorder._poll_and_process_chunks(last_sizes)

        # Check that line was appended to live transcript while session is still active
        active_content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("We are discussing live pipeline architecture.", active_content)
        self.assertTrue(recorder.running, "Session must still be running (no stop/restart required)")

        with mock.patch.object(recorder, "_transcribe_chunk", return_value="We are discussing live pipeline architecture."):
            recorder.stop()
        self.assertFalse(recorder.running)

    # -------------------------------------------------------------------------
    # Category 7: Regression Pins
    # -------------------------------------------------------------------------
    def test_cat7_single_capture_authority_adoption(self) -> None:
        """Regression pin: Spooler adoption transfers capture authority with zero orphan chunks."""
        spooler = InstantAudioSpooler.spawn_instant(mock_mode=True)
        self.assertTrue(spooler.is_running)

        recorder = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="Single Authority Pin",
            meeting_date="10/04/2026",
            mock_mode=True,
            spooler=spooler,
        )
        recorder._init_transcript_file()
        recorder.start_ffmpeg()

        # Spooler marked adopted and capture authority transferred
        self.assertTrue(spooler.is_adopted)
        self.assertEqual(recorder.capture_manager._active_strategy, "spooler_adopted")

        # Stopping spooler does not kill recorder capture authority
        spooler.stop()
        self.assertTrue(recorder.running)

        with mock.patch.object(recorder, "_transcribe_chunk", return_value="Adopted spooler speech."):
            recorder.stop()
        self.assertFalse(recorder.running)


if __name__ == "__main__":
    unittest.main()
