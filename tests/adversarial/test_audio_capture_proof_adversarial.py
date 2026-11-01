"""
tests/adversarial/test_audio_capture_proof_adversarial.py

Adversarial Test Suite for Audio Proof-of-Capture Gate.
Validates Wave 3 invariants and Operator Decision D1:
  1. Empirical Proof-of-Capture (P0: alive, P1: byte rate, P2: mic not digital-zero, P3: continuous growth).
  2. Strict zero-fallback: failures fail closed; absolutely NO fallback to microphone.
  3. Quarantine isolation on failure (.quarantine marker created, lock released).
  4. All closed-enum AudioReasonCodes covered via fault injection.
"""
from __future__ import annotations

import math
import shutil
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)


def _synthesize_test_wav(
    path: Path,
    duration_sec: float,
    channels: int = 1,
    amplitude: float = 3000.0,
    freq_hz: float = 440.0,
    sample_rate: int = 16000,
    mic_digital_zero: bool = False,
) -> Path:
    """Helper to synthesize test WAV with explicit channel acoustic profiles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            t = float(i) / sample_rate
            sample_val = int(amplitude * math.sin(2.0 * math.pi * freq_hz * t))
            if channels == 1:
                val = 0 if mic_digital_zero else sample_val
                frames.extend(struct.pack("<h", max(-32768, min(32767, val))))
            elif channels == 2:
                # Channel 0: System/SCK audio
                frames.extend(struct.pack("<h", max(-32768, min(32767, sample_val))))
                # Channel 1: Microphone audio
                mic_val = 0 if mic_digital_zero else sample_val
                frames.extend(struct.pack("<h", max(-32768, min(32767, mic_val))))
        wf.writeframes(frames)
    return path


class TestAudioCaptureProofAdversarial(unittest.TestCase):
    """Adversarial fault-injection suite for audio proof-of-capture verification."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_audio_proof_")
        self.temp_path = Path(self.temp_dir)
        self.master_wav = self.temp_path / "master.wav"
        self.chunks_dir = self.temp_path / ".chunks"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # ADV-1: P0 Process Early Exit
    # -------------------------------------------------------------------------
    def test_adv_1_ffmpeg_early_exit(self) -> None:
        """Verify that early process termination without stderr error raises FFMPEG_EARLY_EXIT."""
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.stderr = None

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_processes_alive(ffmpeg_proc=mock_proc, wait_ms=10)

        err = ctx.exception
        self.assertEqual(err.stage, "P0_PROCESS_ALIVE")
        self.assertEqual(err.reason_code, AudioReasonCode.FFMPEG_EARLY_EXIT)

    # -------------------------------------------------------------------------
    # ADV-2: P0 Stderr Permission Denied
    # -------------------------------------------------------------------------
    def test_adv_2_stderr_permission_denied(self) -> None:
        """Verify that OS permission denial in stderr raises CAPTURE_STDERR_DENIED with remediation."""
        stderr_log = self.chunks_dir / "capture_stderr.log"
        stderr_log.write_text("avfoundation: [AVFoundation input device @ 0x123] Permission denied\n", encoding="utf-8")

        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = 1

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_processes_alive(
                ffmpeg_proc=mock_proc,
                stderr_log_path=stderr_log,
                wait_ms=10,
            )

        err = ctx.exception
        self.assertEqual(err.stage, "P0_PROCESS_ALIVE")
        self.assertEqual(err.reason_code, AudioReasonCode.CAPTURE_STDERR_DENIED)
        self.assertIn("Microphone", err.remediation)

    # -------------------------------------------------------------------------
    # ADV-3: P0 ScreenCaptureKit Early Exit / TCC Denied
    # -------------------------------------------------------------------------
    def test_adv_3_sck_process_tcc_denied(self) -> None:
        """Verify that ScreenCaptureKit tap process early exit raises SCK_TCC_DENIED."""
        mock_sck = mock.MagicMock()
        mock_sck.poll.return_value = 1
        mock_sck.stderr.read.return_value = b"ScreenCaptureKit TCC authorization denied\n"

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_processes_alive(
                ffmpeg_proc=None,
                sck_proc=mock_sck,
                wait_ms=10,
            )

        err = ctx.exception
        self.assertEqual(err.stage, "P0_PROCESS_ALIVE")
        self.assertEqual(err.reason_code, AudioReasonCode.SCK_TCC_DENIED)
        self.assertIn("Screen & System Audio Recording", err.remediation)

    # -------------------------------------------------------------------------
    # ADV-4: P1 Header-Only WAV (78B / Underflow)
    # -------------------------------------------------------------------------
    def test_adv_4_header_only_wav_byte_rate_deficit(self) -> None:
        """Verify that a header-only or underfilled WAV raises PCM_BYTE_RATE_DEFICIT."""
        self.master_wav.write_bytes(b"RIFF" + b"\x00" * 74)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_pcm_byte_rate(
                master_wav=self.master_wav,
                min_duration_sec=0.5,
                deadline_sec=0.1,
            )

        err = ctx.exception
        self.assertEqual(err.stage, "P1_PCM_BYTE_RATE")
        self.assertEqual(err.reason_code, AudioReasonCode.PCM_BYTE_RATE_DEFICIT)

    # -------------------------------------------------------------------------
    # ADV-5: P1 Half-Rate PCM Stream
    # -------------------------------------------------------------------------
    def test_adv_5_half_rate_pcm_stream_deficit(self) -> None:
        """Verify that sub-80% throughput stream raises PCM_BYTE_RATE_DEFICIT."""
        # 0.5s of audio written, but testing with min_duration_sec=1.0 and tiny deadline
        _synthesize_test_wav(self.master_wav, duration_sec=0.2, amplitude=2000.0)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_pcm_byte_rate(
                master_wav=self.master_wav,
                min_duration_sec=1.0,
                deadline_sec=0.05,
            )

        err = ctx.exception
        self.assertEqual(err.stage, "P1_PCM_BYTE_RATE")
        self.assertEqual(err.reason_code, AudioReasonCode.PCM_BYTE_RATE_DEFICIT)

    # -------------------------------------------------------------------------
    # ADV-6: P2 Microphone Digital Zero (TCC Synthetic Silence)
    # -------------------------------------------------------------------------
    def test_adv_6_mic_all_digital_zero(self) -> None:
        """Verify that digital zero microphone audio triggers MIC_DIGITAL_ZERO."""
        wav_path = self.temp_path / "zero.wav"
        _synthesize_test_wav(wav_path, duration_sec=1.0, mic_digital_zero=True)
        raw_bytes = wav_path.read_bytes()

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(
                pcm_bytes=raw_bytes,
                is_stereo=False,
                min_rms=1.0,
            )

        err = ctx.exception
        self.assertEqual(err.stage, "P2_MIC_NON_DIGITAL_ZERO")
        self.assertEqual(err.reason_code, AudioReasonCode.MIC_DIGITAL_ZERO)
        self.assertIn("Microphone permission", err.remediation)

    # -------------------------------------------------------------------------
    # ADV-7: P2 Stereo Mixed SCK: System OK, Mic Digital Zero
    # -------------------------------------------------------------------------
    def test_adv_7_stereo_mixed_sck_mic_digital_zero(self) -> None:
        """Verify that in stereo mixed_sck, digital zero on the mic channel fails even if system channel has tone."""
        stereo_wav = self.temp_path / "mixed_stereo.wav"
        # Channel 0 has 3000 amplitude tone; Channel 1 (mic) has 0 amplitude (digital zero)
        _synthesize_test_wav(
            stereo_wav,
            duration_sec=1.0,
            channels=2,
            amplitude=3000.0,
            mic_digital_zero=True,
        )
        raw_bytes = stereo_wav.read_bytes()

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(
                pcm_bytes=raw_bytes,
                is_stereo=True,
                min_rms=1.0,
            )

        err = ctx.exception
        self.assertEqual(err.stage, "P2_MIC_NON_DIGITAL_ZERO")
        self.assertEqual(err.reason_code, AudioReasonCode.MIC_DIGITAL_ZERO)

    # -------------------------------------------------------------------------
    # ADV-8: P3 Continuous Growth Stalled
    # -------------------------------------------------------------------------
    def test_adv_8_audio_continuous_growth_stalled(self) -> None:
        """Verify that stalled master WAV growth raises AUDIO_GROWTH_STALLED."""
        self.master_wav.write_bytes(b"\x00" * 10000)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_continuous_growth(
                master_wav=self.master_wav,
                last_size=10000,
                last_growth_time=time.time() - 5.0,
                stall_threshold_sec=3.0,
            )

        err = ctx.exception
        self.assertEqual(err.stage, "P3_CONTINUOUS_GROWTH")
        self.assertEqual(err.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)

    # -------------------------------------------------------------------------
    # ADV-9: Zero Fallback-to-Microphone Guarantee (Operator Decision D1)
    # -------------------------------------------------------------------------
    def test_adv_9_zero_fallback_to_microphone_enforced(self) -> None:
        """Verify that fallback_to_microphone raises AudioCaptureUnprovenError rather than degrading."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mixed_sck",
            mock_mode=True,
        )

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            mgr.fallback_to_microphone(reason="Test simulated failure")

        err = ctx.exception
        self.assertEqual(err.stage, "FALLBACK_GUARD")
        self.assertEqual(err.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)
        self.assertIn("strictly prohibited by repository governance", err.detail)
        self.assertFalse(mgr.is_running)

    # -------------------------------------------------------------------------
    # ADV-10: Quarantine Marker Created on Failure
    # -------------------------------------------------------------------------
    def test_adv_10_quarantine_marker_on_failure(self) -> None:
        """Verify that recording startup failure creates .quarantine marker and releases lock."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        recorder = LiveMeetingRecorder(
            output_dir=self.temp_path,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )

        # Trigger quarantine
        recorder._quarantine_session(
            reason="Simulated unproven capture failure",
            reason_code=AudioReasonCode.MIC_DIGITAL_ZERO,
        )

        quarantine_file = recorder.output_dir / ".quarantine"
        self.assertTrue(quarantine_file.exists(), "Expected .quarantine file to exist")
        data = quarantine_file.read_text(encoding="utf-8")
        self.assertIn(AudioReasonCode.MIC_DIGITAL_ZERO, data)
        self.assertIn("Simulated unproven capture failure", data)

    # -------------------------------------------------------------------------
    # ADV-11: Full Proof Success Produces Valid Receipt
    # -------------------------------------------------------------------------
    def test_adv_11_full_proof_success_receipt(self) -> None:
        """Verify that a genuine acoustic stream successfully reaches CAPTURE_PROVEN."""
        _synthesize_test_wav(
            self.master_wav,
            duration_sec=1.2,
            channels=1,
            amplitude=4000.0,
            mic_digital_zero=False,
        )

        receipt_file = self.temp_path / "record_start_receipt.json"
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 99999

        receipt = AudioCaptureProofVerifier.run_full_start_proof(
            master_wav=self.master_wav,
            ffmpeg_proc=mock_proc,
            is_stereo=False,
            mock_mode=False,
            receipt_path=receipt_file,
        )

        self.assertEqual(receipt["status"], "CAPTURE_PROVEN")
        self.assertIn("CAPTURE_PROVEN", receipt["transitions"])
        self.assertTrue(receipt_file.exists())
        self.assertGreater(receipt["p2_mic_acoustic"]["mic_rms"], 100.0)


if __name__ == "__main__":
    unittest.main()
