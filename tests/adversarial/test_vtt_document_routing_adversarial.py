"""
tests/adversarial/test_vtt_document_routing_adversarial.py

Adversarial and Edge-Case Test Suite for VTT/SRT Document Ingestion Routing (Wave 4).
Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
  1. Boundary Payload: 0-byte inputs, whitespace-only cues, and massive cue payloads.
  2. Malformed Input: Disguised binary executables, malformed timestamp arrows, invalid UTF-8, and broken voice tags.
  3. Invariant Bypass Attempt: Deceptive metadata, audio format collateral bypass attempts, and Output 01 authority isolation.
  4. Exact Production Regression: RCA Teams/Zoom VTT document ingestion failure and auto-reroute resolution.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.handlers import _ingest_document
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.document_pipeline_core.attachment_preflight import preflight_attachment
from dual_pipeline.document_pipeline_core.content_loader import (
    _load_collateral_vtt,
    load_document_content,
)
from dual_pipeline.document_pipeline_core.format_source_units import FailureDisposition


class TestVttDocumentRoutingAdversarial(unittest.TestCase):
    """Adversarial suite probing edge cases, malformed payloads, and invariant defenses."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --------------------------------------------------------------------------
    # Category 1: Boundary Payload
    # --------------------------------------------------------------------------

    def test_category1_boundary_zero_byte_vtt_and_srt(self) -> None:
        """Category: Boundary Payload — 0-byte .vtt and .srt attachments fail closed immediately."""
        for ext in (".vtt", ".srt"):
            pf_standalone = preflight_attachment(b"", f"empty{ext}", is_collateral=False)
            self.assertFalse(pf_standalone.is_valid)
            self.assertEqual(pf_standalone.disposition, FailureDisposition.FAIL_CLOSED)

            pf_collateral = preflight_attachment(b"", f"empty{ext}", is_collateral=True)
            self.assertFalse(pf_collateral.is_valid)
            self.assertEqual(pf_collateral.disposition, FailureDisposition.FAIL_CLOSED)

    def test_category1_boundary_whitespace_only_vtt(self) -> None:
        """Category: Boundary Payload — Whitespace-only .vtt produces empty clean text safely without crashing."""
        whitespace_vtt = b"   \n\n\t  \r\n   "
        meta: dict = {}
        clean_text = _load_collateral_vtt(Path("empty.vtt"), whitespace_vtt, metadata=meta)
        self.assertEqual(clean_text, "")
        self.assertEqual(meta.get("_source_coverage", {}).get("total_records"), 0)
        self.assertEqual(len(meta.get("source_units", [])), 0)

    def test_category1_boundary_massive_single_cue(self) -> None:
        """Category: Boundary Payload — Massive cue (>100k chars) parses safely without buffer overflow."""
        massive_line = "A" * 120_000
        vtt_content = f"WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Speaker>{massive_line}</v>\n"
        meta: dict = {}
        clean_text = _load_collateral_vtt(Path("massive.vtt"), vtt_content.encode("utf-8"), metadata=meta)
        self.assertEqual(clean_text, massive_line)
        self.assertEqual(meta.get("_source_coverage", {}).get("total_records"), 1)

    # --------------------------------------------------------------------------
    # Category 2: Malformed Input
    # --------------------------------------------------------------------------

    def test_category2_malformed_binary_executables_disguised_as_vtt(self) -> None:
        """Category: Malformed Input — Disguised ELF and Mach-O binaries fail closed even with is_collateral=True."""
        exec_payloads = [
            (b"\x7fELF\x02\x01\x01\x00corrupt_payload", "fake_binary.vtt"),
            (b"MZ\x90\x00\x03\x00\x00\x00pe_executable", "fake_driver.srt"),
            (b"\xfe\xed\xfa\xce\x00\x00macho_binary", "fake_macho.vtt"),
        ]
        for payload, name in exec_payloads:
            pf = preflight_attachment(payload, name, is_collateral=True)
            self.assertFalse(pf.is_valid)
            self.assertIn(pf.error_code, {"MALFORMED_VTT_SRT_PAYLOAD", "EXECUTABLE_CONTENT_BLOCKED"})

    def test_category2_malformed_invalid_utf8_in_cues(self) -> None:
        """Category: Malformed Input — Invalid UTF-8 replacement bytes handled safely without unhandled exceptions."""
        bad_utf8 = b"WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nBroken \xff\xfe bytes \x80\x81 recovered.\n"
        meta: dict = {}
        clean_text = _load_collateral_vtt(Path("corrupt.vtt"), bad_utf8, metadata=meta)
        self.assertIn("Broken", clean_text)
        self.assertIn("recovered.", clean_text)

    def test_category2_malformed_broken_voice_tags(self) -> None:
        """Category: Malformed Input — Broken and hanging voice tags stripped safely."""
        corrupt_vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "<v Unclosed tag and broken content\n\n"
            "00:00:05.000 --> 00:00:08.000\n"
            "<v Speaker>>Double bracket <<v Nested>> tag</v> payload.\n"
        )
        meta: dict = {}
        clean_text = _load_collateral_vtt(Path("corrupt_tags.vtt"), corrupt_vtt.encode("utf-8"), metadata=meta)
        self.assertNotIn("<v", clean_text)
        self.assertNotIn("</v>", clean_text)
        self.assertIn("payload.", clean_text)

    # --------------------------------------------------------------------------
    # Category 3: Invariant Bypass Attempt
    # --------------------------------------------------------------------------

    def test_category3_bypass_standalone_loader_without_flag_fails_closed(self) -> None:
        """Category: Invariant Bypass — Calling load_document_content without is_collateral fails closed."""
        vtt_p = self.work_dir / "sneaky.vtt"
        vtt_p.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nUnauthorized cue\n", encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            load_document_content(vtt_p, metadata={"is_collateral": False})
        self.assertIn("AUDIO_TRANSCRIPT_ROUTE_EXCLUDED", str(ctx.exception))
        self.assertIn("nee ingest-meeting --input sneaky.vtt", str(ctx.exception))

    def test_category3_bypass_audio_format_collateral_attempt(self) -> None:
        """Category: Invariant Bypass — Audio files (.wav, .mp3) cannot claim collateral transcript support."""
        fake_wav = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        pf = preflight_attachment(fake_wav, "recording.wav", is_collateral=True)
        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.error_code, "AUDIO_TRANSCRIPT_ROUTE_EXCLUDED")
        self.assertIn("nee ingest-meeting --input recording.wav", pf.remediation_command)

    def test_category3_bypass_output01_isolation(self) -> None:
        """Category: Invariant Bypass — Collateral transcript extraction does not claim Output 01 authority."""
        vtt_p = self.work_dir / "exhibit.vtt"
        vtt_p.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nExhibit text\n", encoding="utf-8")
        meta: dict = {"is_collateral": True}
        load_document_content(vtt_p, metadata=meta)

        self.assertTrue(meta.get("is_collateral_attachment"))
        coverage = meta.get("_source_coverage", {})
        self.assertTrue(coverage.get("is_collateral_transcript"))
        # Output 01 meeting markers must NOT be set
        self.assertNotIn("meeting_transcript", meta)
        self.assertNotIn("canonical_transcript_output01", meta)

    # --------------------------------------------------------------------------
    # Category 4: Exact Production Regression
    # --------------------------------------------------------------------------

    def test_category4_production_regression_rca_prescriptive_error(self) -> None:
        """Category: Production Regression — Exact reproduction of RCA: user passing Teams VTT to document intake."""
        parser = build_parser()
        vtt_p = self.work_dir / "teams_meeting_recording.vtt"
        vtt_p.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nMeeting discussion\n", encoding="utf-8")

        # Without auto-reroute: fails closed with exact runnable fix
        args_no_reroute = parser.parse_args([
            "ingest-document",
            "--input", str(vtt_p),
            "--dry-run",
        ])
        with self.assertRaises(ValueError) as ctx:
            _ingest_document(args_no_reroute)
        self.assertIn("nee ingest-meeting --input teams_meeting_recording.vtt", str(ctx.exception))

    @patch("dual_pipeline.cli.handlers._ingest_meeting")
    def test_category4_production_regression_rca_auto_reroute_remedy(self, mock_ingest: MagicMock) -> None:
        """Category: Production Regression — Remediation with --auto-reroute routes Teams VTT smoothly."""
        mock_ingest.return_value = {"status": "SUCCESS"}
        parser = build_parser()
        vtt_p = self.work_dir / "teams_meeting_recording.vtt"
        vtt_p.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nMeeting discussion\n", encoding="utf-8")

        args_reroute = parser.parse_args([
            "ingest-document",
            "--input", str(vtt_p),
            "--auto-reroute",
            "--project", "test-ops",
        ])
        result = _ingest_document(args_reroute)
        self.assertEqual(result, {"status": "SUCCESS"})
        mock_ingest.assert_called_once()
        called_args = mock_ingest.call_args[0][0]
        self.assertEqual(called_args.command, "ingest-meeting")
        self.assertEqual(called_args.meeting, "teams_meeting_recording")


if __name__ == "__main__":
    unittest.main()
