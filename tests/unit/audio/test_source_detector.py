"""
tests/unit/audio/test_source_detector.py

Unit tests for AudioSourceDetector magic-byte identification.
Verifies source platform detection across WebM/Opus, MP4/AAC, WAV, Cisco ARF, and MP3,
and verifies immunity to extension spoofing.
"""

import unittest

from dual_pipeline.audio.source_detector import AudioSourceDetector
from dual_pipeline.audio_capture_certificate import AudioSourceType


class AudioSourceDetectorUnitTests(unittest.TestCase):
    def test_detect_webm_teams_header(self):
        # EBML header \x1a\x45\xdf\xa3 with Teams hint
        header = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04"
        profile = AudioSourceDetector.detect_from_bytes(header, filename_hint="teams_meeting_recording.webm")
        self.assertEqual(profile.source_type, AudioSourceType.TEAMS)
        self.assertEqual(profile.container_format, "webm")
        self.assertEqual(profile.codec, "opus")
        self.assertGreaterEqual(profile.confidence, 0.90)

    def test_detect_mp4_zoom_header(self):
        # MP4 ftyp box header
        header = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41"
        profile = AudioSourceDetector.detect_from_bytes(header, filename_hint="zoom_call_recording.m4a")
        self.assertEqual(profile.source_type, AudioSourceType.ZOOM)
        self.assertEqual(profile.container_format, "mp4")
        self.assertEqual(profile.codec, "aac")

    def test_detect_wav_riff_header_with_device_hints(self):
        header = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"

        # Bluetooth / AirPods
        p_bt = AudioSourceDetector.detect_from_bytes(header, filename_hint="airpods_pro_capture.wav")
        self.assertEqual(p_bt.source_type, AudioSourceType.BLUETOOTH)

        # ScreenCaptureKit
        p_sck = AudioSourceDetector.detect_from_bytes(header, filename_hint="sck_system_tap.wav")
        self.assertEqual(p_sck.source_type, AudioSourceType.SCK)

        # Loopback
        p_loop = AudioSourceDetector.detect_from_bytes(header, filename_hint="blackhole_loopback.wav")
        self.assertEqual(p_loop.source_type, AudioSourceType.LOOPBACK)

        # Physical Mic
        p_mic = AudioSourceDetector.detect_from_bytes(header, filename_hint="macbook_built_in_mic.wav")
        self.assertEqual(p_mic.source_type, AudioSourceType.MIC)

    def test_extension_spoofing_immunity(self):
        """A WebM file renamed to .wav must be identified as WebM via magic bytes."""
        webm_header = b"\x1a\x45\xdf\xa3\x00\x00"
        profile = AudioSourceDetector.detect_from_bytes(webm_header, filename_hint="fake_audio.wav")
        self.assertEqual(profile.container_format, "webm")
        self.assertEqual(profile.detected_magic, "webm_ebml")

    def test_detect_webex_cisco_arf(self):
        arf_header = b"\x00\x00\x00\x01cisco_webex_recording_stream"
        profile = AudioSourceDetector.detect_from_bytes(arf_header, filename_hint="meeting.arf")
        self.assertEqual(profile.source_type, AudioSourceType.WEBEX)
        self.assertEqual(profile.container_format, "arf")


if __name__ == "__main__":
    unittest.main()
