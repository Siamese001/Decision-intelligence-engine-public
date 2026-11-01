#!/usr/bin/env python3
"""
tests/test_mac_system_audio.py

Comprehensive test suite for Prompt 6: macOS System Audio Tap & Loopback Ingestion for Whisper.
Tests zero-speaker digital capture, CoreAudio device discovery, 48k/44.1k to 16k polyphase resampling,
mock loopback streaming, and integration with AudioSplicer and LocalSTTEngine.
"""
from __future__ import annotations

import asyncio
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from pipeline_a_intake.audio_splicer import AudioSplicer
from pipeline_a_intake.mac_system_audio import (
    AudioDeviceInfo,
    AudioDeviceType,
    AudioResampler16k,
    CoreAudioDeviceManager,
    MacSystemAudioEngine,
    MockSystemAudioLoopback,
)


class TestCoreAudioDeviceManager(unittest.TestCase):
    def test_live_device_listing(self):
        """Tests that device manager successfully enumerates macOS audio hardware."""
        devices = CoreAudioDeviceManager.list_devices()
        self.assertIsInstance(devices, list)
        self.assertGreaterEqual(len(devices), 1)

        # Should find at least one input or output device on macOS
        has_io = any(d.device_type in (AudioDeviceType.INPUT, AudioDeviceType.OUTPUT) for d in devices)
        self.assertTrue(has_io)

    def test_device_classification_logic(self):
        """Tests classification of virtual loopback devices (BlackHole, Soundflower, Multi-Output)."""
        d_blackhole = AudioDeviceInfo(
            device_id="10",
            name="BlackHole 2ch",
            device_type=AudioDeviceType.LOOPBACK,
            input_channels=2,
            output_channels=2,
        )
        self.assertTrue(d_blackhole.is_loopback)

        d_mic = AudioDeviceInfo(
            device_id="0",
            name="MacBook Pro Microphone",
            device_type=AudioDeviceType.INPUT,
            input_channels=1,
            output_channels=0,
        )
        self.assertFalse(d_mic.is_loopback)

    def test_fallback_devices(self):
        """Verifies reliable fallback endpoints when system profiling tools fail."""
        fallbacks = CoreAudioDeviceManager._fallback_list_devices()
        self.assertEqual(len(fallbacks), 2)
        self.assertEqual(fallbacks[0].name, "MacBook Pro Microphone")
        self.assertTrue(fallbacks[0].is_default_input)
        self.assertEqual(fallbacks[1].name, "MacBook Pro Speakers")
        self.assertTrue(fallbacks[1].is_default_output)

    def test_headphone_detection(self):
        """Tests classification of headphones, AirPods, and headsets."""
        airpods = AudioDeviceInfo(
            device_id="2",
            name="AirPods Pro",
            device_type=AudioDeviceType.HEADSET,
            input_channels=1,
            output_channels=2,
            is_default_output=True,
        )
        self.assertTrue(airpods.is_headphones)
        self.assertFalse(airpods.is_loopback)
        self.assertEqual(airpods.device_type, AudioDeviceType.HEADSET)

        fake_devices = [
            AudioDeviceInfo("0", "MacBook Pro Microphone", AudioDeviceType.INPUT, input_channels=1),
            airpods,
        ]
        with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.is_available", return_value=False), \
             patch.object(CoreAudioDeviceManager, "list_devices", return_value=fake_devices):
            self.assertTrue(CoreAudioDeviceManager.is_headphones_connected())
            active = CoreAudioDeviceManager.get_active_headphones()
            self.assertIsNotNone(active)
            self.assertEqual(active.name, "AirPods Pro")

    def test_mute_and_volume_query(self):
        """Tests system mute detection and volume querying."""
        with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.is_available", return_value=False), \
             patch("subprocess.run") as mock_run:
            # Test muted state
            mock_run.return_value = MagicMock(returncode=0, stdout="true\n")
            self.assertTrue(CoreAudioDeviceManager.is_system_muted())

            # Test unmuted state with volume 0 (effectively muted)
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="false\n"),
                MagicMock(returncode=0, stdout="0\n"),
            ]
            self.assertTrue(CoreAudioDeviceManager.is_system_muted())

            # Test unmuted state with volume 65
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="false\n"),
                MagicMock(returncode=0, stdout="65\n"),
            ]
            self.assertFalse(CoreAudioDeviceManager.is_system_muted())

            # Test get_system_volume
            mock_run.side_effect = None
            mock_run.return_value = MagicMock(returncode=0, stdout="75\n")
            self.assertEqual(CoreAudioDeviceManager.get_system_volume(), 75)

    def test_ble_find_my_rejection_and_mic_preference(self):
        """Tests that BLE Find My beacon devices are never selected as default microphone."""
        find_my = AudioDeviceInfo(
            device_id="0",
            name="Amit’s AirPods Pro - Find My",
            device_type=AudioDeviceType.UNKNOWN,
            input_channels=1,
            avfoundation_index=0,
            is_default_input=False,
        )
        self.assertTrue(find_my.is_non_streaming_virtual)

        macbook_mic = AudioDeviceInfo(
            device_id="1",
            name="MacBook Pro Microphone",
            device_type=AudioDeviceType.INPUT,
            input_channels=1,
            transport="builtin",
            avfoundation_index=1,
            is_default_input=True,
        )
        self.assertFalse(macbook_mic.is_non_streaming_virtual)

        fake_devices = [find_my, macbook_mic]
        with patch.object(CoreAudioDeviceManager, "list_devices", return_value=fake_devices):
            selected = CoreAudioDeviceManager.get_default_input_device()
            self.assertIsNotNone(selected)
            self.assertEqual(selected.name, "MacBook Pro Microphone")
            self.assertEqual(selected.avfoundation_index, 1)
            self.assertEqual(CoreAudioDeviceManager.get_default_input_avfoundation_index(), ":1")


class TestAudioResampler16k(unittest.TestCase):
    def setUp(self):
        self.resampler = AudioResampler16k(target_rate=16_000)

    def test_resample_48k_stereo_to_16k_mono(self):
        """48,000 Hz stereo must downmix and decimate by factor 3 to 16,000 Hz mono."""
        duration_sec = 0.5
        source_rate = 48_000
        num_samples = int(source_rate * duration_sec)

        # 440Hz sine wave on stereo channels
        t = np.linspace(0, duration_sec, num_samples, endpoint=False)
        ch0 = 0.5 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
        ch1 = 0.5 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
        stereo_48k = np.column_stack([ch0, ch1])

        mono_16k = self.resampler.resample(stereo_48k, source_rate=48_000, channels=2)

        # Expected output length: num_samples / 3 = 24000 / 3 = 8000
        expected_samples = int(16_000 * duration_sec)
        self.assertEqual(len(mono_16k), expected_samples)
        self.assertEqual(mono_16k.ndim, 1)
        self.assertEqual(mono_16k.dtype, np.float32)

        # Max amplitude should be preserved near 0.5
        self.assertAlmostEqual(float(np.max(np.abs(mono_16k))), 0.5, delta=0.05)

    def test_resample_44k1_stereo_to_16k_mono(self):
        """44,100 Hz stereo must resample to 16,000 Hz mono with accurate sample count."""
        duration_sec = 0.25
        source_rate = 44_100
        num_samples = int(source_rate * duration_sec)

        stereo_44k1 = np.ones((num_samples, 2), dtype=np.float32) * 0.4
        mono_16k = self.resampler.resample(stereo_44k1, source_rate=44_100, channels=2)

        expected_samples = int(round(16_000 * duration_sec))
        self.assertAlmostEqual(len(mono_16k), expected_samples, delta=2)
        self.assertAlmostEqual(float(np.mean(mono_16k)), 0.4, delta=0.05)

    def test_resample_pcm_s16le_bytes(self):
        """Raw 16-bit PCM bytes must decode and normalize to [-1.0, 1.0]."""
        raw_int16 = np.array([0, 16384, 32767, -16384, -32768], dtype=np.int16)
        pcm_bytes = raw_int16.tobytes()

        mono_16k = self.resampler.resample(pcm_bytes, source_rate=16_000, channels=1)
        self.assertEqual(len(mono_16k), 5)
        self.assertAlmostEqual(float(mono_16k[0]), 0.0, delta=0.01)
        self.assertAlmostEqual(float(mono_16k[1]), 0.5, delta=0.01)
        self.assertAlmostEqual(float(mono_16k[2]), 1.0, delta=0.01)
        self.assertAlmostEqual(float(mono_16k[3]), -0.5, delta=0.01)
        self.assertAlmostEqual(float(mono_16k[4]), -1.0, delta=0.01)

    def test_empty_audio_handling(self):
        """Empty audio buffers must return empty float32 arrays without exceptions."""
        empty_arr = np.zeros(0, dtype=np.float32)
        out = self.resampler.resample(empty_arr, source_rate=48_000, channels=2)
        self.assertEqual(len(out), 0)

        out_bytes = self.resampler.resample(b"", source_rate=48_000, channels=2)
        self.assertEqual(len(out_bytes), 0)


class TestMockSystemAudioLoopback(unittest.TestCase):
    def test_generate_speech_sim_chunk(self):
        """Generates multi-harmonic speech-like packet."""
        feeder = MockSystemAudioLoopback(sample_rate=48_000, channels=2, chunk_duration_sec=0.05)
        chunk = feeder.generate_speech_sim_chunk(amplitude=0.6)

        expected_samples = int(48_000 * 0.05)  # 2400
        self.assertEqual(chunk.shape, (expected_samples, 2))
        self.assertEqual(chunk.dtype, np.float32)
        self.assertGreater(float(np.max(np.abs(chunk))), 0.1)

    def test_generate_silence_chunk(self):
        """Generates zero-energy acoustic silence."""
        feeder = MockSystemAudioLoopback(sample_rate=16_000, channels=1, chunk_duration_sec=0.1)
        chunk = feeder.generate_silence_chunk()
        self.assertEqual(len(chunk), 1600)
        self.assertEqual(float(np.max(np.abs(chunk))), 0.0)


class TestMacSystemAudioEngineIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_mock_engine_feeding_splicer(self):
        """
        Tests end-to-end streaming from MockSystemAudioEngine directly into AudioSplicer.
        Verifies non-blocking async execution, RMS dBFS telemetry, and sample accounting.
        """
        splicer = AudioSplicer()
        feeder = MockSystemAudioLoopback(sample_rate=48_000, channels=2, chunk_duration_sec=0.02)

        received_chunks = []
        def on_chunk(samples: np.ndarray, rms: float):
            received_chunks.append((len(samples), rms))

        engine = MacSystemAudioEngine(
            capture_mode="mock",
            target_splicer=splicer,
            on_audio_chunk=on_chunk,
            mock_feeder=feeder,
        )

        self.assertEqual(engine.capture_mode, "mock")
        strat, dev = engine.resolve_strategy()
        self.assertEqual(strat, "mock")

        await engine.start()
        self.assertTrue(engine.is_running)

        # Let it stream for 150ms
        await asyncio.sleep(0.15)

        # Verify telemetry and buffer
        self.assertGreater(engine.total_samples_captured, 1000)
        self.assertGreater(engine.current_rms_dbfs, -50.0)
        self.assertGreater(splicer.ring_buffer.total_samples_written, 1000)
        self.assertGreater(len(received_chunks), 2)

        # Stop engine cleanly
        await engine.stop()
        self.assertFalse(engine.is_running)

    async def test_silence_rms_telemetry(self):
        """Tests that silence chunks register as low RMS dBFS."""
        feeder = MockSystemAudioLoopback(sample_rate=16_000, channels=1, chunk_duration_sec=0.02)
        # Override with silence generator
        feeder.generate_speech_sim_chunk = feeder.generate_silence_chunk

        engine = MacSystemAudioEngine(
            capture_mode="mock",
            mock_feeder=feeder,
        )

        await engine.start()
        await asyncio.sleep(0.08)
        await engine.stop()

        self.assertLessEqual(engine.current_rms_dbfs, -90.0)


class TestMacSystemAudioSTTPipelineIntegration(unittest.TestCase):
    """
    Verifies full end-to-end pipeline:
    MockSystemAudioLoopback -> MacSystemAudioEngine -> AudioResampler16k -> AudioSplicer -> LocalSTTEngine -> SQLite WAL
    """

    def setUp(self):
        import tempfile

        from pipeline_a_intake.local_stt import LocalSTTEngine
        from storage.database import DatabaseManager

        self.temp_dir = tempfile.mkdtemp()
        self.db = DatabaseManager(base_dir=self.temp_dir, allow_unencrypted_dev_fallback=True)
        self.stt_engine = LocalSTTEngine(
            db_manager=self.db,
            mock_mode=True,
            session_id="mac_sys_test_session",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_system_audio_to_sqlite_persistence(self):
        from pipeline_a_intake.local_stt import AudioChunk

        feeder = MockSystemAudioLoopback(sample_rate=48_000, channels=2, chunk_duration_sec=0.1)
        resampler = AudioResampler16k(target_rate=16_000)

        # Generate 1.0 second of stereo 48kHz speech-sim audio
        chunks_48k = [feeder.generate_speech_sim_chunk(amplitude=0.6) for _ in range(10)]
        audio_48k = np.vstack(chunks_48k)

        # Resample to 16kHz mono float32
        mono_16k = resampler.resample(audio_48k, source_rate=48_000, channels=2)
        self.assertEqual(len(mono_16k), 16_000)

        # Feed to LocalSTTEngine
        chunk = AudioChunk(
            chunk_id="sys_chk_01",
            meeting_id="meet_system_audio",
            audio_data=mono_16k,
            speaker="System Audio (Zero-Speaker)",
            metadata={"mock_text": "Project Nexus architecture discussion captured directly from system audio without speakers."},
        )

        deltas = self.stt_engine.feed_audio(chunk)
        self.assertGreater(len(deltas), 0)

        # Flush final tokens
        final_delta = self.stt_engine.flush()
        self.assertIsNotNone(final_delta)

        # Verify evidence committed to SQLite WAL
        records = self.db.get_evidence_by_meeting("meet_system_audio")
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0].speaker, "System Audio (Zero-Speaker)")
        self.assertIn("Project Nexus architecture", records[0].text)

    def test_accelerate_screen_capture_kit_binary_compilation(self):
        from pipeline_a_intake.mac_system_audio import SCK_COMPILED_BIN, SCK_SWIFT_SOURCE
        self.assertTrue(SCK_SWIFT_SOURCE.exists(), "Swift source file must exist")
        # Verify binary exists or compile succeeds
        if not SCK_COMPILED_BIN.exists():
            SCK_COMPILED_BIN.parent.mkdir(parents=True, exist_ok=True)
            res = subprocess.run(
                ["/usr/bin/swiftc", "-O", str(SCK_SWIFT_SOURCE), "-o", str(SCK_COMPILED_BIN)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"Compilation failed: {res.stderr}")
        self.assertTrue(SCK_COMPILED_BIN.exists())


if __name__ == "__main__":
    unittest.main()

