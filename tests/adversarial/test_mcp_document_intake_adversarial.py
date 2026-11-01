"""Adversarial tests for governed MCP document intake bridge.

Categories covered (minimum 3 required):
1. Boundary payload: 0-byte files, non-existent paths, directory paths passed as files.
2. Malformed input & injection: Traversal project names, shell injection tokens in project identifier.
3. Invariant bypass & quarantine: Egress secret patterns (API keys, billing rates) quarantined before egress.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.mcp.intake_bridge import (
    ingest_document_safely,
    sanitize_preview_text,
)


class TestMcpDocumentIntakeAdversarial(unittest.TestCase):
    """Adversarial test suite for MCP document intake bridge."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    # Category 1: Boundary Payloads
    def test_nonexistent_file_returns_clean_failure(self):
        """Boundary: Non-existent file path fails closed with clear error message."""
        fake_path = self.root / "does_not_exist.txt"
        res = ingest_document_safely(fake_path)
        self.assertFalse(res.success)
        self.assertIn("File not found", str(res.error))

    def test_directory_passed_as_file_fails_closed(self):
        """Boundary: Passing a directory instead of a document file fails closed."""
        subdir = self.root / "some_dir"
        subdir.mkdir()
        res = ingest_document_safely(subdir)
        self.assertFalse(res.success)
        self.assertIn("directory", str(res.error).lower())

    def test_zero_byte_empty_file_handled_safely(self):
        """Boundary: 0-byte file fails closed cleanly without unhandled exception."""
        empty_doc = self.root / "empty.txt"
        empty_doc.write_bytes(b"")
        res = ingest_document_safely(empty_doc)
        self.assertFalse(res.success)
        self.assertIn("empty", str(res.error).lower())

    # Category 2: Malformed Input & Project Name Injections
    def test_path_traversal_project_name_rejected(self):
        """Malformed Input: Project names with path traversal or illegal symbols are rejected."""
        valid_file = self.root / "doc.txt"
        valid_file.write_text("Valid content", encoding="utf-8")

        for bad_project in ("../malicious", "proj;rm -rf /", "bad name with spaces", "proj/sub", "a" * 100):
            res = ingest_document_safely(valid_file, project=bad_project)
            self.assertFalse(res.success, f"Project '{bad_project}' should have been rejected")
            self.assertIn("Invalid project identifier", str(res.error))

    # Category 3: Invariant Bypass & Egress Quarantine
    def test_secret_api_key_in_document_triggers_quarantine(self):
        """Invariant Bypass: API keys (sk-...) in content trigger pre-egress quarantine."""
        tainted_file = self.root / "api_keys.txt"
        tainted_file.write_text("Configuration: sk_test_redacted_placeholder\nSecret key.", encoding="utf-8")

        res = ingest_document_safely(tainted_file)
        self.assertTrue(res.success)
        self.assertTrue(res.quarantined)
        self.assertIn("QUARANTINED", res.preview_markdown)
        self.assertNotIn("sk_test_redacted_placeholder", res.preview_markdown)

    def test_billing_rate_in_document_triggers_quarantine(self):
        """Invariant Bypass: Billing rates ($150/hr) in content trigger quarantine."""
        tainted_file = self.root / "contract.txt"
        tainted_file.write_text("Billing agreement: Partner consultant charged at $150/hr.", encoding="utf-8")

        res = ingest_document_safely(tainted_file)
        self.assertTrue(res.success)
        self.assertTrue(res.quarantined)
        self.assertIn("QUARANTINED", res.preview_markdown)
        self.assertNotIn("$150/hr", res.preview_markdown)

    def test_sanitize_preview_text_direct_quarantine_behavior(self):
        """Direct verification of sanitize_preview_text handling clean vs quarantined input."""
        clean_text, q, reason = sanitize_preview_text("Standard engineering report with clean text.")
        self.assertFalse(q)
        self.assertIsNone(reason)
        self.assertEqual(clean_text, "Standard engineering report with clean text.")

        redacted, q, reason = sanitize_preview_text("Confidential billing: $220/hr for strategic advising.")
        self.assertTrue(q)
        self.assertIsNotNone(reason)
        self.assertIn("QUARANTINED", redacted)
