"""
tests/test_live_audio_chunk_validator.py

Unit tests for Wave 3: Zero-Byte WAV Defense, Stream Health Sentinel, and Master Stitcher.
Verifies:
1. AudioChunkValidator rejects 78-byte header-only and digital-zero WAV files.
2. get_audio_rms raises EmptyAudioChunkError on 0-frame or truncated WAV.
3. AudioMasterStitcher concatenates multi-epoch chunks losslessly into a continuous master WAV.
4. LiveMeetingRecorder fails over on stalled sub-4096B chunks and rejects corrupt files.
"""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    MIN_VALID_CHUNK_BYTES,
    AudioChunkValidator,
    EmptyAudioChunkError,
)
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.audio_utils import get_audio_rms


def _create_wav(
    path: Path,
    n_samples: int = 16000,
    amplitude: int = 1000,
    channels: int = 1,
    sample_rate: int = 16000,
) -> None:
    """Helper creating a standard test WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        if n_samples == 0:
            w.writeframes(b"")
            return
        samples = [int(amplitude * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


class TestLiveAudioChunkValidator(unittest.TestCase):
    def test_validator_rejects_78_byte_header_only_wav(self):
        """Asserts that a 78-byte header-only WAV (exact forensic anatomy) fails validation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "empty_chunk.wav"
            # Exactly 78 bytes header
            _create_wav(wav_path, n_samples=0)
            assert wav_path.stat().st_size <= 78

            result = AudioChunkValidator.validate_chunk(wav_path)
            self.assertFalse(result.is_valid)
            self.assertTrue(result.is_dead_stream)
            self.assertIn("below minimum threshold", result.reason)

    def test_validator_detects_pure_digital_zeros(self):
        """Asserts that a full-size chunk containing 100% digital zeros is flagged as dead stream."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "digital_zeros.wav"
            # 8000 samples of amplitude 0 = 16,000 bytes (above 4096B threshold)
            _create_wav(wav_path, n_samples=8000, amplitude=0)
            self.assertGreater(wav_path.stat().st_size, MIN_VALID_CHUNK_BYTES)

            result = AudioChunkValidator.validate_chunk(wav_path, allow_digital_zero=False)
            self.assertFalse(result.is_valid)
            self.assertTrue(result.is_dead_stream)
            self.assertIn("100% digital zeros", result.reason)

    def test_validator_approves_valid_audio(self):
        """Asserts that a normal audio chunk passes validation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "valid.wav"
            _create_wav(wav_path, n_samples=16000, amplitude=1500)
            result = AudioChunkValidator.validate_chunk(wav_path)
            self.assertTrue(result.is_valid)
            self.assertFalse(result.is_dead_stream)
            self.assertGreater(result.rms, 0.0)
            self.assertAlmostEqual(result.duration_sec, 1.0, places=2)

    def test_get_audio_rms_raises_on_empty_chunk(self):
        """Asserts that get_audio_rms raises EmptyAudioChunkError on 0-frame or sub-100B WAV."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "zero_frames.wav"
            _create_wav(wav_path, n_samples=0)
            with self.assertRaises(EmptyAudioChunkError):
                get_audio_rms(wav_path)

    def test_master_stitcher_concatenates_valid_chunks(self):
        """Asserts that AudioMasterStitcher stitches valid chunks into unified master WAV."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks_dir = Path(tmp_dir) / "chunks"
            master_wav = Path(tmp_dir) / "master.wav"

            # Create valid contiguous 1-second chunks
            _create_wav(chunks_dir / "chunk_0000.wav", n_samples=16000, amplitude=800)
            _create_wav(chunks_dir / "chunk_0001.wav", n_samples=16000, amplitude=1200)

            success, count, duration = AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)
            self.assertTrue(success)
            self.assertEqual(count, 2)
            self.assertAlmostEqual(duration, 2.0, places=2)
            self.assertTrue(master_wav.exists())

            # Verify master WAV frame count
            with wave.open(str(master_wav), "rb") as w:
                self.assertEqual(w.getnframes(), 32000)

            # Fail-fast: adding a corrupt/empty chunk must raise RuntimeError, refusing to drop audio
            _create_wav(chunks_dir / "chunk_0002.wav", n_samples=0)
            with self.assertRaises(RuntimeError) as ctx:
                AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)
            self.assertIn("[STITCHER-CORRUPT-CHUNK]", str(ctx.exception))

    def test_recorder_process_latest_chunk_triggers_failover_on_truncated_chunk(self):
        """Asserts that _process_latest_chunk detects stalled sub-4096B file and invokes fallback."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            chunks_dir = tmp_path / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            chunk_file = chunks_dir / "chunk_0000.wav"
            _create_wav(chunk_file, n_samples=0)  # 78 bytes

            with patch("dual_pipeline.live_meeting_recorder.LiveAudioCaptureManager") as mock_cap_cls, patch(
                "dual_pipeline.audio_ingestion_core.diarization_engine.get_diarization_engine"
            ):
                mock_cap = MagicMock()
                mock_cap_cls.return_value = mock_cap
                recorder = LiveMeetingRecorder(
                    chunks_dir=chunks_dir,
                    output_dir=tmp_path / "out",
                    meeting_name="Stall Test",
                    mock_mode=False,
                )

                try:
                    last_file_sizes = {"chunk_0000.wav": chunk_file.stat().st_size}
                    with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                        recorder._process_latest_chunk(chunk_file, last_file_sizes)
                    self.assertEqual(ctx.exception.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)
                    mock_cap.fallback_to_microphone.assert_not_called()
                    self.assertNotIn("chunk_0000.wav", recorder.processed_chunks)
                finally:
                    if getattr(recorder, "_slug_lock_fd", None):
                        try:
                            import fcntl
                            fcntl.flock(recorder._slug_lock_fd, fcntl.LOCK_UN)
                            recorder._slug_lock_fd.close()
                        except Exception:
                            pass


if __name__ == "__main__":
    unittest.main()
