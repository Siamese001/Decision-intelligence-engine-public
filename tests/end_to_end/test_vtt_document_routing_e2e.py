"""
tests/end_to_end/test_vtt_document_routing_e2e.py

End-to-End Governance Test Suite for VTT/SRT Document Ingestion Routing & Collateral Lifecycle (Wave 4).
Exercises full lifecycles:
  1. CLI ingress auto-redirection from document command to canonical meeting pipeline.
  2. Fail-closed preflight rejection when auto-reroute is omitted.
  3. Multi-source document envelope intake with primary document and collateral transcript exhibit.
  4. Source manifest accounting, source unit extraction, and idempotency across repeated loads.
"""
from __future__ import annotations

import argparse
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.handlers import _ingest_document
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.document_pipeline_core.attachment_preflight import preflight_attachment
from dual_pipeline.document_pipeline_core.content_loader import (
    load_document_content,
)
from dual_pipeline.document_pipeline_core.format_source_units import build_text_source_units
from dual_pipeline.document_pipeline_core.source_manifest import build_source_manifest

SAMPLE_E2E_VTT = """WEBVTT - Investigation Interview Recording

1
00:00:00.500 --> 00:00:03.200
<v Detective Miller>State your name and title for the official record.</v>

2
00:00:03.800 --> 00:00:07.100
<v Dr. Thorne>Dr. Aris Thorne, Chief Propulsion Architect at Hyperion Dynamics.</v>

3
00:00:08.000 --> 00:00:11.500
<v Detective Miller>Did you authorize the thermal bypass sequence on October third?</v>

4
00:00:12.000 --> 00:00:15.000
<v Dr. Thorne>No, that bypass was executed automatically by the diagnostic failsafe.</v>
"""

SAMPLE_PRIMARY_DOC = """# Incident Investigation Dossier: Hyperion Propulsion Anomaly

## Overview
This dossier synthesizes evidence regarding the thermal anomaly observed during the October 3 testing cycle.

## Exhibits
- Exhibit A: Telemetry log dump.
- Exhibit B: Audio interview transcript with Chief Architect Dr. Thorne.
"""


class TestVttDocumentRoutingE2E(unittest.TestCase):
    """End-to-end suite verifying VTT routing, CLI auto-redirection, and collateral intake."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.vtt_file = self.work_dir / "interview_exhibit.vtt"
        self.vtt_file.write_text(SAMPLE_E2E_VTT, encoding="utf-8")
        self.doc_file = self.work_dir / "investigation_dossier.md"
        self.doc_file.write_text(SAMPLE_PRIMARY_DOC, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("dual_pipeline.cli.handlers._ingest_meeting")
    def test_e2e_cli_auto_reroute_lifecycle(self, mock_ingest_meeting: MagicMock) -> None:
        """Full CLI lifecycle: ingest-document with --auto-reroute seamlessly dispatches to meeting pipeline."""
        mock_ingest_meeting.return_value = 0
        parser = build_parser()

        args = parser.parse_args([
            "ingest-document",
            "--input", str(self.vtt_file),
            "--auto-reroute",
            "--project", "incident-alpha",
            "--meeting", "Thorne Interview",
            "--title", "Thorne Deposition",
        ])

        exit_code = _ingest_document(args)
        self.assertEqual(exit_code, 0)
        mock_ingest_meeting.assert_called_once()
        called_args = mock_ingest_meeting.call_args[0][0]
        self.assertEqual(called_args.project, "incident-alpha")
        self.assertEqual(called_args.meeting, "Thorne Deposition")
        self.assertEqual(str(called_args.input), str(self.vtt_file))

    def test_e2e_cli_without_auto_reroute_fails_prescriptively(self) -> None:
        """Full CLI lifecycle: ingest-document without --auto-reroute fails closed with actionable message."""
        parser = build_parser()
        args = parser.parse_args([
            "ingest-document",
            "--input", str(self.vtt_file),
            "--project", "project_nexus",
        ])

        with self.assertRaises(ValueError) as ctx:
            _ingest_document(args)

        err_msg = str(ctx.exception)
        self.assertIn("AUDIO_TRANSCRIPT_ROUTE_EXCLUDED", err_msg)
        self.assertIn("nee ingest-meeting --input", err_msg)

    def test_e2e_collateral_document_envelope_lifecycle(self) -> None:
        """Lifecycle: Multi-source document envelope with primary text and collateral transcript exhibit."""
        # 1. Load primary document
        primary_meta: dict = {}
        primary_text, primary_hash, primary_fmt = load_document_content(self.doc_file, metadata=primary_meta)
        self.assertEqual(primary_fmt, "md")
        self.assertIn("Incident Investigation Dossier", primary_text)

        # 2. Preflight and load collateral transcript exhibit
        vtt_bytes = self.vtt_file.read_bytes()
        pf_res = preflight_attachment(vtt_bytes, self.vtt_file.name, is_collateral=True)
        self.assertTrue(pf_res.is_valid)
        self.assertEqual(pf_res.format_id, "VTT_SRT")

        collateral_meta: dict = {"is_collateral": True}
        collateral_text, collateral_hash, collateral_fmt = load_document_content(self.vtt_file, metadata=collateral_meta)
        self.assertEqual(collateral_fmt, "vtt")
        self.assertIn("Dr. Aris Thorne", collateral_text)
        self.assertNotIn("WEBVTT", collateral_text)
        self.assertNotIn("-->", collateral_text)

        # 3. Verify collateral metadata and source unit extraction
        self.assertTrue(collateral_meta.get("is_collateral_attachment"))
        coverage = collateral_meta.get("_source_coverage", {})
        self.assertTrue(coverage.get("is_collateral_transcript"))
        self.assertEqual(coverage.get("total_records"), 4)

        source_units = collateral_meta.get("source_units", [])
        self.assertEqual(len(source_units), 4)
        for unit in source_units:
            self.assertIn("unit_id", unit)
            self.assertEqual(unit["format"], "vtt_collateral")
            self.assertEqual(unit["unit_type"], "line_block")
            self.assertIn("content", unit)
            self.assertGreater(len(unit["content"]), 0)

        # 4. Manifest building across both components
        manifest = build_source_manifest(
            sources=[self.doc_file, self.vtt_file],
            metadata={"has_collateral": True},
            converted_texts={
                str(self.doc_file): primary_text,
                str(self.vtt_file): collateral_text,
            },
        )
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.total_sources, 2)
        self.assertGreater(manifest.total_raw_bytes, 0)

    def test_e2e_collateral_loading_idempotency(self) -> None:
        """Idempotency check: Repeated collateral loadings produce identical text and content hash."""
        meta1: dict = {"is_collateral": True}
        meta2: dict = {"is_collateral": True}

        text1, hash1, fmt1 = load_document_content(self.vtt_file, metadata=meta1)
        text2, hash2, fmt2 = load_document_content(self.vtt_file, metadata=meta2)

        self.assertEqual(text1, text2)
        self.assertEqual(hash1, hash2)
        self.assertEqual(fmt1, fmt2)
        self.assertEqual(meta1["source_units"], meta2["source_units"])


if __name__ == "__main__":
    unittest.main()
