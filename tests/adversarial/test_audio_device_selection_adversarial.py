"""tests/adversarial/test_audio_device_selection_adversarial.py

Adversarial test suite covering AVFoundation audio device discovery, BLE index :0 rejection,
preflight acoustic signal probing, and sub-500ms hot-failover latency bounds.
"""
from __future__ import annotations

import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_preflight import (
    AudioPreflightValidator,
)
from dual_pipeline.live_meeting_recorder_core.hardware_classifier import (
    HardwareDeviceClassifier,
)
from pipeline_a_intake.mac_system_audio import (
    AudioDeviceInfo,
    AudioDeviceType,
    CoreAudioDeviceManager,
)


class TestAudioDeviceSelectionAdversarial(unittest.TestCase):
    """Adversarial test cases for hardware device classification, BLE trap rejection, and failover latency."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.chunks_dir = Path(self.temp_dir.name) / "chunks"
        self.master_wav = Path(self.temp_dir.name) / "master.wav"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

        self.ble_beacon = AudioDeviceInfo(
            device_id="0",
            name="Amit’s AirPods Pro - Find My",
            device_type=AudioDeviceType.UNKNOWN,
            input_channels=1,
            avfoundation_index=0,
            is_default_input=False,
        )
        self.macbook_mic = AudioDeviceInfo(
            device_id="1",
            name="MacBook Pro Microphone",
            device_type=AudioDeviceType.INPUT,
            input_channels=1,
            transport="builtin",
            avfoundation_index=1,
            is_default_input=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_test_wav(self, path: Path, amplitude: int = 1000, n_samples: int = 16000) -> None:
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            data = struct.pack(f"{n_samples}h", *[amplitude] * n_samples)
            w.writeframes(data)

    def test_ble_beacon_keywords_classification(self) -> None:
        """Adversarial scenario: Various BLE tracking devices attempt to masquerade as microphones."""
        adversarial_names = [
            "Amit’s AirPods Pro - Find My",
            "Living Room Tile Tracker",
            "Apple AirTag Locator 2026",
            "Bluetooth Beacon Endpoint",
            "AirPods - FindMy",
        ]
        for name in adversarial_names:
            self.assertTrue(
                HardwareDeviceClassifier.is_ble_or_non_streaming(name),
                f"Failed to identify BLE non-streaming device: {name}",
            )

        valid_names = [
            "MacBook Pro Microphone",
            "Built-in Microphone",
            "External USB Audio CODEC",
            "Jabra Evolve2 65",
            "Shure MV7",
        ]
        for name in valid_names:
            self.assertFalse(
                HardwareDeviceClassifier.is_ble_or_non_streaming(name),
                f"Legitimate microphone falsely flagged as BLE: {name}",
            )

    def test_ble_find_my_index_zero_rejected_built_in_selected(self) -> None:
        """Adversarial scenario: BLE beacon occupies index [0] in AVFoundation.

        LiveAudioCaptureManager and HardwareDeviceClassifier must explicitly reject :0
        and automatically bind to index :1 (MacBook Pro Microphone).
        """
        devices = [self.ble_beacon, self.macbook_mic]
        with patch.object(CoreAudioDeviceManager, "list_devices", return_value=devices):
            best_mic = HardwareDeviceClassifier.select_default_microphone(devices, preferred_index=":0")
            self.assertIsNotNone(best_mic)
            self.assertEqual(best_mic.name, "MacBook Pro Microphone")
            self.assertEqual(best_mic.avfoundation_index, 1)

            safe_idx = HardwareDeviceClassifier.resolve_safe_device_index(preferred_index=":0", devices=devices)
            self.assertEqual(safe_idx, ":1")

            manager = LiveAudioCaptureManager(
                chunks_dir=self.chunks_dir,
                master_wav=self.master_wav,
                source_mode="mic",
                device_index=":0",
                mock_mode=True,
            )
            self.assertEqual(manager.device_index, ":1")

            strategy, dev = manager.resolve_source_strategy()
            self.assertEqual(strategy, "mock")
            self.assertEqual(manager.device_index, ":1")

    def test_adversarial_corrupted_ffmpeg_device_list(self) -> None:
        """Adversarial scenario: FFmpeg outputs malformed, truncated, or empty device listings."""
        corrupted_stderr_1 = "Some random error\nNo audio devices found"
        self.assertFalse(
            AudioPreflightValidator.validate_avfoundation_index(
                "ffmpeg", ":0", stderr_text=corrupted_stderr_1
            )
        )
        self.assertFalse(
            AudioPreflightValidator.validate_avfoundation_index(
                "ffmpeg", ":1", stderr_text=corrupted_stderr_1
            )
        )

        corrupted_stderr_2 = (
            "[AVFoundation audio devices:]\n"
            "[0] Amit’s AirPods Pro - Find My\n"
            "[1] MacBook Pro Microphone\n"
        )
        # :0 is a BLE beacon -> must be False
        self.assertFalse(
            AudioPreflightValidator.validate_avfoundation_index(
                "ffmpeg", ":0", stderr_text=corrupted_stderr_2
            )
        )
        # :1 is MacBook Pro Mic -> must be True
        self.assertTrue(
            AudioPreflightValidator.validate_avfoundation_index(
                "ffmpeg", ":1", stderr_text=corrupted_stderr_2
            )
        )
        # :2 does not exist -> must be False
        self.assertFalse(
            AudioPreflightValidator.validate_avfoundation_index(
                "ffmpeg", ":2", stderr_text=corrupted_stderr_2
            )
        )

        parsed = HardwareDeviceClassifier.parse_avfoundation_device_lines(corrupted_stderr_2.splitlines())
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0], (0, "Amit’s AirPods Pro - Find My", False))
        self.assertEqual(parsed[1], (1, "MacBook Pro Microphone", True))

    def test_audio_preflight_acoustic_signal_probe_energy(self) -> None:
        """Adversarial scenario: Acoustic probe evaluates real signal vs flatline silence."""
        active_wav = Path(self.temp_dir.name) / "active_probe.wav"
        self._create_test_wav(active_wav, amplitude=2500)

        silent_wav = Path(self.temp_dir.name) / "silent_probe.wav"
        self._create_test_wav(silent_wav, amplitude=0)

        # Active file probe
        has_signal, rms = AudioPreflightValidator.probe_acoustic_signal(active_wav, min_rms=100.0)
        self.assertTrue(has_signal)
        self.assertGreater(rms, 2000.0)

        # Silent file probe
        has_signal_silent, rms_silent = AudioPreflightValidator.probe_acoustic_signal(silent_wav, min_rms=10.0)
        self.assertFalse(has_signal_silent)
        self.assertEqual(rms_silent, 0.0)

        # Probe non-existent file
        has_signal_none, rms_none = AudioPreflightValidator.probe_acoustic_signal(
            Path(self.temp_dir.name) / "non_existent.wav"
        )
        self.assertFalse(has_signal_none)
        self.assertEqual(rms_none, 0.0)

    def test_probe_acoustic_signal_rejects_ble_device_index(self) -> None:
        """Adversarial scenario: Probing device :0 when :0 is a BLE beacon aborts immediately without hanging."""
        with patch.object(
            AudioPreflightValidator,
            "validate_avfoundation_index",
            side_effect=lambda bin_path, idx, **kw: idx not in (":0", "0"),
        ):
            has_signal, rms = AudioPreflightValidator.probe_acoustic_signal(":0")
            self.assertFalse(has_signal)
            self.assertEqual(rms, 0.0)

    def test_hot_failover_latency_under_500ms(self) -> None:
        """Adversarial benchmark: Audio stream starvation triggers hot-failover to physical microphone.

        Must complete within <= 500ms without blocking or hanging.
        """
        devices = [self.ble_beacon, self.macbook_mic]
        with patch.object(CoreAudioDeviceManager, "list_devices", return_value=devices):
            manager = LiveAudioCaptureManager(
                chunks_dir=self.chunks_dir,
                master_wav=self.master_wav,
                source_mode="system",
                device_index=":0",
                mock_mode=True,
            )
            manager.start()

            # Attach mock process to simulate active stream
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.pid = 99999
            manager._ffmpeg_proc = mock_proc

            t0 = time.perf_counter()
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                manager.fallback_to_microphone(reason="Adversarial Starvation Drop")
            elapsed_sec = time.perf_counter() - t0

            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)
            self.assertLessEqual(
                elapsed_sec,
                0.200,
                f"Fail-closed latency exceeded bound! Took {elapsed_sec * 1000.0:.2f}ms",
            )
            self.assertFalse(manager.is_running)
            self.assertTrue(manager._sck_permanently_unavailable)
            manager.stop()

    def test_concurrent_failover_and_sentinel_no_deadlock(self) -> None:
        """Adversarial stress: Simultaneous calls to fallback_to_microphone and sentinel do not deadlock."""
        devices = [self.ble_beacon, self.macbook_mic]
        with patch.object(CoreAudioDeviceManager, "list_devices", return_value=devices):
            manager = LiveAudioCaptureManager(
                chunks_dir=self.chunks_dir,
                master_wav=self.master_wav,
                source_mode="mic",
                device_index=":1",
                mock_mode=True,
            )
            manager.start()

            exceptions: list[Exception] = []

            def worker_failover():
                try:
                    for _ in range(5):
                        try:
                            manager.fallback_to_microphone(reason="Worker thread stress")
                        except AudioCaptureUnprovenError:
                            pass
                        time.sleep(0.01)
                except Exception as exc:
                    exceptions.append(exc)

            def worker_sentinel():
                try:
                    for _ in range(5):
                        manager.check_headphone_trap_sentinel()
                        time.sleep(0.01)
                except Exception as exc:
                    exceptions.append(exc)

            threads = [
                threading.Thread(target=worker_failover),
                threading.Thread(target=worker_sentinel),
                threading.Thread(target=worker_failover),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=3.0)
                self.assertFalse(t.is_alive(), "Worker thread deadlocked during concurrent failover!")

            self.assertEqual(len(exceptions), 0, f"Thread exceptions occurred: {exceptions}")
            manager.stop()

    def test_resolve_safe_strategy_sck_unavailable_ble_zero_rejected(self) -> None:
        """Adversarial scenario: SCK is denied/unavailable and requested mic index is :0 (BLE beacon).

        resolve_safe_strategy must downgrade to 'microphone' at :1.
        """
        stderr_sample = (
            "[AVFoundation audio devices:]\n"
            "[0] Amit’s AirPods Pro - Find My\n"
            "[1] MacBook Pro Microphone\n"
        )
        safe_strategy, safe_idx = AudioPreflightValidator.resolve_safe_strategy(
            requested_strategy="screen_capture_kit",
            sck_available=False,
            ffmpeg_bin="ffmpeg",
            preferred_mic_index=":0",
            stderr_text=stderr_sample,
        )
        self.assertEqual(safe_strategy, "microphone")
        self.assertEqual(safe_idx, ":1")


if __name__ == "__main__":
    unittest.main()
