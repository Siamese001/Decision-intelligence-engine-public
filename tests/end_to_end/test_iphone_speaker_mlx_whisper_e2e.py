"""
tests/end_to_end/test_iphone_speaker_mlx_whisper_e2e.py

Wave 3 E2E Verification Tests:
Tests complete end-to-end flow for iPhone speakerphone acoustic recording:
1. CLI flag parsing (--audio-source iphone-speaker and --iphone-speaker convenience flag).
2. Fast pre-CLI bootstrap routing to iphone-speaker source mode.
3. LiveAudioCaptureManager strategy resolution, built-in mic selection, and mono P0-P3 proof.
4. Record start receipt generation with strategy="iphone_speaker" and is_stereo=False.
5. Local MLX Whisper transcription of 16kHz mono chunks with zero cloud egress.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import run_fast_bootstrap
from pipeline_a_intake.coreaudio_hal import HALDeviceInfo
from pipeline_a_intake.mac_system_audio import AudioDeviceInfo, AudioDeviceType


def _generate_test_wav(path: Path, duration_sec: float = 1.0, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # 440 Hz tone samples
        data = bytearray()
        import math
        for i in range(num_samples):
            val = int(10000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            data.extend(struct.pack("<h", val))
        w.writeframes(data)


class TestIPhoneSpeakerMLXWhisperE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_e2e_iphone_speaker_")
        self.temp_path = Path(self.temp_dir)
        self.chunks_dir = self.temp_path / "chunks"
        self.master_wav = self.temp_path / "master.wav"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

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

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        Path("reports/chunk_00000_acoustic_provenance_receipt.json").unlink(missing_ok=True)

    def test_cli_parser_iphone_speaker_flags(self):
        """Verifies CLI parser handles both --audio-source iphone-speaker and --iphone-speaker."""
        parser = build_parser()
        args1 = parser.parse_args(["record-meeting", "--audio-source", "iphone-speaker"])
        self.assertEqual(args1.audio_source, "iphone-speaker")

        args2 = parser.parse_args(["record-meeting", "--iphone-speaker"])
        self.assertEqual(args2.audio_source, "iphone-speaker")

    def test_fast_bootstrap_handles_iphone_speaker_flag(self):
        """Verifies run_fast_bootstrap parses --iphone-speaker and routes to instant spooler."""
        argv = ["record-meeting", "--project", "test_proj", "--iphone-speaker", "--mock"]
        spool_dir = run_fast_bootstrap(argv)
        self.assertIsNotNone(spool_dir)
        spool_path = Path(spool_dir)
        self.assertTrue(spool_path.exists())
        # Clean up
        shutil.rmtree(spool_path, ignore_errors=True)

    @patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.find_builtin_microphone")
    @patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.set_default_input_device_id")
    @patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device")
    @patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.list_devices")
    @patch("dual_pipeline.live_meeting_recorder_core.mic_mode_preflight.MicModePreflightChecker.detect_mic_mode")
    def test_capture_manager_iphone_speaker_lifecycle_and_receipt(
        self,
        mock_mic_mode,
        mock_list_devices,
        mock_get_default_input,
        mock_set_default_id,
        mock_find_builtin,
    ):
        """Verifies LiveAudioCaptureManager in iphone-speaker mode produces valid mono receipt."""
        mock_find_builtin.return_value = self.builtin_hal
        mock_set_default_id.return_value = True
        mock_get_default_input.return_value = self.airpods_info
        mock_list_devices.return_value = [self.airpods_info, self.builtin_info]
        mock_mic_mode.return_value = ("Standard", False)

        manager = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="iphone-speaker",
            device_index=":0",
            segment_seconds=5,
        )

        # In iphone-speaker mode, mono capture is enforced (stereo isolation disabled)
        self.assertFalse(manager.enable_stereo_isolation)
        self.assertEqual(manager.device_index, ":1")

        strategy, dev = manager.resolve_source_strategy()
        self.assertEqual(strategy, "iphone_speaker")
        self.assertEqual(int(dev.device_id), 202)

        # Synthesize audio chunk and master file to verify proof receipt generation
        _generate_test_wav(self.master_wav, duration_sec=1.5, sample_rate=16000)
        chunk_file = self.chunks_dir / "chunk_00000.wav"
        _generate_test_wav(chunk_file, duration_sec=1.5, sample_rate=16000)

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None
        manager._ffmpeg_proc = mock_proc

        receipt = manager._run_start_proof(strategy="iphone_speaker")
        self.assertEqual(receipt["status"], "CAPTURE_PROVEN")
        self.assertEqual(receipt["strategy"], "iphone_speaker")
        self.assertFalse(receipt["is_stereo"])

        receipt_file = self.master_wav.parent / "record_start_receipt.json"
        self.assertTrue(receipt_file.exists())
        saved_receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertEqual(saved_receipt["strategy"], "iphone_speaker")
        self.assertFalse(saved_receipt["is_stereo"])

    def test_mlx_whisper_transcription_zero_egress(self):
        """Verifies mono chunk can be transcribed locally with zero network egress."""
        chunk_wav = self.temp_path / "chunk_00000.wav"
        # Set offline flags to avoid HF remote update checks
        orig_hf = os.environ.get("HF_HUB_OFFLINE")
        orig_tr = os.environ.get("TRANSFORMERS_OFFLINE")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        orig_hf_const = None
        try:
            import huggingface_hub.constants
            orig_hf_const = getattr(huggingface_hub.constants, "HF_HUB_OFFLINE", None)
            huggingface_hub.constants.HF_HUB_OFFLINE = True
        except (ImportError, AttributeError):
            pass

        try:
            # Generate real spoken audio via macOS say if on Darwin
            aiff = self.temp_path / "probe.aiff"
            if sys.platform == "darwin":
                try:
                    subprocess.run(["say", "-o", str(aiff), "iPhone speaker test."], check=True, timeout=5)
                    import imageio_ffmpeg
                    subprocess.run(
                        [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(chunk_wav)],
                        check=True,
                        timeout=5,
                    )
                except Exception:
                    _generate_test_wav(chunk_wav, duration_sec=1.0, sample_rate=16000)
            else:
                _generate_test_wav(chunk_wav, duration_sec=1.0, sample_rate=16000)

            # Egress firewall
            network_attempts = []
            orig_connect = socket.socket.connect

            def _firewall_connect(self_, address):
                host = address[0] if isinstance(address, tuple) else str(address)
                if not any(str(host).startswith(p) for p in ("127.", "::1", "localhost")):
                    network_attempts.append(host)
                    raise ConnectionRefusedError(f"Cloud egress blocked: {host}")
                return orig_connect(self_, address)

            with patch.object(socket.socket, "connect", _firewall_connect):
                from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
                    LocalMLXAudioTranscriber,
                    is_mlx_available,
                )
                if not is_mlx_available():
                    self.skipTest("MLX Metal GPU not available in test environment")
                transcriber = LocalMLXAudioTranscriber(model_name="mlx-community/whisper-tiny")
                res = transcriber.transcribe(str(chunk_wav), metadata={"attendees": ["Speaker"]})
                self.assertIsNotNone(res)
                self.assertEqual(len(network_attempts), 0, "Cloud network calls detected during transcription")
        finally:
            if orig_hf is not None:
                os.environ["HF_HUB_OFFLINE"] = orig_hf
            else:
                os.environ.pop("HF_HUB_OFFLINE", None)
            if orig_tr is not None:
                os.environ["TRANSFORMERS_OFFLINE"] = orig_tr
            else:
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
            if orig_hf_const is not None:
                try:
                    import huggingface_hub.constants
                    huggingface_hub.constants.HF_HUB_OFFLINE = orig_hf_const
                except Exception:
                    pass
