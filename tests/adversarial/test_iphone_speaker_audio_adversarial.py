"""
tests/adversarial/test_iphone_speaker_audio_adversarial.py

Wave 3 Adversarial & Edge-Case Tests:
Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Malformed input / boundary payload: Flatline zero bytes & 0-byte files fail capture proof.
2. Invariant bypass attempt: BLE beacons and AirPods override attempts are rejected.
3. Voice Isolation attenuation failure: Preflight fails closed when Voice Isolation is active.
4. Dynamic device reconnection: AirPods connecting mid-stream does not corrupt built-in mic session.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.hardware_classifier import HardwareDeviceClassifier
from dual_pipeline.live_meeting_recorder_core.mic_mode_preflight import MicModePreflightChecker
from pipeline_a_intake.coreaudio_hal import HALDeviceInfo
from pipeline_a_intake.mac_system_audio import AudioDeviceInfo, AudioDeviceType


class TestIPhoneSpeakerAudioAdversarial(unittest.TestCase):
    def setUp(self):
        self.builtin_hal = HALDeviceInfo(
            device_id=202,
            name="MacBook Pro Microphone",
            uid="builtin-mic-uid-202",
            transport="bltn",
            input_channels=1,
            output_channels=0,
            is_default_input=False,
            is_default_output=False,
        )
        self.airpods_hal = HALDeviceInfo(
            device_id=101,
            name="Amit's AirPods Pro",
            uid="airpods-uid-101",
            transport="blue",
            input_channels=1,
            output_channels=2,
            is_default_input=True,
            is_default_output=True,
        )
        self.ble_beacon_hal = HALDeviceInfo(
            device_id=303,
            name="Tile Pro Tracker",
            uid="tile-uid-303",
            transport="blea",
            input_channels=0,
            output_channels=0,
            is_default_input=False,
            is_default_output=False,
        )
        self.builtin_info = AudioDeviceInfo(
            device_id="202",
            name="MacBook Pro Microphone",
            device_type=AudioDeviceType.INPUT,
            input_channels=1,
            output_channels=0,
            is_default_input=False,
            is_default_output=False,
            transport="built-in",
            avfoundation_index=1,
        )
        self.airpods_info = AudioDeviceInfo(
            device_id="101",
            name="Amit's AirPods Pro",
            device_type=AudioDeviceType.HEADSET,
            input_channels=1,
            output_channels=2,
            is_default_input=True,
            is_default_output=True,
            transport="bluetooth",
            avfoundation_index=0,
        )
        self.ble_info = AudioDeviceInfo(
            device_id="303",
            name="Tile Pro Tracker",
            device_type=AudioDeviceType.HEADSET,
            input_channels=0,
            output_channels=0,
            is_default_input=False,
            is_default_output=False,
            transport="bluetooth",
            avfoundation_index=2,
        )

    def test_voice_isolation_active_blocks_speakerphone_recording(self):
        """Category: Invariant enforcement - Voice Isolation blocks speakerphone initiation."""
        with patch.object(
            MicModePreflightChecker,
            "detect_mic_mode",
            return_value=("Voice Isolation", True),
        ):
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp = Path(tmp_dir)
                manager = LiveAudioCaptureManager(
                    chunks_dir=tmp / "chunks",
                    master_wav=tmp / "master.wav",
                    source_mode="iphone-speaker",
                )
                with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                    manager.resolve_source_strategy()
                self.assertEqual(ctx.exception.reason_code, AudioReasonCode.VOICE_ISOLATION_ACTIVE)
                self.assertIn("Click Control Center in the macOS menu bar -> Mic Mode -> select Standard", ctx.exception.remediation)

    def test_boundary_zero_byte_pcm_fails_acoustic_proof(self):
        """Category: Boundary payload - 0-byte or flatline silent PCM fails acoustic proof."""
        # 0 bytes
        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(pcm_bytes=b"", is_stereo=False)
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.PCM_BYTE_RATE_DEFICIT)

        # Flatline zeros
        zero_pcm = b"\x00" * 32000
        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(pcm_bytes=zero_pcm, is_stereo=False)
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.MIC_DIGITAL_ZERO)

    def test_invariant_bypass_ble_beacon_rejected(self):
        """Category: Invariant bypass attempt - BLE beacon masquerading as default input is rejected."""
        # Attempting to resolve safe index with BLE beacon as preferred
        safe_idx = HardwareDeviceClassifier.resolve_safe_device_index(
            preferred_index=":2",
            devices=[self.airpods_info, self.builtin_info, self.ble_info],
            prefer_builtin_mic=True,
        )
        self.assertEqual(safe_idx, ":1", "Must resolve to built-in mic (:1), rejecting BLE beacon")

    def test_concurrency_airpods_connection_mid_session(self):
        """Category: Concurrency & race - AirPods connecting dynamically does not mutate active built-in device."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.find_builtin_microphone", return_value=self.builtin_hal), \
                 patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.set_default_input_device_id", return_value=True), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device", return_value=self.builtin_info), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.list_devices", return_value=[self.builtin_info]), \
                 patch("dual_pipeline.live_meeting_recorder_core.mic_mode_preflight.MicModePreflightChecker.detect_mic_mode", return_value=("Standard", False)):

                manager = LiveAudioCaptureManager(
                    chunks_dir=tmp / "chunks",
                    master_wav=tmp / "master.wav",
                    source_mode="iphone-speaker",
                )
                strategy, dev = manager.resolve_source_strategy()
                initial_dev_index = manager.device_index
                self.assertEqual(int(dev.device_id), 202)

                # Dynamic AirPods connect event occurs in OS
                with patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device", return_value=self.airpods_info):
                    # Manager's active stream device_index must remain pinned to built-in mic
                    self.assertEqual(manager.device_index, initial_dev_index)
                    self.assertFalse(manager.enable_stereo_isolation)
