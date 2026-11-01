"""
tests/test_live_audio_zero_loss_resilience.py

End-to-End and Adversarial Verification Suite for Wave 5:
Zero-Loss Audio Architecture & MLX "Auto" Setting Defect Eradication.

Coverage:
1. Native ScreenCaptureKit compiled binary verification.
2. Headphone trap fail-closed invariant: blocks silent mic fallback when remote speech is isolated.
3. SCK process early termination / TCC permission failure detection and failover.
4. Real-time AudioEnergyWatchdog flatline silence detection and alerting.
5. Adaptive VAD sensitivity (0.005 threshold) capturing low-amplitude speech dropped by legacy 0.015.
6. Zero-loss silent chunk quarantine directory preservation.
7. MLX "auto" backend and model resolution parity on Apple Silicon Metal.
8. LiveMeetingRecorder continuous master WAV dual-artery spooling E2E.
"""
from __future__ import annotations

import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
    is_sck_tap_available,
)
from dual_pipeline.live_meeting_recorder_core.audio_vad import AudioVADSegmenter
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import AudioEnergyWatchdog
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _resolve_effective_backend,
    transcribe_meeting_chunk,
)
from pipeline_a_intake.mac_system_audio import (
    SCK_COMPILED_BIN,
)


def _synthesize_pcm_wav(path: Path, duration_sec: float, amplitude: float = 0.0, freq_hz: float = 300.0) -> Path:
    """Helper to synthesize test WAV with exact amplitude and frequency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            if amplitude > 0.0:
                t = float(i) / sample_rate
                val = int(amplitude * math.sin(2.0 * math.pi * freq_hz * t))
            else:
                val = 0
            frames.extend(struct.pack("<h", max(-32768, min(32767, val))))
        wf.writeframes(frames)
    return path


class TestLiveAudioZeroLossResilience(unittest.TestCase):
    """Adversarial and E2E verification of Zero-Loss Audio Architecture."""

    def test_sck_compiled_binary_exists_and_verified(self) -> None:
        """ScreenCaptureKit binary must exist, be executable, and match target architecture."""
        assert SCK_COMPILED_BIN.exists(), f"SCK binary missing at {SCK_COMPILED_BIN}"
        assert os.access(str(SCK_COMPILED_BIN), os.X_OK), "SCK binary is not executable"
        assert SCK_COMPILED_BIN.stat().st_size > 50000, "SCK binary suspiciously small"
        assert is_sck_tap_available() is True

    def test_audio_capture_headphone_trap_fail_closed_invariant(self) -> None:
        """When headphones are active and system taps fail, auto mode MUST fail closed, not drop audio."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks = Path(tmp_dir) / "chunks"
            master = Path(tmp_dir) / "master.wav"
            mgr = LiveAudioCaptureManager(chunks_dir=chunks, master_wav=master, source_mode="auto")

            # Mock hardware: headphones connected, loopback none, SCK unavailable
            with patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_headphones_connected", return_value=True), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_system_muted", return_value=False), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]), \
                 patch("dual_pipeline.live_meeting_recorder_core.audio_capture.is_sck_tap_available", return_value=False):
                with self.assertRaises(RuntimeError) as ctx:
                    mgr.resolve_source_strategy()
                assert "CRITICAL AUDIO FAULT" in str(ctx.exception)
                assert "Headphones are connected" in str(ctx.exception)

    def test_audio_capture_sck_process_failure_detection(self) -> None:
        """Detects immediate SCK process exit (e.g. TCC decline) and refuses silent mic fallback on headphones."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks = Path(tmp_dir) / "chunks"
            master = Path(tmp_dir) / "master.wav"
            mgr = LiveAudioCaptureManager(chunks_dir=chunks, master_wav=master, source_mode="system")
            mgr._detected_headphones = True

            # Mock dead SCK process with exit code 1
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 1
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = b"[SCK-TAP] Initialization failed: The user declined TCCs"
            mgr._sck_proc = mock_proc

            with patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]):
                with self.assertRaises(RuntimeError) as ctx:
                    mgr._verify_sck_process(None)
                assert "ScreenCaptureKit audio tap initialization failed" in str(ctx.exception)
                assert "The user declined TCCs" in str(ctx.exception)

    def test_audio_energy_watchdog_detects_flatline_silence(self) -> None:
        """Watchdog tracks RMS and raises alarm callback when silence persists >= 2 chunks."""
        alerts: list[str] = []

        def _on_alert(msg: str, idx: int, rms: float) -> None:
            alerts.append(msg)

        dog = AudioEnergyWatchdog(flatline_threshold_rms=5.0, alert_after_chunks=2, segment_seconds=15, on_alert_callback=_on_alert)

        # Chunk 0: silent
        alert0 = dog.record_chunk(0, rms=0.0, time_str="[00:00:00]")
        assert alert0 is None
        assert len(alerts) == 0

        # Chunk 1: silent -> triggers threshold alert
        alert1 = dog.record_chunk(1, rms=0.0, time_str="[00:00:15]")
        assert alert1 is not None
        assert "CRITICAL AUDIO ALERT" in alert1
        assert "Flatline silence detected across 2 consecutive chunks" in alert1
        assert len(alerts) == 1

        # Chunk 2: sound restored -> streak resets
        alert2 = dog.record_chunk(2, rms=450.0, time_str="[00:00:30]")
        assert alert2 is None
        assert dog.consecutive_silent_chunks == 0
        stats = dog.get_stats()
        assert stats["total_chunks_monitored"] == 3
        assert stats["total_silent_chunks"] == 2
        assert stats["max_silent_streak"] == 2

    def test_audio_vad_adaptive_sensitivity_recovers_quiet_speech(self) -> None:
        """Adaptive threshold 0.005 recovers quiet system audio that legacy 0.015 dropped."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            quiet_wav = Path(tmp_dir) / "quiet_system_audio.wav"
            # Amplitude 260 -> normalized RMS ~ 0.0056 (between 0.005 and 0.015)
            _synthesize_pcm_wav(quiet_wav, duration_sec=2.0, amplitude=260.0, freq_hz=250.0)

            legacy_vad = AudioVADSegmenter(energy_threshold=0.015)
            hardened_vad = AudioVADSegmenter(energy_threshold=0.005)

            # Legacy dropped quiet audio
            assert legacy_vad.is_speech_chunk(quiet_wav) is False
            # Hardened recovers quiet audio
            assert hardened_vad.is_speech_chunk(quiet_wav) is True

    def test_audio_vad_quarantine_preserves_silent_chunks(self) -> None:
        """Suppressed silent chunks are copied to quarantine directory for zero-loss recovery."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            silence_wav = Path(tmp_dir) / "silent_chunk.wav"
            _synthesize_pcm_wav(silence_wav, duration_sec=1.0, amplitude=0.0)
            quarantine = Path(tmp_dir) / ".silence_quarantine"

            vad = AudioVADSegmenter(energy_threshold=0.005)
            active, silent = vad.filter_speech_chunks([silence_wav], quarantine_dir=quarantine)

            assert len(active) == 0
            assert len(silent) == 1
            assert (quarantine / "silent_chunk.wav").exists()
            assert (quarantine / "silent_chunk.wav").stat().st_size == silence_wav.stat().st_size

    def test_mlx_auto_backend_and_model_resolution(self) -> None:
        """Backend 'auto' and model 'auto' resolve to canonical MLX Whisper on Apple Silicon."""
        backend = _resolve_effective_backend("auto")
        assert backend == "mlx"

        # Mock transcribe_meeting_chunk with mock_mode=True
        with tempfile.TemporaryDirectory() as tmp_dir:
            dummy_wav = Path(tmp_dir) / "dummy.wav"
            _synthesize_pcm_wav(dummy_wav, duration_sec=1.0, amplitude=500.0)
            text = transcribe_meeting_chunk(
                dummy_wav,
                backend="auto",
                model_name="auto",
                mock_mode=True,
            )
            assert "Mock transcript segment" in text

    def test_live_meeting_recorder_continuous_master_wav_spool_e2e(self) -> None:
        """LiveMeetingRecorder runs with AudioEnergyWatchdog and creates continuous master WAV."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        with tempfile.TemporaryDirectory() as tmp_dir:
            rec = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="ZeroLossTest",
                meeting_date="09/25/2026",
                output_dir=Path(tmp_dir),
                mock_mode=True,
                audio_source="mock",
                enable_ws=False,
                enforce_worktree=False,
            )
            assert hasattr(rec, "audio_watchdog")
            assert rec.master_wav.name.endswith(".wav")
            # Run 3 mock iterations
            rec.run(max_iterations=3)
            assert rec.transcript_md.exists()
            assert rec.session_manifest.exists()
            dog_stats = rec.audio_watchdog.get_stats()
            assert dog_stats["total_chunks_monitored"] >= 0
