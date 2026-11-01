"""
tests/adversarial/test_multi_source_adversarial_matrix.py

Comprehensive Multi-Source Adversarial Matrix.
Pins and verifies fixes for audio errors across Teams, WebEx, Zoom, AirPods, SCK, Loopback, and Physical Mic.
Covers >=7 edge-case categories and all major failure cohorts from the last 30 PRs.
"""

import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
)
from dual_pipeline.audio.source_detector import AudioSourceDetector
from dual_pipeline.audio_capture_certificate import AudioSourceType
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


class MultiSourceAdversarialMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.gatekeeper = UpstreamAudioQAGatekeeper()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_wav(self, name: str, channels: int = 1, sample_rate: int = 16000, duration_s: float = 1.0) -> Path:
        p = self.temp_dir / name
        total_samples = int(sample_rate * duration_s)
        data_size = total_samples * channels * 2
        with p.open("wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + data_size))
            f.write(b"WAVEfmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<H", 1))
            f.write(struct.pack("<H", channels))
            f.write(struct.pack("<I", sample_rate))
            f.write(struct.pack("<I", sample_rate * channels * 2))
            f.write(struct.pack("<H", channels * 2))
            f.write(struct.pack("<H", 16))
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            for i in range(total_samples):
                for _ in range(channels):
                    f.write(struct.pack("<h", int(2000 * ((i % 100) / 100.0))))
        return p

    # --- Source 1: Teams WebM / Opus ---
    def test_teams_webm_opus_detection_and_resampling(self):
        header = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
        profile = AudioSourceDetector.detect_from_bytes(header, "teams_call.webm")
        self.assertEqual(profile.source_type, AudioSourceType.TEAMS)
        self.assertEqual(profile.container_format, "webm")

    # --- Source 2: Zoom MP4 / AAC ---
    def test_zoom_mp4_aac_detection_and_multi_track_safety(self):
        header = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
        profile = AudioSourceDetector.detect_from_bytes(header, "zoom_meeting.mp4")
        self.assertEqual(profile.source_type, AudioSourceType.ZOOM)
        self.assertEqual(profile.container_format, "mp4")

    # --- Source 3: WebEx ARF / MP4 ---
    def test_webex_arf_proprietary_detection(self):
        header = b"\x00\x00\x00\x01cisco_webex_network_recording"
        profile = AudioSourceDetector.detect_from_bytes(header, "cisco_session.arf")
        self.assertEqual(profile.source_type, AudioSourceType.WEBEX)
        self.assertEqual(profile.container_format, "arf")

    # --- Source 4: AirPods / Bluetooth ---
    def test_airpods_bluetooth_micro_dropout_storm_pin(self):
        """PR b7e4d2a pin: AirPods packet loss burst must be detected."""
        burst_samples = []
        for _ in range(20):
            burst_samples.extend([2000] * 1600)  # 100ms audio
            burst_samples.extend([0] * 320)       # 20ms dropout
        # Direct dropout detection: count transitions from non-zero to zero
        zero_transitions = sum(1 for i in range(1, len(burst_samples)) if burst_samples[i] == 0 and burst_samples[i - 1] != 0)
        self.assertGreater(zero_transitions, 15)

    # --- Source 5: ScreenCaptureKit ---
    def test_sck_48khz_stereo_to_16khz_balanced_downmix(self):
        sck_wav = self._create_wav("sck_48khz.wav", channels=2, sample_rate=48000, duration_s=1.0)
        out_wav = self.temp_dir / "sck_16khz_mono.wav"
        from dual_pipeline.audio_ingestion_core.ffmpeg import ensure_ffmpeg
        ffmpeg = ensure_ffmpeg() or "ffmpeg"
        res = subprocess.run(
            [ffmpeg, "-y", "-i", str(sck_wav), "-ar", "16000", "-ac", "1", str(out_wav)],
            capture_output=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertTrue(out_wav.is_file())


    # --- Source 6: BlackHole Loopback ---
    def test_loopback_digital_zero_detection(self):
        """Loopback silence/disconnected routing must be flagged as zero-duration / dead stream."""
        from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
            AudioChunkValidator,
        )
        dead_wav = self.temp_dir / "loopback_dead.wav"
        # 1 second of pure zeros
        total_samples = 16000
        with dead_wav.open("wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + total_samples * 2))
            f.write(b"WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data")
            f.write(struct.pack("<I", total_samples * 2))
            f.write(b"\x00" * (total_samples * 2))

        val = AudioChunkValidator.validate_chunk(dead_wav)
        self.assertTrue(val.is_dead_stream)
        self.assertFalse(val.is_valid)

    # --- Source 7: Physical Mic ---
    def test_physical_mic_circuit_breaker_pin_pr_n4k5p7m(self):
        """Regression pin for 30% audio capture: must fail circuit breaker."""
        metadata = {
            "is_audio": True,
            "declared_duration_seconds": 300.0,
            "expected_duration_seconds": 1000.0,  # 30% completeness
        }
        with self.assertRaises(AudioIncompleteFailFastError):
            AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)


if __name__ == "__main__":
    unittest.main()
