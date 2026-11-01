"""
tests/test_vtt_preflight_prescriptive_error.py

Wave 1 Verification: Validates that transcript cue (.vtt, .srt) and audio files
supplied to document intake preflight fail closed with structured error codes
and actionable, prescriptive remediation commands pointing to nee ingest-meeting.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline_core.attachment_preflight import preflight_attachment
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.format_source_units import FailureDisposition


class TestVttPreflightPrescriptiveError(unittest.TestCase):
    """Test suite validating prescriptive remediation errors on excluded cue/audio routes."""

    def test_vtt_preflight_blocked_with_prescriptive_command(self) -> None:
        """Verify .vtt files fail preflight and return runnable ingest-meeting command."""
        raw_vtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Alex>Hello team</v>"
        pf = preflight_attachment(raw_vtt, "project_sync.vtt")

        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.format_id, "VTT_SRT")
        self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
        self.assertEqual(pf.error_code, "AUDIO_TRANSCRIPT_ROUTE_EXCLUDED")
        self.assertTrue(pf.is_excluded_from_attachment)
        self.assertIsNotNone(pf.remediation_command)
        self.assertEqual(pf.remediation_command, "nee ingest-meeting --input project_sync.vtt")
        self.assertIn("nee ingest-meeting --input project_sync.vtt", pf.error_message or "")

        # Verify serialization payload
        d = pf.to_dict()
        self.assertIn("remediation_command", d)
        self.assertEqual(d["remediation_command"], "nee ingest-meeting --input project_sync.vtt")

    def test_srt_preflight_blocked_with_prescriptive_command(self) -> None:
        """Verify .srt files fail preflight and provide actionable remediation."""
        raw_srt = b"1\n00:00:01,000 --> 00:00:04,000\nHello team\n"
        pf = preflight_attachment(raw_srt, "client_interview.srt")

        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.format_id, "VTT_SRT")
        self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
        self.assertEqual(pf.error_code, "AUDIO_TRANSCRIPT_ROUTE_EXCLUDED")
        self.assertEqual(pf.remediation_command, "nee ingest-meeting --input client_interview.srt")
        self.assertIn("nee ingest-meeting --input client_interview.srt", pf.error_message or "")

    def test_audio_preflight_blocked_with_prescriptive_command(self) -> None:
        """Verify audio formats fail preflight with acoustic route prescriptive guidance."""
        raw_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        pf = preflight_attachment(raw_wav, "recording.wav")

        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.format_id, "AUDIO")
        self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
        self.assertEqual(pf.error_code, "AUDIO_TRANSCRIPT_ROUTE_EXCLUDED")
        self.assertEqual(pf.remediation_command, "nee ingest-meeting --input recording.wav")
        self.assertIn("nee ingest-meeting --input recording.wav", pf.error_message or "")

    def test_content_loader_raises_prescriptive_remediation_on_vtt(self) -> None:
        """Verify load_document_content raises a ValueError embedding the remediation command."""
        raw_vtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Jordan>Testing vtt load</v>"
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "leadership_sync.vtt"
            p.write_bytes(raw_vtt)

            with self.assertRaises(ValueError) as ctx:
                load_document_content(p)

            err_msg = str(ctx.exception)
            self.assertIn("preflight failed", err_msg.lower())
            self.assertIn("AUDIO_TRANSCRIPT_ROUTE_EXCLUDED", err_msg)
            self.assertIn("nee ingest-meeting --input leadership_sync.vtt", err_msg)

    def test_case_insensitivity_and_default_fallback(self) -> None:
        """Verify uppercase extension and missing filename handle remediation gracefully."""
        raw_vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nTest"
        pf_upper = preflight_attachment(raw_vtt, "CALL.VTT")
        self.assertEqual(pf_upper.remediation_command, "nee ingest-meeting --input CALL.VTT")

        pf_no_name = preflight_attachment(raw_vtt, "")
        self.assertEqual(pf_no_name.error_code, "UNSUPPORTED_BINARY_FORMAT")


if __name__ == "__main__":
    unittest.main()
