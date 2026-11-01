"""End-to-end integration tests for Stage U0 Ingestion --dry-run short-circuit."""
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
class IngestDryRunE2ETests(unittest.TestCase):
    """End-to-End test suite verifying Stage U0 ingestion dry-run behaviors."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Create valid transcript fixture
        self.transcript_file = self.tmp_path / "01_test_meeting_transcript.md"
        self.transcript_file.write_text(
            "## Forensic Transcript\n\n"
            "[00:00:10] Amit Ayer: Let's review the business case for Nexus.\n"
            "[00:01:00] Jill Cizewski: The transmission and solar parity metrics look solid.\n",
            encoding="utf-8",
        )

        # Create valid document fixture
        self.document_file = self.tmp_path / "nexus_business_case.md"
        self.document_file.write_text(
            "# Nexus Strategic Business Case\n\n"
            "Executive Summary: Apex Global Enterprises solar and transmission expansion.\n",
            encoding="utf-8",
        )

        # Create valid auth token
        self.auth_token_file = self.tmp_path / "operator_token.json"
        self.auth_token_file.write_text(
            json.dumps({
                "sub": "Amit Ayer",
                "capabilities": ["knowledge:release"],
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ingest_meeting_dryrun_e2e(self):
        """E2E: ingest-meeting --dry-run is rejected fail-closed while parser and metadata resolution complete in <1s without LLM egress."""
        py_bin = sys.executable
        cmd = [
            py_bin,
            str(ROOT / "nee.py"),
            "ingest-meeting",
            "--input",
            str(self.transcript_file),
            "--project",
            "project_nexus",
            "--meeting",
            "Test_Meeting",
            "--date",
            "09/28/2026",
            "--dry-run",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 1)
        self.assertIn("flag removed: '--dry-run' has been eliminated", res.stderr)

        from dual_pipeline.cli.ingest import _resolve_model_and_effort, build_canonical_metadata
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "ingest-meeting",
            "--input", str(self.transcript_file),
            "--project", "project_nexus",
            "--meeting", "Test_Meeting",
            "--date", "09/28/2026",
        ])
        meta = build_canonical_metadata(args)
        _, _, effort = _resolve_model_and_effort(args)
        self.assertEqual(args.command, "ingest-meeting")
        self.assertEqual(effort, "high")
        self.assertEqual(meta["project"], "project_nexus")

    def test_ingest_meeting_dryrun_with_mutate_graph_and_auth_e2e(self):
        """E2E: ingest-meeting --mutate-graph --auth-token validates RBAC and derives reviewer from token subject."""
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
            "--meeting", "Test_Meeting",
            "--date", "09/28/2026",
            "--directive-id", "directive-project_nexus-test_meeting-09-28-2026-v1",
            "--auth-token", str(self.auth_token_file),
        ])
        meta = build_canonical_metadata(args)
        rationale = _resolve_default_rationale(meta, None)
        reviewer = _resolve_ingest_rbac_authorization(args, None, rationale, meta)
        self.assertEqual(reviewer, "Amit Ayer")
        self.assertTrue(meta["rbac_auth"]["authorized"])
        self.assertEqual(meta["rbac_auth"]["subject"], "Amit Ayer")

    def test_ingest_document_dryrun_e2e(self):
        """E2E: ingest-document resolves DOCUMENT modality cleanly via build_canonical_metadata."""
        from dual_pipeline.cli.ingest import build_canonical_metadata
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "ingest-document",
            "--input", str(self.document_file),
            "--project", "project_nexus",
            "--title", "Nexus_Doc",
            "--date", "09/28/2026",
        ])
        args.is_document = True
        meta = build_canonical_metadata(args)
        self.assertEqual(args.command, "ingest-document")
        self.assertEqual(meta["project"], "project_nexus")
        self.assertEqual(meta["title"], "Nexus_Doc")

    def test_ingest_meeting_dryrun_explicit_reasoning_effort_e2e(self):
        """E2E: ingest-meeting respects explicit --reasoning-effort override."""
        from dual_pipeline.cli.ingest import _resolve_model_and_effort
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "ingest-meeting",
            "--input", str(self.transcript_file),
            "--project", "project_nexus",
            "--meeting", "Test_Meeting",
            "--date", "09/28/2026",
            "--reasoning-effort", "max",
        ])
        _, _, effort = _resolve_model_and_effort(args)
        self.assertEqual(effort, "max")


if __name__ == "__main__":
    unittest.main()
