"""
tests/adversarial/test_live_audio_starvation_adversarial.py

Adversarial test suite covering audio starvation detection, BLE device index contention,
and starvation vs natural pause discrimination.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioStarvationDetector,
)
from dual_pipeline.transcript_stages_core.silence_gap_classifier import classify_silence_gaps
from pipeline_a_intake.mac_system_audio import (
    AudioDeviceInfo,
    AudioDeviceType,
    CoreAudioDeviceManager,
)


class TestLiveAudioStarvationAdversarial(unittest.TestCase):
    def test_ble_find_my_index_zero_contention_adversarial(self) -> None:
        """Adversarial scenario: BLE beacon takes index [0] in AVFoundation.

        System must reject [0] and resolve physical microphone at [1].
        """
        ble_find_my = AudioDeviceInfo(
            device_id="0",
            name="Amit’s AirPods Pro - Find My",
            device_type=AudioDeviceType.UNKNOWN,
            input_channels=1,
            avfoundation_index=0,
            is_default_input=False,
        )
        macbook_mic = AudioDeviceInfo(
            device_id="1",
            name="MacBook Pro Microphone",
            device_type=AudioDeviceType.INPUT,
            input_channels=1,
            transport="builtin",
            avfoundation_index=1,
            is_default_input=True,
        )

        with patch.object(CoreAudioDeviceManager, "list_devices", return_value=[ble_find_my, macbook_mic]):
            chosen = CoreAudioDeviceManager.get_default_input_device()
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen.name, "MacBook Pro Microphone")
            self.assertEqual(chosen.avfoundation_index, 1)
            self.assertEqual(CoreAudioDeviceManager.get_default_input_avfoundation_index(), ":1")

    def test_audio_starvation_detector_sustained_silence(self) -> None:
        """Adversarial scenario: Audio drops to RMS < 0.005 for > 10s.

        Detector must transition to AUDIO_STARVATION_PAUSE.
        """
        detector = AudioStarvationDetector(threshold_rms=0.005, starvation_seconds=10.0)
        self.assertFalse(detector.is_starved)

        # 5 seconds of silence -> still active probation
        status = detector.record_chunk(rms=0.001, duration_seconds=5.0)
        self.assertEqual(status, "ACTIVE")
        self.assertFalse(detector.is_starved)

        # Another 6 seconds of silence -> exceeds 10s -> starvation tripped
        status = detector.record_chunk(rms=0.0005, duration_seconds=6.0)
        self.assertEqual(status, "AUDIO_STARVATION_PAUSE")
        self.assertTrue(detector.is_starved)

        # Speech resumes -> starvation cleared
        status = detector.record_chunk(rms=0.08, duration_seconds=5.0)
        self.assertEqual(status, "ACTIVE")
        self.assertFalse(detector.is_starved)

    def test_silence_gap_classifier_prohibits_natural_pause_on_long_starvation(self) -> None:
        """Adversarial scenario: 23-minute silence gap (1376s) must NOT be absorbed as a natural bio break."""
        text = (
            "# Meeting Transcript\n\n"
            "**Rose Teame**: Welcome everyone.\n\n"
            "[Recording gap: 00:10:04 – 00:33:00 (Audio starvation / dropped stream)]\n\n"
            "**Amit Ayer**: Thanks Rose."
        )
        metadata = {
            "platform": "Webex",
            "provider": "offline",
            "allow_autonomous_gaps": True,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = HITLDecisionStore(
                ledger_path=Path(tmp_dir) / "hitl_decision_ledger.json",
                sqlite_path=Path(tmp_dir) / "hitl_decision_ledger.sqlite",
            )
            updated_text, meta_out = classify_silence_gaps(text, metadata, store=store)
            resolutions = meta_out.get("hitl_resolutions", {})
            gap_res = resolutions.get("gap_001004", {})

            # Gap must be classified as RECORDING_GAP_INTERRUPTION, never NATURAL_SESSION_PAUSE
            self.assertIn("RECORDING_GAP_INTERRUPTION", gap_res.get("resolution", ""))
            self.assertNotIn("NATURAL_SESSION_PAUSE", gap_res.get("resolution", ""))
            self.assertIn("Recording gap: 00:10:04 – 00:33:00", updated_text)


if __name__ == "__main__":
    unittest.main()
