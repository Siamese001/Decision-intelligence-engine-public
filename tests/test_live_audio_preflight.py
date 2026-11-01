"""
tests/test_live_audio_preflight.py

Unit tests for Wave 1: AudioPreflightValidator and safe strategy resolution.
Verifies:
1. ScreenCaptureKit TCC preflight permission validation.
2. Safe strategy resolution fallback to AVFoundation microphone (:0).
3. LiveAudioCaptureManager resolution under TCC denied vs granted.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from pipeline_a_intake.mac_system_audio import AudioDeviceInfo, AudioDeviceType


class TestLiveAudioPreflight(unittest.TestCase):
    def test_preflight_sck_tcc_permission_probe_fallback(self):
        """Validates that non-existent binary or missing TCC returns False."""
        self.assertFalse(AudioPreflightValidator.check_sck_tcc_permission(Path("/tmp/nonexistent_bin_12345")))

    def test_preflight_resolve_safe_strategy_downgrades_sck_without_tcc(self):
        """Asserts that screen_capture_kit downgrades to microphone when TCC permission is denied."""
        with patch.object(AudioPreflightValidator, "validate_avfoundation_index", return_value=True):
            safe_strategy, safe_idx = AudioPreflightValidator.resolve_safe_strategy(
                requested_strategy="screen_capture_kit",
                sck_available=False,
                ffmpeg_bin="ffmpeg",
                preferred_mic_index=":0",
            )
            self.assertEqual(safe_strategy, "microphone")
            self.assertEqual(safe_idx, ":0")

    def test_preflight_resolve_safe_strategy_downgrades_mixed_without_tcc(self):
        """Asserts that mixed_sck downgrades to microphone when TCC permission is denied."""
        with patch.object(AudioPreflightValidator, "validate_avfoundation_index", return_value=True):
            safe_strategy, safe_idx = AudioPreflightValidator.resolve_safe_strategy(
                requested_strategy="mixed_sck",
                sck_available=False,
                ffmpeg_bin="ffmpeg",
                preferred_mic_index=":1",
            )
            self.assertEqual(safe_strategy, "microphone")

    def test_preflight_resolve_safe_strategy_preserves_authorized_sck(self):
        """Asserts that screen_capture_kit is preserved when TCC permission is granted."""
        safe_strategy, safe_idx = AudioPreflightValidator.resolve_safe_strategy(
            requested_strategy="screen_capture_kit",
            sck_available=True,
            ffmpeg_bin="ffmpeg",
            preferred_mic_index=":0",
        )
        self.assertEqual(safe_strategy, "screen_capture_kit")
        self.assertEqual(safe_idx, ":0")

    def test_capture_manager_auto_mode_falls_back_to_mic_when_tcc_denied(self):
        """Asserts that LiveAudioCaptureManager in 'auto' mode resolves to microphone when TCC is denied."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mgr = LiveAudioCaptureManager(
                chunks_dir=tmp_path / "chunks",
                master_wav=tmp_path / "master.wav",
                source_mode="auto",
            )

            mock_mic = AudioDeviceInfo(
                device_id="1",
                name="MacBook Pro Microphone",
                device_type=AudioDeviceType.INPUT,
                input_channels=1,
                output_channels=0,
                sample_rate=48000,
                is_default_input=True,
                is_default_output=False,
                transport="builtin",
                avfoundation_index=0,
            )

            with patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device", return_value=mock_mic), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_headphones_connected", return_value=False), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_system_muted", return_value=False), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]), \
                 patch.object(AudioPreflightValidator, "check_sck_tcc_permission", return_value=False):

                with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                    mgr.resolve_source_strategy()
                self.assertEqual(ctx.exception.reason_code, AudioReasonCode.SCK_TAP_FAILED)

    def test_capture_manager_system_mode_falls_back_to_mic_when_tcc_denied(self):
        """Asserts that LiveAudioCaptureManager in 'system' mode fails closed without silent fallback when TCC is denied (Operator Decision D1)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mgr = LiveAudioCaptureManager(
                chunks_dir=tmp_path / "chunks",
                master_wav=tmp_path / "master.wav",
                source_mode="system",
            )

            mock_mic = AudioDeviceInfo(
                device_id="1",
                name="MacBook Pro Microphone",
                device_type=AudioDeviceType.INPUT,
                input_channels=1,
                output_channels=0,
                sample_rate=48000,
                is_default_input=True,
                is_default_output=False,
                transport="builtin",
                avfoundation_index=0,
            )

            with patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device", return_value=mock_mic), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]), \
                 patch.object(AudioPreflightValidator, "check_sck_tcc_permission", return_value=False):

                with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                    mgr.resolve_source_strategy()
                self.assertEqual(ctx.exception.reason_code, AudioReasonCode.SCK_TAP_FAILED)


if __name__ == "__main__":
    unittest.main()
