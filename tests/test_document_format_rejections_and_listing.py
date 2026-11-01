"""
tests/test_document_format_rejections_and_listing.py

Wave 2 Acceptance Suite: Prescriptive Format Rejections and Format Listing.
Tests that:
1. Blocked formats (.xls, .ppt, .msg) fail preflight with prescriptive remediation guidance and command.
2. Unknown formats (.zzz) fail preflight with prescriptive remediation guidance.
3. Audio (.wav) and cue (.vtt) files fail document preflight with prescriptive meeting route redirect.
4. CLI command 'nee formats' renders the single SSOT format capability matrix matching audit_matrix().
5. CLI command 'nee formats --json' returns valid JSON matching audit_matrix().
6. CLI command 'nee formats --status' and '--ext' filter correctly.
"""
from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli.formats_cmd import handle_formats_command
from dual_pipeline.document_pipeline_core.attachment_preflight import preflight_attachment
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.format_source_units import FailureDisposition
from dual_pipeline.source_classification.capability_registry import audit_matrix


class TestDocumentFormatRejections(unittest.TestCase):
    """Verifies that all rejections provide clear reasons and actionable next steps."""

    def test_legacy_xls_rejection_guidance(self) -> None:
        """Legacy .xls must provide conversion guidance to .xlsx/.csv and cite nee formats."""
        ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
        pf = preflight_attachment(ole_bytes, "budget.xls")
        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
        self.assertEqual(pf.error_code, "LEGACY_XLS_BLOCKED")
        self.assertIn("Convert to modern .xlsx or .csv", pf.error_message)
        self.assertIn("nee formats", pf.error_message)
        self.assertEqual(pf.remediation_command, "nee formats")

    def test_legacy_ppt_rejection_guidance(self) -> None:
        """Legacy .ppt must provide conversion guidance to .pptx and cite nee formats."""
        ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
        pf = preflight_attachment(ole_bytes, "deck.ppt")
        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
        self.assertEqual(pf.error_code, "LEGACY_PPT_BLOCKED")
        self.assertIn("Convert to modern .pptx", pf.error_message)
        self.assertIn("nee formats", pf.error_message)
        self.assertEqual(pf.remediation_command, "nee formats")

    def test_legacy_msg_rejection_guidance(self) -> None:
        """Legacy .msg must provide conversion guidance to .eml and cite nee formats."""
        ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
        pf = preflight_attachment(ole_bytes, "email.msg")
        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
        self.assertEqual(pf.error_code, "LEGACY_MSG_BLOCKED")
        self.assertIn("Export or convert to standard .eml", pf.error_message)
        self.assertIn("nee formats", pf.error_message)
        self.assertEqual(pf.remediation_command, "nee formats")

    def test_unknown_extension_rejection_guidance(self) -> None:
        """Unknown extension .zzz must state format is not supported and cite nee formats."""
        pf = preflight_attachment(b"arbitrary payload", "payload.zzz")
        self.assertFalse(pf.is_valid)
        self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
        self.assertEqual(pf.error_code, "UNSUPPORTED_BINARY_FORMAT")
        self.assertIn("Format '.zzz' is not supported", pf.error_message)
        self.assertIn("nee formats", pf.error_message)
        self.assertEqual(pf.remediation_command, "nee formats")

    def test_audio_and_cue_redirection_guidance(self) -> None:
        """Audio and cue rejections must provide runnable meeting ingestion command."""
        pf_wav = preflight_attachment(b"RIFF....WAVEfmt ", "meeting.wav")
        self.assertFalse(pf_wav.is_valid)
        self.assertIn("nee ingest-meeting --input meeting.wav", pf_wav.error_message)
        self.assertEqual(pf_wav.remediation_command, "nee ingest-meeting --input meeting.wav")

        pf_vtt = preflight_attachment(b"WEBVTT\n\n00:00.000 --> 00:05.000\nHello", "notes.vtt")
        self.assertFalse(pf_vtt.is_valid)
        self.assertIn("nee ingest-meeting --input notes.vtt", pf_vtt.error_message)
        self.assertEqual(pf_vtt.remediation_command, "nee ingest-meeting --input notes.vtt")

    def test_content_loader_raises_with_remediation_guidance(self) -> None:
        """load_document_content must fail with descriptive error message and guidance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Blocked XLS
            xls_file = Path(tmpdir) / "sheet.xls"
            xls_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
            with self.assertRaises(ValueError) as ctx_xls:
                load_document_content(xls_file)
            self.assertIn("LEGACY_XLS_BLOCKED", str(ctx_xls.exception))
            self.assertIn("nee formats", str(ctx_xls.exception))

            # Unknown ZZZ
            zzz_file = Path(tmpdir) / "doc.zzz"
            zzz_file.write_bytes(b"sample content")
            with self.assertRaises(ValueError) as ctx_zzz:
                load_document_content(zzz_file)
            self.assertIn("UNSUPPORTED_BINARY_FORMAT", str(ctx_zzz.exception))
            self.assertIn("nee formats", str(ctx_zzz.exception))


class TestFormatListingCommand(unittest.TestCase):
    """Verifies that 'nee formats' CLI command outputs accurate, filtered matrix views."""

    def test_formats_command_json_matches_audit_matrix(self) -> None:
        """JSON output from formats command must equal capability_registry.audit_matrix()."""
        args = argparse.Namespace(json=True, status=None, ext=None)
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            res = handle_formats_command(args)

        self.assertEqual(res["status"], "PASS")
        matrix_from_json = json.loads(captured.getvalue())
        expected = audit_matrix()
        self.assertEqual(matrix_from_json, expected)

    def test_formats_command_status_filter(self) -> None:
        """Filtering by status=BLOCKED must return exactly the blocked legacy formats."""
        args = argparse.Namespace(json=True, status="BLOCKED", ext=None)
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            res = handle_formats_command(args)

        self.assertEqual(res["status"], "PASS")
        matrix = json.loads(captured.getvalue())
        self.assertEqual(set(matrix.keys()), {"MSG_LEGACY", "PPT_LEGACY", "XLS_LEGACY"})
        for item in matrix.values():
            self.assertEqual(item["status"], "BLOCKED")

    def test_formats_command_ext_lookup(self) -> None:
        """Querying a specific extension must return its exact capability record."""
        args = argparse.Namespace(json=True, status=None, ext=".docx")
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            res = handle_formats_command(args)

        self.assertEqual(res["status"], "PASS")
        matrix = json.loads(captured.getvalue())
        self.assertIn("DOCX", matrix)
        self.assertEqual(matrix["DOCX"]["status"], "SUPPORTED")

    def test_formats_command_human_readable_table(self) -> None:
        """Default table output must contain header, format IDs, and extensions."""
        args = argparse.Namespace(json=False, status=None, ext=None)
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            res = handle_formats_command(args)

        self.assertEqual(res["status"], "PASS")
        output = captured.getvalue()
        self.assertIn("Format ID", output)
        self.assertIn("Status", output)
        self.assertIn("Extensions", output)
        self.assertIn("DOCX", output)
        self.assertIn("PDF", output)
        self.assertIn("XLSX_WORKBOOK", output)
        self.assertIn("XLS_LEGACY", output)
