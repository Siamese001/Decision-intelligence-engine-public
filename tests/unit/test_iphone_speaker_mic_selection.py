"""
tests/unit/test_iphone_speaker_mic_selection.py

Wave 1 Verification Tests:
Verifies that built-in MacBook Pro Microphone is selected over AirPods (Bluetooth headset)
when capturing iPhone speakerphone audio, even if AirPods are configured as default input.
"""
from __future__ import annotations

import unittest
from pipeline_a_intake.coreaudio_hal import CoreAudioHAL, HALDeviceInfo
from pipeline_a_intake.mac_system_audio import AudioDeviceInfo, AudioDeviceType
from dual_pipeline.live_meeting_recorder_core.hardware_classifier import HardwareDeviceClassifier


class TestIPhoneSpeakerMicSelection(unittest.TestCase):
    """Test suite for built-in Mac Mic selection and AirPods override."""

    def setUp(self):
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
        self.airtag_hal = HALDeviceInfo(
            device_id=303,
            name="Find My Beacon",
            uid="airtag-uid-303",
            transport="blea",
            input_channels=0,
            output_channels=0,
            is_default_input=False,
            is_default_output=False,
        )

        self.airpods_dev = AudioDeviceInfo(
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
        self.builtin_dev = AudioDeviceInfo(
            device_id="202",
            name="MacBook Pro Microphone",
            device_type=AudioDeviceType.INPUT,
            input_channels=1,
            output_channels=0,
            is_default_input=False,
            is_default_output=False,
            transport="builtin",
            avfoundation_index=1,
        )
        self.airtag_dev = AudioDeviceInfo(
            device_id="303",
            name="Find My Locator",
            device_type=AudioDeviceType.UNKNOWN,
            input_channels=0,
            output_channels=0,
            is_default_input=False,
            is_default_output=False,
            transport="ble",
            avfoundation_index=2,
        )

    def tearDown(self):
        CoreAudioHAL.set_mock_devices(None)

    def test_coreaudio_hal_find_builtin_microphone(self):
        """CoreAudioHAL finds built-in MacBook microphone correctly."""
        CoreAudioHAL.set_mock_devices(
            devices=[self.airpods_hal, self.builtin_hal, self.airtag_hal],
            default_output=self.airpods_hal,
            default_input=self.airpods_hal,
        )
        mic = CoreAudioHAL.find_builtin_microphone()
        self.assertIsNotNone(mic)
        self.assertEqual(mic.device_id, 202)
        self.assertEqual(mic.name, "MacBook Pro Microphone")
        self.assertFalse(mic.is_headphones)
        self.assertEqual(mic.transport, "bltn")

    def test_coreaudio_hal_set_default_input_device(self):
        """CoreAudioHAL allows setting default input device."""
        CoreAudioHAL.set_mock_devices(
            devices=[self.airpods_hal, self.builtin_hal],
            default_input=self.airpods_hal,
        )
        self.assertEqual(CoreAudioHAL.get_default_input_device().device_id, 101)

        # Switch to built-in mic
        success = CoreAudioHAL.set_default_input_device(self.builtin_hal)
        self.assertTrue(success)
        self.assertEqual(CoreAudioHAL.get_default_input_device().device_id, 202)

    def test_hardware_classifier_prefers_builtin_mic_when_flagged(self):
        """HardwareDeviceClassifier selects built-in mic over AirPods when prefer_builtin_mic=True."""
        devices = [self.airpods_dev, self.builtin_dev, self.airtag_dev]

        # Normal selection follows default input (AirPods)
        normal_mic = HardwareDeviceClassifier.select_default_microphone(devices, prefer_builtin_mic=False)
        self.assertEqual(normal_mic.name, "Amit's AirPods Pro")

        # Speakerphone mode selection overrides AirPods to select MacBook Pro Microphone
        speaker_mic = HardwareDeviceClassifier.select_default_microphone(devices, prefer_builtin_mic=True)
        self.assertIsNotNone(speaker_mic)
        self.assertEqual(speaker_mic.name, "MacBook Pro Microphone")
        self.assertFalse(speaker_mic.is_headphones)
        self.assertEqual(speaker_mic.avfoundation_index, 1)

    def test_hardware_classifier_resolve_safe_device_index_overrides_airpods(self):
        """resolve_safe_device_index overrides preferred :0 (AirPods) when prefer_builtin_mic=True."""
        devices = [self.airpods_dev, self.builtin_dev, self.airtag_dev]

        # Default resolution targets :0
        idx_normal = HardwareDeviceClassifier.resolve_safe_device_index(
            preferred_index=":0", devices=devices, prefer_builtin_mic=False
        )
        self.assertEqual(idx_normal, ":0")

        # Speakerphone resolution overrides :0 to built-in mic index :1
        idx_speaker = HardwareDeviceClassifier.resolve_safe_device_index(
            preferred_index=":0", devices=devices, prefer_builtin_mic=True
        )
        self.assertEqual(idx_speaker, ":1")

    def test_hardware_classifier_rejects_ble_beacons_fail_closed(self):
        """resolve_safe_device_index rejects BLE beacons even in speakerphone mode."""
        # Setup device topology where :0 is BLE beacon
        ble_at_zero = AudioDeviceInfo(
            device_id="303",
            name="AirTag Locator",
            device_type=AudioDeviceType.UNKNOWN,
            input_channels=0,
            avfoundation_index=0,
        )
        devices = [ble_at_zero, self.builtin_dev]
        idx = HardwareDeviceClassifier.resolve_safe_device_index(
            preferred_index=":0", devices=devices, prefer_builtin_mic=True
        )
        self.assertEqual(idx, ":1")


if __name__ == "__main__":
    unittest.main()
