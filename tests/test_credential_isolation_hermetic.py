#!/usr/bin/env python3
"""
tests/test_credential_isolation_hermetic.py

Class A unit tests for hermetic credential isolation and CredentialManager SSOT.
Enforces that no direct os.environ reads for sensitive tokens exist in the codebase.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from storage.credentials import CredentialManager
from tools.scan_credential_leaks import CredentialLeakVisitor, scan_repo

REPO_ROOT = Path(__file__).resolve().parents[1]


class CredentialIsolationHermeticTests(unittest.TestCase):
    """Verifies hermetic credential isolation and CredentialManager capabilities."""

    def test_credential_manager_loads_ssot(self):
        """CredentialManager can resolve a test environment variable or default."""
        val = CredentialManager.get_credential("NON_EXISTENT_TEST_KEY_XYZ", default="fallback_val")
        self.assertEqual(val, "fallback_val")

    def test_credential_manager_masking(self):
        """Validates token masking for security audit telemetry."""
        self.assertEqual(CredentialManager.mask(None), "[NOT_CONFIGURED]")
        self.assertEqual(CredentialManager.mask(""), "[NOT_CONFIGURED]")

        # Short key
        self.assertEqual(CredentialManager.mask("short"), "[REDACTED]")

        # sk- key
        masked_sk = CredentialManager.mask("sk_test_redacted_placeholder")
        self.assertTrue(masked_sk.startswith("sk-"))
        self.assertTrue(masked_sk.endswith("7890"))
        self.assertIn("...", masked_sk)

        # sk-proj- key
        masked_proj = CredentialManager.mask("sk_test_redacted_placeholder")
        self.assertTrue(masked_proj.startswith("sk-proj-"))
        self.assertTrue(masked_proj.endswith("7890"))

    def test_credential_manager_mock_mode(self):
        """CredentialManager safely returns mock client and credentials."""
        client, creds = CredentialManager.get_openai_client(allow_mock=True, model="gpt-5.6-sol")
        self.assertIsNotNone(creds)
        self.assertEqual(creds.model, "gpt-5.6-sol")

    def test_scanner_detects_synthetic_leak(self):
        """AST scanner properly flags direct os.environ.get('OPENAI_API_KEY')."""
        bad_code = """
import os
def get_key():
    return os.environ.get('OPENAI_API_KEY')
"""
        tree = ast.parse(bad_code)
        visitor = CredentialLeakVisitor("synthetic_file.py")
        visitor.visit(tree)
        self.assertEqual(len(visitor.violations), 1)
        self.assertEqual(visitor.violations[0].leak_type, "DIRECT_ENV_SECRET_READ")

    def test_full_repository_scan_has_zero_violations(self):
        """Scans the entire repository and asserts 0 credential leaks or unmanaged secret reads."""
        violations = scan_repo(REPO_ROOT)
        if violations:
            err_msg = "\n".join(
                f"{v.file_path}:{v.line_number} [{v.leak_type}] {v.detail}" for v in violations
            )
            self.fail(f"Expected 0 credential violations, found {len(violations)}:\n{err_msg}")
        self.assertEqual(len(violations), 0)


if __name__ == "__main__":
    unittest.main()
