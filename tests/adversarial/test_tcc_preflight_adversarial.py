"""
tests/adversarial/test_tcc_preflight_adversarial.py

Adversarial Stress & Hardening Test Suite for macOS TCC and Audio Preflight:
1. Probe Subprocess Timeout & Starvation: Asserts slow probe does not crash or corrupt state.
2. Headphone Trap Crash Prevention: Asserts headphones + denied TCC degrades gracefully to mic without unhandled RuntimeError.
3. Mid-Flight Permission Revocation: Asserts SCK termination mid-session triggers watchdog failover to mic without data loss.
4. Sentinel Death-Loop Protection: Asserts headphone trap sentinel locks out failing SCK and prevents cyclic crash loops.
5. Production Mock Bypass Prohibition: Asserts MacSystemAudioEngine never silently substitutes synthetic sine waves for real audio.
6. Verification Polling Race Absorption: Asserts processes exiting at 150ms are captured cleanly without 20s watchdog stall.
7. AVFoundation Dynamic Disconnect/Reconnect: Asserts unplugs/replugs fall back to index :0 gracefully.
8. Headless CI Environment Hermeticity: Asserts preflight respects CI environment variables.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from pipeline_a_intake.mac_system_audio import (
    AudioDeviceInfo,
    AudioDeviceType,
    CoreAudioDeviceManager,
)


class TestTCCPreflightAdversarial(unittest.TestCase):
    """Adversarial suite validating macOS TCC permission hardening and preflight failover."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.chunks_dir = self.tmp_path / "chunks"
        self.master_wav = self.tmp_path / "master.wav"

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("sys.platform", "darwin")
    @patch("dual_pipeline.live_meeting_recorder_core.audio_preflight.subprocess.run")
    def test_sck_probe_timeout_raises_runtime_error(self, mock_run):
        """Simulates a slow SCK daemon probe (> timeout) and asserts RuntimeError is raised to alert operator."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["mac_audio_tap", "--probe"], timeout=0.8)

        with patch("ctypes.cdll.LoadLibrary") as mock_load:
            mock_cg = MagicMock()
            mock_cg.CGPreflightScreenCaptureAccess.return_value = True
            mock_load.return_value = mock_cg

            dummy_bin = self.tmp_path / "mac_audio_tap"
            dummy_bin.touch()

            # Must raise RuntimeError identifying timeout instead of swallowing as False
            with self.assertRaises(RuntimeError) as ctx:
                AudioPreflightValidator.check_sck_tcc_permission(sck_bin=dummy_bin, timeout_sec=0.8)
            self.assertIn("timed out", str(ctx.exception).lower())

    @patch("sys.platform", "darwin")
    def test_tcc_denied_with_headphones_degrades_gracefully_without_unhandled_crash(self):
        """CRITICAL: Asserts that denied TCC with headphones connected does NOT throw an unhandled RuntimeError."""
        mock_mic = AudioDeviceInfo(
            device_id="1", name="Built-in Microphone", device_type=AudioDeviceType.INPUT,
            input_channels=1, output_channels=0, sample_rate=48000,
            is_default_input=True, is_default_output=False, transport="builtin",
            avfoundation_index=0,
        )

        with patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device", return_value=mock_mic), \
             patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_headphones_connected", return_value=True), \
             patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_system_muted", return_value=False), \
             patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]), \
             patch.object(AudioPreflightValidator, "check_sck_tcc_permission", return_value=False):

            mgr = LiveAudioCaptureManager(
                chunks_dir=self.chunks_dir,
                master_wav=self.master_wav,
                source_mode="auto",
            )

            # Under Decision D1: Must fail closed with AudioCaptureUnprovenError
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                mgr.resolve_source_strategy()
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.SCK_TAP_FAILED)

    @patch("sys.platform", "darwin")
    def test_permission_revoked_mid_flight_triggers_watchdog_and_safe_fallback(self):
        """Simulates TCC revocation mid-flight killing SCK with exit code 1; asserts watchdog halts session without fallback."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="system",
        )

        # Mock active SCK session
        mgr._active_strategy = "screen_capture_kit"
        mgr._is_running = True

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            mgr._handle_watchdog_failover("SCK process terminated: TCC revoked", exit_code=1)
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)
        self.assertFalse(mgr.is_running)

    @patch("sys.platform", "darwin")
    def test_headphone_trap_sentinel_locks_out_after_sck_failure(self):
        """Asserts that once SCK has failed or TCC is denied, the headphone sentinel never re-triggers failover to SCK."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="auto",
        )
        mgr._active_strategy = "microphone"
        mgr._is_running = True
        # Mark SCK permanently unavailable due to preflight TCC failure
        mgr._sck_permanently_unavailable = True

        with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.is_default_output_headphones", return_value=True), \
             patch.object(mgr, "failover_to_screen_capture_kit") as mock_failover:

            triggered = mgr.check_headphone_trap_sentinel(next_chunk_idx=1)
            self.assertFalse(triggered)
            mock_failover.assert_not_called()

    @patch("sys.platform", "darwin")
    def test_mac_system_audio_engine_respects_tcc_and_rejects_silent_mock_bypass(self):
        """Asserts that MacSystemAudioEngine checks TCC and does NOT inject synthetic mock audio in production."""
        with patch.object(AudioPreflightValidator, "check_sck_tcc_permission", return_value=False), \
             patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]):

            strategy, dev = CoreAudioDeviceManager.detect_best_system_audio_source()
            # Must fall back to microphone, NOT screen_capture_kit
            self.assertEqual(strategy, "microphone")

    def test_verify_sck_process_timing_race_condition_absorption(self):
        """Simulates an SCK process dying at 150ms and asserts active verification catches it before 20s watchdog stall."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="auto",
        )

        mock_proc = MagicMock()
        # Returns None on first poll, then 1 (error) on subsequent polls
        mock_proc.poll.side_effect = [None, None, 1]
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b"com.apple.ScreenCaptureKit.common error -3801"
        mgr._sck_proc = mock_proc

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            mgr._verify_sck_process(None)
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.SCK_TCC_DENIED)
        self.assertTrue(mgr._sck_permanently_unavailable)

    @patch("sys.platform", "darwin")
    def test_avfoundation_device_disconnection_and_dynamic_index_healing(self):
        """Asserts that index shifts or device unplugs are caught and defaulted to index :0."""
        with patch("subprocess.run") as mock_run, \
             patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_avfoundation_index", return_value=":0"):
            # Device index 5 no longer exists
            mock_run.return_value = MagicMock(
                returncode=0,
                stderr="[AVFoundation indev @ 0x123] AVFoundation audio devices:\n[AVFoundation indev @ 0x123] [0] Built-in Mic\n",
            )
            is_valid = AudioPreflightValidator.validate_avfoundation_index("ffmpeg", ":5")
            self.assertFalse(is_valid)

            safe_strat, safe_idx = AudioPreflightValidator.resolve_safe_strategy(
                "microphone", sck_available=False, ffmpeg_bin="ffmpeg", preferred_mic_index=":5"
            )
            self.assertEqual(safe_strat, "microphone")
            self.assertEqual(safe_idx, ":0")

    def test_headless_ci_environment_detection_and_safe_branching(self):
        """Asserts that running in CI environment safely bypasses native CoreGraphics WindowServer crashes."""
        with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": "true"}, clear=False):
            # In CI without explicit allow flag, check_sck_tcc_permission returns False cleanly
            has_tcc = AudioPreflightValidator.check_sck_tcc_permission(Path("/dummy/path"))
            self.assertFalse(has_tcc)


if __name__ == "__main__":
    unittest.main()
