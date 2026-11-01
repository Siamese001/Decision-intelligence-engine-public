"""
tests/test_vtt_cli_auto_redirection.py

Wave 2 Verification: Validates CLI smart auto-redirection on transcript cue inputs
(.vtt, .srt) supplied to ingest-document with --auto-reroute flag.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.cli.handlers import _ingest_document
from dual_pipeline.cli.ingest import _apply_semantic_flags, build_canonical_metadata
from dual_pipeline.cli.parser import build_parser


class TestVttCliAutoRedirection(unittest.TestCase):
    """Test suite validating smart CLI auto-redirection of transcript cue files."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_parser_accepts_auto_reroute_flag(self) -> None:
        """Verify ingest-document subparser accepts --auto-reroute."""
        args = self.parser.parse_args([
            "ingest-document",
            "--input", "meeting_recording.vtt",
            "--project", "test_project",
            "--auto-reroute",
        ])
        self.assertTrue(args.auto_reroute)
        self.assertEqual(str(args.input), "meeting_recording.vtt")

    def test_apply_semantic_flags_with_auto_reroute(self) -> None:
        """Verify _apply_semantic_flags routes .vtt to MEETING_TRANSCRIPT when auto_reroute is True."""
        args = argparse.Namespace(
            command="ingest-document",
            input=Path("team_sync.vtt"),
            source=None,
            document=True,
            notes=False,
            copilot_notes=False,
            output3_only=False,
            auto_reroute=True,
            meeting="Team Sync",
            title=None,
            regenerate=None,
        )
        meta: dict = {}
        _apply_semantic_flags(meta, args)

        self.assertFalse(meta.get("is_document", False))
        self.assertEqual(meta.get("canonical_modality"), CanonicalModality.MEETING_TRANSCRIPT.value)
        self.assertEqual(meta.get("outputs"), ["01", "02", "03"])
        self.assertTrue(meta.get("auto_rerouted_from_document"))
        self.assertEqual(meta.get("meeting_name"), "Team Sync")

    def test_apply_semantic_flags_without_auto_reroute_preserves_document_mode(self) -> None:
        """Verify .vtt defaults to DOCUMENT mode when --auto-reroute is omitted."""
        args = argparse.Namespace(
            command="ingest-document",
            input=Path("team_sync.vtt"),
            source=None,
            document=True,
            notes=False,
            copilot_notes=False,
            output3_only=False,
            auto_reroute=False,
            meeting=None,
            title=None,
            regenerate=None,
        )
        meta: dict = {}
        _apply_semantic_flags(meta, args)

        self.assertTrue(meta.get("is_document"))
        self.assertEqual(meta.get("canonical_modality"), CanonicalModality.DOCUMENT.value)
        self.assertEqual(meta.get("outputs"), ["03"])
        self.assertFalse(meta.get("auto_rerouted_from_document", False))

    def test_auto_reroute_meeting_name_inference(self) -> None:
        """Verify meeting name falls back to title or file stem during auto-redirection."""
        # Case A: title provided
        args_title = argparse.Namespace(
            command="ingest-document",
            input=Path("notes_call.vtt"),
            source=None,
            document=True,
            notes=False,
            copilot_notes=False,
            output3_only=False,
            auto_reroute=True,
            meeting=None,
            title="Q3 Strategic Alignment",
            regenerate=None,
        )
        meta_title: dict = {}
        _apply_semantic_flags(meta_title, args_title)
        self.assertEqual(meta_title.get("meeting_name"), "Q3 Strategic Alignment")

        # Case B: fallback to filename stem
        args_stem = argparse.Namespace(
            command="ingest-document",
            input=Path("Executive_Review_2026.vtt"),
            source=None,
            document=True,
            notes=False,
            copilot_notes=False,
            output3_only=False,
            auto_reroute=True,
            meeting=None,
            title=None,
            regenerate=None,
        )
        meta_stem: dict = {}
        _apply_semantic_flags(meta_stem, args_stem)
        self.assertEqual(meta_stem.get("meeting_name"), "Executive_Review_2026")

    @patch("dual_pipeline.cli.handlers._ingest_meeting")
    def test_handler_dispatches_to_ingest_meeting(self, mock_meeting: MagicMock) -> None:
        """Verify _ingest_document dispatches to _ingest_meeting when auto-rerouting."""
        mock_meeting.return_value = {"status": "COMPLETE", "modality": "MEETING_TRANSCRIPT"}
        with tempfile.TemporaryDirectory() as tmpdir:
            vtt_p = Path(tmpdir) / "client_session.vtt"
            vtt_p.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello")

            args = argparse.Namespace(
                command="ingest-document",
                input=vtt_p,
                source=None,
                auto_reroute=True,
                title="Client Session",
                meeting=None,
                document=True,
            )

            res = _ingest_document(args)
            self.assertEqual(res["status"], "COMPLETE")
            mock_meeting.assert_called_once()
            called_args = mock_meeting.call_args[0][0]
            self.assertFalse(called_args.document)
            self.assertEqual(called_args.command, "ingest-meeting")
            self.assertEqual(called_args.meeting, "Client Session")

    @patch("dual_pipeline.cli.handlers.ingest_canonical")
    def test_handler_does_not_reroute_standard_document(self, mock_canonical: MagicMock) -> None:
        """Verify non-transcript documents are not rerouted even if auto_reroute is True."""
        mock_canonical.return_value = {"status": "COMPLETE", "modality": "DOCUMENT"}
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_p = Path(tmpdir) / "statement_of_work.pdf"
            pdf_p.write_bytes(b"%PDF-1.4 sample")

            args = argparse.Namespace(
                command="ingest-document",
                input=pdf_p,
                source=None,
                auto_reroute=True,
                title="SOW",
                meeting=None,
                document=False,
            )

            res = _ingest_document(args)
            self.assertEqual(res["status"], "COMPLETE")
            mock_canonical.assert_called_once()
            called_args = mock_canonical.call_args[0][0]
            self.assertTrue(called_args.document)


if __name__ == "__main__":
    unittest.main()
