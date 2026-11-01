"""
tests/adversarial/test_mlx_init_fail_fast_adversarial.py

Adversarial Test Suite for MLX Whisper Fail-Fast Proof-of-Readiness Gate.
Validates Wave 2 invariants:
  1. Staged proof-of-readiness handshake: SPAWNED -> IMPORT -> METAL -> RESOLVE -> LOAD -> GOLDEN_INFERENCE -> READY.
  2. Strict zero-fallback: every failure mode immediately raises typed MLXInitError with closed enum reason codes.
  3. Strict process airgap: outbound AF_INET/AF_INET6 connections raise AirgapViolationError.
  4. Audio analysis fail-fast: RMS/VAD failures raise AudioAnalysisError instead of silent strings.
  5. Recovery boundary: unrecoverable worker crashes raise MLXRuntimeLost after single governed restart.

Reason Codes Covered:
  - MLX_IMPORT_FAILED
  - METAL_UNAVAILABLE
  - MODEL_NOT_CACHED
  - MODEL_REVISION_MISMATCH
  - MODEL_LOAD_FAILED
  - GOLDEN_INFERENCE_EMPTY
  - GOLDEN_INFERENCE_MISMATCH
  - MLX_READY_DEADLINE_EXCEEDED
  - MLX_WORKER_DIED / MLX_RUNTIME_LOST
"""
from __future__ import annotations

import queue
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
    ReadinessFuture,
)
from dual_pipeline.live_meeting_recorder_core.mlx_worker import (
    AirgapViolationError,
    MLXInitError,
    MLXReasonCode,
    MLXRuntimeLost,
    enforce_process_airgap,
    perform_mlx_readiness_probe,
)
from pipeline_a_intake.local_stt_core.audio_processor import (
    ModelNotCachedError,
    ModelRevisionMismatchError,
)


class TestMLXInitFailFastAdversarial(unittest.TestCase):
    """Adversarial fault-injection suite for MLX Whisper readiness verification."""

    def setUp(self) -> None:
        self._orig_socket_connect = socket.socket.connect
        self._golden_wav = Path(__file__).resolve().parents[2] / "dual_pipeline" / "live_meeting_recorder_core" / "assets" / "mlx_golden_16k.wav"
        self._created_golden_wav = False
        if not self._golden_wav.exists():
            self._golden_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
            self._created_golden_wav = True

    def tearDown(self) -> None:
        socket.socket.connect = self._orig_socket_connect
        if getattr(self, "_created_golden_wav", False) and self._golden_wav.exists():
            self._golden_wav.unlink(missing_ok=True)

    # -------------------------------------------------------------------------
    # ADV-1: Airgap Violation & Network Isolation
    # -------------------------------------------------------------------------
    def test_adv_1_airgap_blocks_external_egress(self) -> None:
        """Verify that outbound external network connections are blocked by the airgap policy."""
        enforce_process_airgap()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(AirgapViolationError) as ctx:
                s.connect(("93.184.216.34", 80))  # example.com
            self.assertIn("blocked by MLX Whisper airgap policy", str(ctx.exception))
            self.assertIn("zero cloud egress", str(ctx.exception))
        finally:
            s.close()

    def test_adv_1_airgap_allows_localhost_loopback(self) -> None:
        """Verify that loopback connections (IPC/WebSocket) are permitted under airgap."""
        enforce_process_airgap()
        # Should NOT raise AirgapViolationError when attempting localhost
        # (It may fail with ConnectionRefusedError if no listener exists, but not AirgapViolationError)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                s.connect(("127.0.0.1", 59999))
            except ConnectionRefusedError:
                pass  # Permitted
            except OSError:
                pass  # Permitted
        finally:
            s.close()

    # -------------------------------------------------------------------------
    # ADV-2: MLX Import Failure
    # -------------------------------------------------------------------------
    def test_adv_2_import_failure_raises_typed_error(self) -> None:
        """Verify that missing MLX packages trigger MLX_IMPORT_FAILED."""
        real_import = __import__

        def _mock_import(name, *args, **kwargs):
            if "mlx_whisper" in name:
                raise ImportError("No module named 'mlx_whisper'")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=_mock_import):
            with self.assertRaises(MLXInitError) as ctx:
                perform_mlx_readiness_probe(mock_mode=False)

            err = ctx.exception
            self.assertEqual(err.stage, "IMPORT")
            self.assertEqual(err.reason_code, MLXReasonCode.MLX_IMPORT_FAILED)
            self.assertIn("Failed to import MLX modules", err.detail)

    # -------------------------------------------------------------------------
    # ADV-3: Metal Acceleration Unavailable
    # -------------------------------------------------------------------------
    @mock.patch("mlx.core.metal.is_available", return_value=False)
    def test_adv_3_metal_unavailable_raises_typed_error(self, mock_metal) -> None:
        """Verify that lack of Apple Silicon Metal GPU triggers METAL_UNAVAILABLE."""
        with self.assertRaises(MLXInitError) as ctx:
            perform_mlx_readiness_probe(mock_mode=False)

        err = ctx.exception
        self.assertEqual(err.stage, "METAL")
        self.assertEqual(err.reason_code, MLXReasonCode.METAL_UNAVAILABLE)
        self.assertIn("Metal GPU acceleration is not available", err.detail)

    # -------------------------------------------------------------------------
    # ADV-4: Model Cache Resolution Failure
    # -------------------------------------------------------------------------
    @mock.patch("mlx.core.metal.is_available", return_value=True)
    @mock.patch(
        "pipeline_a_intake.local_stt_core.audio_processor.resolve_model_path",
        side_effect=ModelNotCachedError("Snapshot missing in HF cache"),
    )
    def test_adv_4_model_not_cached_raises_typed_error(self, mock_resolve, mock_metal) -> None:
        """Verify that an uncached model triggers MODEL_NOT_CACHED."""
        with self.assertRaises(MLXInitError) as ctx:
            perform_mlx_readiness_probe(mock_mode=False)

        err = ctx.exception
        self.assertEqual(err.stage, "MODEL_RESOLVE")
        self.assertEqual(err.reason_code, MLXReasonCode.MODEL_NOT_CACHED)
        self.assertIn("Snapshot missing in HF cache", err.detail)

    # -------------------------------------------------------------------------
    # ADV-5: Model Revision Mismatch
    # -------------------------------------------------------------------------
    @mock.patch("mlx.core.metal.is_available", return_value=True)
    @mock.patch(
        "pipeline_a_intake.local_stt_core.audio_processor.resolve_model_path",
        side_effect=ModelRevisionMismatchError("Expected commit abc, found def"),
    )
    def test_adv_5_model_revision_mismatch_raises_typed_error(self, mock_resolve, mock_metal) -> None:
        """Verify that revision drift triggers MODEL_REVISION_MISMATCH."""
        with self.assertRaises(MLXInitError) as ctx:
            perform_mlx_readiness_probe(mock_mode=False)

        err = ctx.exception
        self.assertEqual(err.stage, "MODEL_RESOLVE")
        self.assertEqual(err.reason_code, MLXReasonCode.MODEL_REVISION_MISMATCH)
        self.assertIn("Expected commit abc, found def", err.detail)

    # -------------------------------------------------------------------------
    # ADV-6: Model Load Weight Corruption
    # -------------------------------------------------------------------------
    @mock.patch("mlx.core.metal.is_available", return_value=True)
    @mock.patch(
        "pipeline_a_intake.local_stt_core.audio_processor.resolve_model_path",
        return_value="/tmp/mock_model_path",
    )
    @mock.patch(
        "mlx_whisper.load_models.load_model",
        side_effect=RuntimeError("weights.safetensors header corrupted"),
    )
    def test_adv_6_model_load_failed_raises_typed_error(self, mock_load, mock_resolve, mock_metal) -> None:
        """Verify that weight loading failures trigger MODEL_LOAD_FAILED."""
        with self.assertRaises(MLXInitError) as ctx:
            perform_mlx_readiness_probe(mock_mode=False)

        err = ctx.exception
        self.assertEqual(err.stage, "MODEL_LOAD")
        self.assertEqual(err.reason_code, MLXReasonCode.MODEL_LOAD_FAILED)
        self.assertIn("weights.safetensors header corrupted", err.detail)

    # -------------------------------------------------------------------------
    # ADV-7: Golden Inference Emits Empty Text
    # -------------------------------------------------------------------------
    @mock.patch("mlx.core.metal.is_available", return_value=True)
    @mock.patch(
        "pipeline_a_intake.local_stt_core.audio_processor.resolve_model_path",
        return_value="/tmp/mock_model_path",
    )
    @mock.patch("mlx_whisper.load_models.load_model")
    @mock.patch("mlx.core.eval")
    @mock.patch("mlx_whisper.transcribe", return_value={"text": ""})
    def test_adv_7_golden_inference_empty_raises_typed_error(
        self, mock_transcribe, mock_eval, mock_load, mock_resolve, mock_metal
    ) -> None:
        """Verify that silent/empty golden inference output triggers GOLDEN_INFERENCE_EMPTY."""
        with self.assertRaises(MLXInitError) as ctx:
            perform_mlx_readiness_probe(mock_mode=False)

        err = ctx.exception
        self.assertEqual(err.stage, "GOLDEN_INFERENCE")
        self.assertEqual(err.reason_code, MLXReasonCode.GOLDEN_INFERENCE_EMPTY)
        self.assertIn("empty transcription text", err.detail)

    # -------------------------------------------------------------------------
    # ADV-8: Golden Inference Token Mismatch / Severe Hallucination
    # -------------------------------------------------------------------------
    @mock.patch("mlx.core.metal.is_available", return_value=True)
    @mock.patch(
        "pipeline_a_intake.local_stt_core.audio_processor.resolve_model_path",
        return_value="/tmp/mock_model_path",
    )
    @mock.patch("mlx_whisper.load_models.load_model")
    @mock.patch("mlx.core.eval")
    @mock.patch(
        "mlx_whisper.transcribe",
        return_value={"text": "completely unrelated random noise that has zero token overlap with golden test"},
    )
    def test_adv_8_golden_inference_mismatch_raises_typed_error(
        self, mock_transcribe, mock_eval, mock_load, mock_resolve, mock_metal
    ) -> None:
        """Verify that degraded golden inference token overlap triggers GOLDEN_INFERENCE_MISMATCH."""
        with self.assertRaises(MLXInitError) as ctx:
            perform_mlx_readiness_probe(mock_mode=False)

        err = ctx.exception
        self.assertEqual(err.stage, "GOLDEN_INFERENCE")
        self.assertEqual(err.reason_code, MLXReasonCode.GOLDEN_INFERENCE_MISMATCH)
        self.assertIn("token overlap ratio", err.detail)

    # -------------------------------------------------------------------------
    # ADV-9: Probe Deadline Exceeded (Timeout)
    # -------------------------------------------------------------------------
    def test_adv_9_readiness_deadline_exceeded(self) -> None:
        """Verify that worker failing to signal READY before deadline raises TimeoutError."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client._output_q = queue.Queue()  # Empty queue simulates unresponsive worker
        fut = ReadinessFuture(client, timeout=0.05)
        with self.assertRaises(TimeoutError) as ctx:
            fut.result(timeout=0.2)
        self.assertIn(MLXReasonCode.MLX_READY_DEADLINE_EXCEEDED, str(ctx.exception))

    # -------------------------------------------------------------------------
    # ADV-10: Client Handshake Fail-Fast on Worker Error
    # -------------------------------------------------------------------------
    def test_adv_10_client_start_propagates_worker_init_error(self) -> None:
        """Verify that when the worker process emits an INIT_ERROR, client.start() re-raises MLXInitError."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        fake_output_q = queue.Queue()
        fake_output_q.put({
            "type": "INIT_ERROR",
            "stage": "METAL",
            "reason_code": MLXReasonCode.METAL_UNAVAILABLE,
            "detail": "Adversarial simulated Metal GPU failure",
        })
        client._output_q = fake_output_q
        client._is_running = True

        with self.assertRaises(MLXInitError) as ctx:
            client._await_handshake(timeout=1.0)

        err = ctx.exception
        self.assertEqual(err.stage, "METAL")
        self.assertEqual(err.reason_code, MLXReasonCode.METAL_UNAVAILABLE)
        self.assertIn("Adversarial simulated Metal GPU failure", err.detail)
        self.assertFalse(client.is_running)

    # -------------------------------------------------------------------------
    # ADV-11: Worker Process Termination and Recovery Boundary
    # -------------------------------------------------------------------------
    def test_adv_11_worker_crash_recovery_and_runtime_lost(self) -> None:
        """Verify single governed restart and MLXRuntimeLost on persistent worker crash."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client._is_running = True
        client._input_q = queue.Queue()
        client._output_q = queue.Queue()

        # Mock process alive at entry check, but dead after queue timeout
        mock_proc = mock.MagicMock()
        mock_proc.is_alive.side_effect = [True, False, False, False]
        client._process = mock_proc

        # Set restart_count to 1 so the next crash exceeds maximum recovery attempts
        client._restart_count = 1

        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            wav_path = Path(f.name)
            with self.assertRaises(MLXRuntimeLost) as ctx:
                client.transcribe(wav_path, timeout=0.05)

            self.assertIn(MLXReasonCode.MLX_RUNTIME_LOST, str(ctx.exception))
            self.assertIn("exceeded maximum recovery attempts", str(ctx.exception))

    # -------------------------------------------------------------------------
    # ADV-12: Closed Standard Stream FD Repaired Before Spawn
    # -------------------------------------------------------------------------
    def test_adv_12_closed_standard_stream_fd_repaired(self) -> None:
        """Verify _ensure_valid_standard_streams repairs a closed FD (EBADF) to /dev/null."""
        import os

        real_fstat = os.fstat
        fstat_calls = []

        def fake_fstat(fd: int):
            fstat_calls.append(fd)
            if fd == 0 and fstat_calls.count(0) == 1:
                raise OSError(9, "Bad file descriptor")
            return real_fstat(fd)

        with mock.patch("os.fstat", side_effect=fake_fstat), \
             mock.patch("os.dup2") as mock_dup2:
            IsolatedMLXTranscriberClient._ensure_valid_standard_streams()
            self.assertTrue(mock_dup2.called)
            dup_args = mock_dup2.call_args[0]
            self.assertEqual(dup_args[1], 0)

    # -------------------------------------------------------------------------
    # ADV-13: Immediate Worker Exit During Handshake Fails Fast (<0.5s)
    # -------------------------------------------------------------------------
    def test_adv_13_handshake_immediate_worker_exit_fails_fast(self) -> None:
        """Verify _await_handshake detects dead worker process in <0.5s instead of hanging 60s."""
        import time

        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client._is_running = True
        client._output_q = queue.Queue()

        dead_proc = mock.MagicMock()
        dead_proc.is_alive.return_value = False
        dead_proc.exitcode = 1
        client._process = dead_proc

        t0 = time.monotonic()
        with self.assertRaises(MLXInitError) as ctx:
            client._await_handshake(timeout=60.0)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.5, f"Handshake blocked for {elapsed:.2f}s despite dead worker")
        self.assertEqual(ctx.exception.stage, "SPAWN")
        self.assertEqual(ctx.exception.reason_code, MLXReasonCode.MLX_WORKER_DIED)
        self.assertIn("exitcode=1", ctx.exception.detail)
        self.assertFalse(client.is_running)

    # -------------------------------------------------------------------------
    # ADV-14: Revoked Darwin PTY Slave on FD 0 Repaired Before Worker Spawn
    # -------------------------------------------------------------------------
    def test_adv_14_revoked_pty_slave_repaired_before_worker_spawn(self) -> None:
        """Verify child with revoked PTY on FD 0 repairs stream and spawns worker without init_sys_streams crash."""
        import os
        import pty
        import subprocess
        import sys
        import time

        repo_root = Path(__file__).resolve().parents[2]
        master, slave = pty.openpty()
        with tempfile.TemporaryDirectory() as tmpdir:
            ready_flag = Path(tmpdir) / "ready.flag"
            child_script = f"""
import fcntl, os, pathlib, sys, termios, time
sys.path.insert(0, {repr(str(repo_root))})
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
)

try:
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
except Exception:
    pass

pathlib.Path({repr(str(ready_flag))}).write_text("1")
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    try:
        os.fstat(0)
        time.sleep(0.02)
    except OSError:
        break

IsolatedMLXTranscriberClient._ensure_valid_standard_streams()
os.fstat(0)
client = IsolatedMLXTranscriberClient(mock_mode=True)
client.start(timeout=10.0)
assert client.is_running
client.stop()
"""
            proc = subprocess.Popen(
                [sys.executable, "-c", child_script],
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave)
            try:
                deadline = time.monotonic() + 5.0
                while not ready_flag.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                os.close(master)
                _, stderr_bytes = proc.communicate(timeout=15.0)
                stderr_txt = stderr_bytes.decode("utf-8", errors="replace")
                self.assertEqual(proc.returncode, 0, f"Subprocess failed: {stderr_txt}")
                self.assertNotIn("init_sys_streams", stderr_txt)
            finally:
                try:
                    os.close(master)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()

