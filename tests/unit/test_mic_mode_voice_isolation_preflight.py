"""
tests/unit/test_mic_mode_voice_isolation_preflight.py

Wave 2 Verification Tests:
Verifies macOS Voice Isolation detection and Standard Mic Mode preflight.
Ensures that active Voice Isolation raises AudioCaptureUnprovenError with
reason code VOICE_ISOLATION_ACTIVE and exact remediation instructions.
"""
from __future__ import annotations

import unittest
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from dual_pipeline.live_meeting_recorder_core.mic_mode_preflight import (
    MicModePreflightChecker,
    MicModeStatus,
    REMEDIATION_MESSAGE,
)


class TestMicModeVoiceIsolationPreflight(unittest.TestCase):
    """Test suite for Voice Isolation detection and Standard Mic Mode verification."""

    def tearDown(self):
        MicModePreflightChecker.set_mock_mode(None)

    def test_standard_mode_passes_preflight(self):
        """Standard Mic Mode passes preflight verification cleanly."""
        MicModePreflightChecker.set_mock_mode(MicModeStatus.STANDARD)

        res = MicModePreflightChecker.verify_mic_mode_for_speakerphone(is_speakerphone_source=True)
        self.assertEqual(res["mic_mode"], MicModeStatus.STANDARD)
        self.assertFalse(res["is_voice_isolation"])
        self.assertTrue(res["is_speakerphone_compatible"])

        # AudioPreflightValidator helper also returns True
        self.assertTrue(AudioPreflightValidator.check_mic_mode_for_speakerphone(is_speakerphone_source=True))

        # AudioCaptureProofVerifier helper does not raise
        AudioCaptureProofVerifier.verify_mic_mode_preflight(is_speakerphone_source=True)

    def test_wide_spectrum_passes_preflight(self):
        """Wide Spectrum mode passes preflight verification."""
        MicModePreflightChecker.set_mock_mode(MicModeStatus.WIDE_SPECTRUM)

        res = MicModePreflightChecker.verify_mic_mode_for_speakerphone(is_speakerphone_source=True)
        self.assertEqual(res["mic_mode"], MicModeStatus.WIDE_SPECTRUM)
        self.assertFalse(res["is_voice_isolation"])
        self.assertTrue(res["is_speakerphone_compatible"])

    def test_voice_isolation_fails_closed_with_remediation(self):
        """Voice Isolation raises AudioCaptureUnprovenError with exact remediation."""
        MicModePreflightChecker.set_mock_mode(MicModeStatus.VOICE_ISOLATION)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            MicModePreflightChecker.verify_mic_mode_for_speakerphone(is_speakerphone_source=True)

        err = ctx.exception
        self.assertEqual(err.reason_code, AudioReasonCode.VOICE_ISOLATION_ACTIVE)
        self.assertEqual(err.stage, "PREFLIGHT_MIC_MODE")
        self.assertIn("Voice Isolation", err.detail)
        self.assertEqual(err.remediation, REMEDIATION_MESSAGE)
        self.assertIn("Control Center in the macOS menu bar -> Mic Mode -> select Standard", err.remediation)

        # Ensure dictionary representation carries exact fields
        d = err.to_dict()
        self.assertEqual(d["reason_code"], AudioReasonCode.VOICE_ISOLATION_ACTIVE)
        self.assertEqual(d["remediation"], REMEDIATION_MESSAGE)

    def test_voice_isolation_ignored_when_not_speakerphone(self):
        """Voice Isolation is permitted when not capturing acoustic speakerphone audio."""
        MicModePreflightChecker.set_mock_mode(MicModeStatus.VOICE_ISOLATION)

        # Normal headset mic or system audio capture does not raise
        res = MicModePreflightChecker.verify_mic_mode_for_speakerphone(is_speakerphone_source=False)
        self.assertTrue(res["is_voice_isolation"])
        self.assertFalse(res["is_speakerphone_compatible"])

    def test_preflight_verifier_fails_on_voice_isolation(self):
        """AudioCaptureProofVerifier.verify_mic_mode_preflight raises on Voice Isolation."""
        MicModePreflightChecker.set_mock_mode(MicModeStatus.VOICE_ISOLATION)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_mode_preflight(is_speakerphone_source=True)

        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.VOICE_ISOLATION_ACTIVE)


if __name__ == "__main__":
    unittest.main()
