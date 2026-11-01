"""End-to-end integration tests for automatic reviewer auth-token resolution in document and meeting ingestion."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import ROOT


@pytest.mark.e2e
class TestDocIngestAutoAuthE2E(unittest.TestCase):
    """E2E suite verifying automated auth token injection across document and meeting ingestion CLI entry points."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Create valid test document
        self.doc_file = self.tmp_path / "architecture_memo.md"
        self.doc_file.write_text(
            "# Architecture Review Memo\n\n"
            "Executive Summary: Apex Global Enterprises Decision Intelligence Platform architecture alignment.\n",
            encoding="utf-8",
        )

        # Create valid test transcript
        self.transcript_file = self.tmp_path / "meeting_transcript.md"
        self.transcript_file.write_text(
            "## Forensic Transcript\n\n"
            "[00:00:10] Amit Ayer: Reviewing the document ingestion architecture and token injection.\n"
            "[00:00:45] Jason PartnerAdvisory: Looks aligned with RBAC requirements.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_doc_ingest_cli_auto_auth_e2e(self):
        """E2E: ingest-document without --auth-token or --reviewer resolves operator credentials for 'Amit Ayer'."""
        from dual_pipeline.cli.ingest import (
            _resolve_default_rationale,
            _resolve_ingest_rbac_authorization,
            build_canonical_metadata,
        )
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "ingest-document",
            "--input", str(self.doc_file),
            "--project", "project_nexus",
            "--title", "Architecture Review Memo",
            "--date", "10-12-2026",
        ])
        meta = build_canonical_metadata(args)
        rationale = _resolve_default_rationale(meta, None)
        reviewer = _resolve_ingest_rbac_authorization(args, "Amit Ayer", rationale, meta)

        self.assertEqual(reviewer, "Amit Ayer")
        rbac = meta.get("rbac_auth", {})
        self.assertTrue(rbac.get("authorized"))
        self.assertEqual(rbac.get("subject"), "Amit Ayer")
        self.assertIn("knowledge:release", rbac.get("capabilities", []))

    def test_meeting_ingest_cli_auto_auth_e2e(self):
        """E2E: ingest-meeting without --auth-token or --reviewer resolves operator credentials for 'Amit Ayer'."""
        from dual_pipeline.cli.ingest import (
            _resolve_default_rationale,
            _resolve_ingest_rbac_authorization,
            build_canonical_metadata,
        )
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "ingest-meeting",
            "--input", str(self.transcript_file),
            "--project", "project_nexus",
            "--meeting", "Architecture Review Standup",
            "--date", "10-12-2026",
        ])
        meta = build_canonical_metadata(args)
        rationale = _resolve_default_rationale(meta, None)
        reviewer = _resolve_ingest_rbac_authorization(args, "Amit Ayer", rationale, meta)

        self.assertEqual(reviewer, "Amit Ayer")
        rbac = meta.get("rbac_auth", {})
        self.assertTrue(rbac.get("authorized"))
        self.assertEqual(rbac.get("subject"), "Amit Ayer")

    def test_scripts_ingest_document_wrapper_e2e(self):
        """E2E: scripts/ingest_document.sh omits removed --reviewer/--rationale flags and NEE_AUTH_TOKEN synthesis."""
        script_path = ROOT / "scripts" / "ingest_document.sh"
        script_text = script_path.read_text(encoding="utf-8")
        self.assertNotIn("--reviewer", script_text)
        self.assertNotIn("--rationale", script_text)
        self.assertNotIn("NEE_AUTH_TOKEN", script_text)
        self.assertIn("ingest-document", script_text)

        res = subprocess.run(["bash", str(script_path), "--help"], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, f"Script --help failed: {res.stderr}")
        self.assertNotIn("--reviewer", res.stdout)
        self.assertIn("--author", res.stdout)


if __name__ == "__main__":
    unittest.main()
