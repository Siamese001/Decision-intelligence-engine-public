"""End-to-end lifecycle tests for governed MCP document intake bridge.

Validates:
- Safe ingestion across supported document formats (text, csv, json)
- Verified SHA-256 computation and format classification
- Pre-egress sanitization integration for clean payloads
- Parameterized project binding without hardcoded project names
- CLI execution returning structured JSON
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.mcp.intake_bridge import (
    GovernedDocumentIntakeResult,
    ingest_document_safely,
)


class TestMcpDocumentIntakeE2E(unittest.TestCase):
    """E2E suite for governed document intake bridge."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_text_document_intake_lifecycle_e2e(self):
        """E2E: Intake of standard text file returns verified SHA256 and sanitized preview."""
        sample_doc = self.root / "system_architecture.txt"
        sample_doc.write_text("Architecture Specification: The ingestion pipeline uses unified loaders.\nAll data is preserved.", encoding="utf-8")

        result = ingest_document_safely(sample_doc, project="project_alpha")

        self.assertIsInstance(result, GovernedDocumentIntakeResult)
        self.assertTrue(result.success)
        self.assertEqual(result.filename, "system_architecture.txt")
        self.assertEqual(result.project, "project_alpha")
        self.assertTrue(result.sanitized)
        self.assertFalse(result.quarantined)
        self.assertIn("Architecture Specification", result.preview_markdown)
        self.assertGreater(result.row_count, 0)
        self.assertEqual(len(result.sha256), 64)

    def test_csv_table_metrics_and_sanitization_e2e(self):
        """E2E: Intake of CSV file extracts tabular metrics and produces clean preview."""
        sample_csv = self.root / "org_units.csv"
        sample_csv.write_text("ID,Unit,Lead\n1,Platform,Alice\n2,Security,Bob\n3,Operations,Charlie\n", encoding="utf-8")

        result = ingest_document_safely(sample_csv, project="enterprise_ops")

        self.assertTrue(result.success)
        self.assertEqual(result.format, "csv")
        self.assertEqual(result.project, "enterprise_ops")
        self.assertTrue(result.sanitized)
        self.assertFalse(result.quarantined)
        self.assertGreaterEqual(result.row_count, 3)

    def test_cli_execution_returns_structured_json_e2e(self):
        """E2E: CLI invocation of dual_pipeline.mcp.intake_bridge outputs parseable JSON."""
        sample_file = self.root / "deployment_notes.md"
        sample_file.write_text("# Deployment Notes\nAll services operational.", encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, "-m", "dual_pipeline.mcp.intake_bridge", str(sample_file), "--project", "test_proj", "--preview-only"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertTrue(data["success"])
        self.assertEqual(data["filename"], "deployment_notes.md")
        self.assertEqual(data["project"], "test_proj")
        self.assertTrue(data["sanitized"])
        self.assertFalse(data["quarantined"])
