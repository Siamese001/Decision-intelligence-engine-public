"""
tests/adversarial/test_live_asr_circular_import_and_worker_crash_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 1:
Live ASR Worker Circular Import Elimination, Dead-Worker Fast-Fail & Heartbeat FAILED State.

Covers Mandatory Edge-Case Categories (per .agents/rules/e2e-and-edge-case-coverage.md):
1. Exact production regression: Fresh-interpreter import order (Order A vs Order B) preventing circular import.
2. Boundary payload & recovery: Worker death fast-fail (<2s) and terminal circuit breaker preventing infinite respawn.
3. Invariant enforcement: Heartbeat ASR_FAILED state transition and zero-turn context state governance.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.heartbeat_worker import (
    _get_asr_state,
    _get_context_state,
    _get_copilot_state,
    compute_recorder_lifecycle_states,
)
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
    MLXRuntimeLost,
)


class TestLiveASRCircularImportAndWorkerCrashAdversarial(unittest.TestCase):
    """Adversarial and regression test suite for live ASR worker lifecycle and import safety."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent

    # ----------------------------------------------------------------------
    # Category 1: Exact Production Regression — Subprocess Import Orders
    # ----------------------------------------------------------------------

    def test_regression_order_b_audio_postprocess_fresh_interpreter(self) -> None:
        """Regression: Importing audio_postprocess first in a clean interpreter must succeed without circular import."""
        cmd = [
            sys.executable,
            "-c",
            "import dual_pipeline.audio_postprocess; print('ORDER_B_OK')",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Failed Order B import with stderr: {proc.stderr}",
        )
        self.assertIn("ORDER_B_OK", proc.stdout)

    def test_regression_chunk_transcriber_fresh_interpreter(self) -> None:
        """Regression: Importing chunk_transcriber entrypoint directly in a clean interpreter must succeed."""
        cmd = [
            sys.executable,
            "-c",
            "from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import transcribe_meeting_chunk; print('CHUNK_OK')",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Failed chunk_transcriber import with stderr: {proc.stderr}",
        )
        self.assertIn("CHUNK_OK", proc.stdout)

    def test_regression_order_a_audio_ingestion_core_first(self) -> None:
        """Regression: Importing audio_ingestion_core before audio_postprocess must continue to succeed."""
        cmd = [
            sys.executable,
            "-c",
            "import dual_pipeline.audio_ingestion_core; import dual_pipeline.audio_postprocess; print('ORDER_A_OK')",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Failed Order A import with stderr: {proc.stderr}",
        )
        self.assertIn("ORDER_A_OK", proc.stdout)

    # ----------------------------------------------------------------------
    # Category 2: Boundary & Concurrency — Fast-Fail on Dead Child Process
    # ----------------------------------------------------------------------

    def test_dead_worker_fast_fail_under_two_seconds(self) -> None:
        """Boundary: When worker process dies, await_result must detect termination in <2s rather than waiting 30s."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        try:
            client.start(timeout=10.0)
            self.assertTrue(client.is_running)
            worker_proc = client._process
            self.assertIsNotNone(worker_proc)

            # Terminate the worker process abruptly
            if worker_proc is not None:
                worker_proc.terminate()
                worker_proc.join(timeout=2.0)

            t0 = time.perf_counter()
            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_wav:
                # transcribe() should detect death, attempt 1 restart, and if dead, raise MLXRuntimeLost quickly
                # To test _drain_and_await_result directly:
                res = client._drain_and_await_result("task_dummy", timeout=10.0)
                elapsed = time.perf_counter() - t0
                self.assertIsNone(res)
                self.assertLess(
                    elapsed,
                    2.0,
                    f"Expected dead-worker detection in <2s, took {elapsed:.2f}s",
                )
        finally:
            client.stop()

    def test_terminal_circuit_breaker_prevents_infinite_respawns(self) -> None:
        """Boundary: Exceeding maximum restart attempts sets terminal failure and halts further respawns."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        try:
            client.start(timeout=10.0)
            client._restart_count = 1  # Simulate that 1 restart already occurred

            # Kill the process
            if client._process is not None:
                client._process.terminate()
                client._process.join(timeout=2.0)

            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_wav:
                with self.assertRaises(MLXRuntimeLost):
                    client._handle_worker_death(
                        Path(tmp_wav.name),
                        whisper_prompt=None,
                        model_name=None,
                        timeout=5.0,
                    )

            self.assertTrue(client._terminal_failure)
            self.assertFalse(client.is_running)

            # Subsequent transcribe call must fail immediately without spawning a new worker
            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_wav2:
                with self.assertRaises(MLXRuntimeLost):
                    client.transcribe(Path(tmp_wav2.name), timeout=5.0)

            self.assertIsNone(client._process)
        finally:
            client.stop()

    # ----------------------------------------------------------------------
    # Category 3: Invariant Enforcement — Heartbeat ASR_FAILED State
    # ----------------------------------------------------------------------

    def test_asr_state_transitions_to_failed_on_terminal_failure(self) -> None:
        """Invariant: _get_asr_state returns FAILED when terminal_failure is True."""
        mock_transcriber = MagicMock()
        mock_transcriber._terminal_failure = True
        mock_transcriber.is_running = False

        state = _get_asr_state(mock_transcriber, mock=False, is_stopped=False)
        self.assertEqual(state, "FAILED")

    def test_asr_state_transitions_to_failed_when_recorder_asr_failed_set(self) -> None:
        """Invariant: _get_asr_state returns FAILED when asr_failed flag is explicitly set."""
        state = _get_asr_state(None, mock=False, is_stopped=False, asr_failed=True)
        self.assertEqual(state, "FAILED")

    def test_copilot_state_is_not_ready_when_asr_failed(self) -> None:
        """Invariant: copilot_state must be NOT_READY whenever asr_state is FAILED."""
        state = _get_copilot_state(
            capture_state="CAPTURING",
            asr_state="FAILED",
            context_state="FRESH",
        )
        self.assertEqual(state, "NOT_READY")

    def test_context_state_initializing_when_zero_transcribed_chunks(self) -> None:
        """Invariant: Context state must be INITIALIZING when no chunks have been successfully transcribed."""
        recorder = MagicMock()
        recorder._is_stopped = False
        recorder.running = True
        recorder.mock_mode = True
        recorder.capture_manager = None
        recorder._quarantined = False
        recorder.transcribed_chunks = set()
        recorder._asr_failed = False
        recorder.processed_chunks = {"chunk_00000.wav", "chunk_00001.wav"}
        recorder.last_audible_monotonic = time.monotonic()
        recorder.isolated_transcriber = MagicMock()
        recorder.isolated_transcriber._terminal_failure = False
        recorder.isolated_transcriber.is_running = True

        _, asr_st, ctx_st, copilot_st = compute_recorder_lifecycle_states(recorder)
        self.assertEqual(ctx_st, "INITIALIZING")
        self.assertEqual(asr_st, "READY")
        self.assertEqual(copilot_st, "READY")

        # Now simulate terminal ASR failure
        recorder._asr_failed = True
        _, asr_st2, _, copilot_st2 = compute_recorder_lifecycle_states(recorder)
        self.assertEqual(asr_st2, "FAILED")
        self.assertEqual(copilot_st2, "NOT_READY")


if __name__ == "__main__":
    unittest.main()
