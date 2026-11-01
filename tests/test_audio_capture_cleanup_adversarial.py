"""Adversarial and pipe-interaction tests for Phase 3:
Eliminate unbounded capture cleanup and stream-reader pipe deadlocks.

Covers:
1. Quiet producer with active blocked stderr reader thread (verifies zero-deadlock bounded cleanup).
2. Startup proof exception preservation across secondary cleanup errors.
3. Multi-child cleanup resilience when one child process fails or exits prematurely.
4. Unrelated process preservation (verifies zero cross-session signalling).
5. Proof receipt write failure resilience.
6. Channel-specific silence detection in stereo mode.
7. Delayed buffered audio measurement within deadline bounds.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
    MultiSourceAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)


def test_pipe_cleanup_with_quiet_producer_and_active_reader(tmp_path: Path):
    """Verifies that a background reader blocked on readline() does NOT deadlock cleanup on a quiet producer."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    master_wav = tmp_path / "master.wav"

    mgr = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        source_mode="auto",
        mock_mode=True,
    )

    # Spawn an isolated quiet python process that stays alive and writes nothing to stderr
    cmd = [sys.executable, "-c", "import time; time.sleep(15)"]
    quiet_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    mgr._sck_proc = quiet_proc
    mgr._is_running = True

    # Start the stderr drain thread (blocked in readline)
    mgr._start_sck_stderr_drain(quiet_proc)
    assert len(mgr._drain_threads) == 1
    assert mgr._drain_threads[0].is_alive()

    # Time cleanup: must finish in < 1.0 second without deadlocking on pipe close
    t0 = time.perf_counter()
    mgr.stop_processes(fast=False)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, f"Cleanup took too long ({elapsed:.3f}s), indicating a potential stream-lock wait."
    assert quiet_proc.poll() is not None
    assert mgr.capture_state == "STOPPED"
    assert len(mgr._drain_threads) == 0


def test_cleanup_preserves_initial_startup_exception(tmp_path: Path):
    """Cleanup failure must not erase or replace the initial startup/proof exception."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    master_wav = tmp_path / "master.wav"

    mgr = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        source_mode="auto",
        mock_mode=True,
    )

    initial_exc = AudioCaptureUnprovenError(
        stage="P0_SCK_VERIFY",
        reason_code=AudioReasonCode.SCK_TAP_FAILED,
        detail="Original tap initialization failure",
    )
    failure_path = mgr._persist_startup_failure(initial_exc, stage="P0_SCK_VERIFY")
    assert failure_path is not None
    assert failure_path.exists()

    # Inject mock process whose cleanup throws an exception
    mock_proc = MagicMock()
    mock_proc.poll.side_effect = RuntimeError("Secondary termination crash")
    mgr._ffmpeg_proc = mock_proc

    # Cleanup runs and handles secondary crash
    mgr.stop_processes()

    # Original exception remains preserved and unmasked
    assert mgr.startup_exception is initial_exc
    assert mgr.capture_state == "FAILED"

    # Verify persisted JSON on disk still contains the original error
    disk_data = json.loads(failure_path.read_text(encoding="utf-8"))
    assert disk_data["reason_code"] == AudioReasonCode.SCK_TAP_FAILED
    assert "Original tap initialization failure" in disk_data["error"]


def test_cleanup_handles_multiple_owned_children_when_one_fails(tmp_path: Path):
    """Cleanup must process all owned children even if one child raises an exception."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    master_wav = tmp_path / "master.wav"

    mgr = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        source_mode="auto",
        mock_mode=True,
    )

    # Proc 1: Normal real subprocess
    proc1 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"], start_new_session=True)
    # Proc 2: Normal real subprocess
    proc2 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"], start_new_session=True)

    mgr._sck_proc = proc1
    mgr._ffmpeg_proc = proc2

    # Patch proc1.wait to raise an unexpected OSError during termination
    orig_wait = proc1.wait
    def _broken_wait(*args, **kwargs):
        raise OSError("Simulated broken wait")
    proc1.wait = _broken_wait

    t0 = time.perf_counter()
    mgr.stop_processes(fast=False)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.5
    # Both processes must be terminated
    assert proc1.poll() is not None or proc1.returncode is not None
    assert proc2.poll() is not None or proc2.returncode is not None


def test_cleanup_never_signals_unrelated_capture_processes(tmp_path: Path):
    """Cleanup must isolate signaling strictly to owned session processes, never killing unrelated processes."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    master_wav = tmp_path / "master.wav"

    mgr = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        source_mode="auto",
        mock_mode=True,
    )

    # Spawn an unrelated background dummy process
    unrelated_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        start_new_session=True,
    )
    try:
        assert unrelated_proc.poll() is None

        # Manager only owns its own process
        owned_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
        mgr._ffmpeg_proc = owned_proc

        mgr.stop_processes()

        # Owned process is dead
        assert owned_proc.poll() is not None

        # Unrelated process is STILL ALIVE
        assert unrelated_proc.poll() is None
    finally:
        unrelated_proc.kill()
        unrelated_proc.wait(timeout=1.0)


def test_proof_receipt_write_failure_resilience(tmp_path: Path):
    """Proof-receipt write failure must not crash or hang capture verification."""
    master_wav = tmp_path / "master.wav"
    master_wav.write_bytes(b"RIFF" + b"\x00" * 40 + (b"\x50\x00" + b"\xb0\xff") * 8000)

    mock_ffmpeg = MagicMock()
    mock_ffmpeg.poll.return_value = None
    mock_ffmpeg.pid = 9999

    unwriteable_receipt_path = Path("/dev/null/cannot_write_receipt/receipt.json")

    receipt = AudioCaptureProofVerifier.run_full_start_proof(
        master_wav=master_wav,
        ffmpeg_proc=mock_ffmpeg,
        mock_mode=False,
        receipt_path=unwriteable_receipt_path,
    )
    assert receipt["status"] == "CAPTURE_PROVEN"
    assert receipt["ffmpeg_pid"] == 9999


def test_channel_specific_silence_detection_stereo(tmp_path: Path):
    """In stereo mixed_sck mode, digital zero on the mic channel (channel 1) must fail closed."""
    # Create 1 second of 16kHz stereo: channel 0 has signal, channel 1 is all zeros
    n_frames = 16000
    interleaved = bytearray()
    for _ in range(n_frames):
        interleaved.extend((1000).to_bytes(2, byteorder="little", signed=True))  # SCK active
        interleaved.extend((0).to_bytes(2, byteorder="little", signed=True))     # Mic digital zero

    wav_header = b"RIFF" + (36 + len(interleaved)).to_bytes(4, "little") + b"WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00\x80>\x00\x00\x00\xfa\x00\x00\x04\x00\x10\x00data" + len(interleaved).to_bytes(4, "little")
    raw_wav = bytes(wav_header + interleaved)

    with pytest.raises(AudioCaptureUnprovenError) as exc_info:
        AudioCaptureProofVerifier.verify_mic_non_digital_zero(
            pcm_bytes=raw_wav,
            is_stereo=True,
            mock_mode=False,
        )
    assert exc_info.value.reason_code == AudioReasonCode.MIC_DIGITAL_ZERO


def test_delayed_buffered_audio_measurement(tmp_path: Path):
    """Verifies that delayed buffered audio accumulates within deadline bounds and satisfies byte rate."""
    master_wav = tmp_path / "delayed_master.wav"

    def _delayed_writer():
        time.sleep(0.15)  # Simulate initial CoreAudio / FFmpeg buffer latency
        # Write 1.5s worth of mono PCM (16000 * 2 * 1.5 = 48000 bytes)
        payload = b"RIFF" + b"\x00" * 40 + (b"\x10\x00" * 24000)
        master_wav.write_bytes(payload)

    writer_thread = threading.Thread(target=_delayed_writer, daemon=True)
    writer_thread.start()

    res = AudioCaptureProofVerifier.verify_pcm_byte_rate(
        master_wav=master_wav,
        is_stereo=False,
        min_duration_sec=1.0,
        deadline_sec=2.0,
        mock_mode=False,
    )
    writer_thread.join()

    assert res["proven"] is True
    assert res["bytes_read"] >= 48000


def test_detect_device_contention_auto_reaps_orphaned_ffmpeg(tmp_path: Path):
    """Verifies detect_device_contention detects and terminates foreign orphaned FFmpeg processes accessing the device."""
    mock_pgrep = MagicMock()
    mock_pgrep.returncode = 0
    mock_pgrep.stdout = "8888 ffmpeg -f avfoundation -i :0 -ar 16000\n"

    with patch("dual_pipeline.live_meeting_recorder_core.process_cleanup.subprocess.run", return_value=mock_pgrep), \
         patch("dual_pipeline.live_meeting_recorder_core.process_cleanup.os.kill") as mock_kill, \
         patch("dual_pipeline.live_meeting_recorder_core.process_cleanup.time.sleep"):
        mock_kill.side_effect = [None, None, None, OSError("No such process")]
        contention = LiveAudioCaptureManager.detect_device_contention(device_index=":0", auto_reap=True)
        assert contention is True
        mock_kill.assert_any_call(8888, signal.SIGTERM)


def test_clean_session_quarantine_purges_marker_paths(tmp_path: Path):
    """Verifies clean_session_quarantine unlinks .quarantine from output_dir and _target_output_dir."""
    from dual_pipeline.live_meeting_recorder_core.recording_host_integration import clean_session_quarantine

    out_dir = tmp_path / "out"
    tgt_dir = tmp_path / "tgt"
    out_dir.mkdir()
    tgt_dir.mkdir()

    q1 = out_dir / ".quarantine"
    q2 = tgt_dir / ".quarantine"
    q1.write_text('{"status": "QUARANTINED"}')
    q2.write_text('{"status": "QUARANTINED"}')

    assert q1.exists()
    assert q2.exists()

    dummy_recorder = MagicMock()
    dummy_recorder.output_dir = out_dir
    dummy_recorder._target_output_dir = tgt_dir

    clean_session_quarantine(dummy_recorder)

    assert not q1.exists()
    assert not q2.exists()
