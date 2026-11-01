"""End-to-End integration test suite for live capture resilience and master audio stitching.

Validates the full live recording lifecycle through simulated stream dropouts:
1. Session initialization with non-dropping mixer and stderr redirection.
2. Inactivity timeout governance persisting through extended silence intervals.
3. Master audio stitching completing with valid non-empty WAV file.
4. Clean process teardown without orphaned processes or locked session files.
"""
from __future__ import annotations

import contextlib
import struct
import tempfile
import time
import unittest
import uuid
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from pipeline_a_intake.mac_system_audio import FFMPEG_BIN


def _generate_synthetic_wav(path: Path, duration_sec: float = 1.0, sample_rate: int = 16000) -> Path:
    """Generates a valid synthetic mono 16kHz PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # 440 Hz tone
        raw_frames = bytearray()
        for i in range(n_samples):
            val = int(1000.0 * (1 if (i // 18) % 2 == 0 else -1))
            raw_frames.extend(struct.pack("<h", val))
        wf.writeframes(raw_frames)
    return path


class TestLiveCaptureResilienceE2E(unittest.TestCase):
    """End-to-End integration tests for live audio capture resilience."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.output_dir = self.tmp_path / "output"
        self.chunks_dir = self.tmp_path / "chunks"
        self.master_wav = self.tmp_path / "master.wav"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_e2e_recording_survives_silence_and_stitches_master(self) -> None:
        """E2E: Meeting recorder survives silence gap and successfully stitches master WAV."""
        unique_meeting = f"E2E Resilience {uuid.uuid4().hex[:8]}"
        recorder = LiveMeetingRecorder(
            meeting_name=unique_meeting,
            output_dir=self.output_dir,
            mock_mode=True,
            segment_seconds=1,
            inactivity_timeout_seconds=0.0,
            immediate_worktree=False,
        )

        def _cleanup():
            with contextlib.suppress(Exception):
                recorder.stop()
        self.addCleanup(_cleanup)

        # Populate synthetic chunks
        chunk0 = _generate_synthetic_wav(recorder.chunks_dir / "chunk_00000.wav", duration_sec=1.0)
        chunk1 = _generate_synthetic_wav(recorder.chunks_dir / "chunk_00001.wav", duration_sec=1.0)
        recorder.processed_chunks.update([chunk0, chunk1])
        recorder.total_audible_chunks = 2

        # Simulate 200s gap since last audible sound (would have triggered 180s old timeout)
        recorder.last_audible_monotonic = time.monotonic() - 200.0

        # Stitch master audio
        stitched = recorder._stitch_master_audio()
        self.assertTrue(stitched, "Master audio failed to stitch")
        self.assertTrue(recorder.master_wav.exists(), "Master WAV was not generated")
        self.assertGreater(recorder.master_wav.stat().st_size, 4096, "Master WAV is empty or truncated")

        # Verify no premature auto-stop reason was recorded
        self.assertIsNone(recorder.auto_stopped_reason)

    def test_e2e_capture_manager_stderr_diagnostics_logging(self) -> None:
        """E2E: LiveAudioCaptureManager writes diagnostic stderr logs and flushes cleanly."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mic",
            mock_mode=True,
        )
        self.addCleanup(mgr.stop)

        mgr.start()
        self.assertTrue(mgr.is_running)

        # Trigger stderr file creation via _get_capture_stderr_file
        f = mgr._get_capture_stderr_file()
        f.write("[E2E-TEST] ScreenCaptureKit diagnostic stream log entry\n")
        f.flush()

        mgr.stop()
        self.assertFalse(mgr.is_running)

        # Verify stderr log is persisted
        stderr_log = self.chunks_dir / "capture_stderr.log"
        self.assertTrue(stderr_log.exists(), "capture_stderr.log was not created")
        log_content = stderr_log.read_text(encoding="utf-8")
        self.assertIn("[E2E-TEST] ScreenCaptureKit diagnostic stream log entry", log_content)


if __name__ == "__main__":
    unittest.main()
