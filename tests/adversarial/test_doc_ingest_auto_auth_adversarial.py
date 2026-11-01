"""Adversarial and boundary test suite for automated reviewer auth-token resolution.

Covers:
1. Invariant bypass attempt: Unauthorized reviewer identity rejects auto-auth.
2. Invariant bypass attempt: Explicit --no-auto-auth flag enforces strict token requirement.
3. Malformed input: Corrupted/malformed auth token rejects before execution.
4. Exact production regression: Core programmatic validate_mutation_authorization(None, ...) remains fail-closed.
5. Boundary payload: Empty token string / 0-byte file rejects cleanly.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import ROOT
from dual_pipeline.rbac import RBACAuthorizationError, validate_mutation_authorization


@pytest.mark.adversarial
class TestDocIngestAutoAuthAdversarial(unittest.TestCase):
    """Adversarial verification of RBAC boundaries, bypass attempts, and regressions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        self.doc_file = self.tmp_path / "adversarial_doc.md"
        self.doc_file.write_text("# Adversarial Test Document\n\nTesting edge cases.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_removed_reviewer_and_rationale_flags_rejected(self):
        """Category: Invariant bypass attempt - Removed --reviewer and --rationale CLI flags fail closed."""
        py_bin = sys.executable
        for flag in ("--reviewer", "--rationale"):
            cmd = [
                py_bin,
                str(ROOT / "nee.py"),
                "ingest-document",
                "--input",
                str(self.doc_file),
                "--project",
                "project_nexus",
                "--title",
                "Adversarial Review",
                "--date",
                "10-12-2026",
                flag,
                "Untrusted Value",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("flag removed:", res.stderr)
            self.assertIn(flag, res.stderr)

    def test_adversarial_no_auto_auth_flag_enforces_strict_token(self):
        """Category: Invariant bypass attempt - --no-auto-auth disables auto-minting even for recognized reviewer."""
        import argparse
        from dual_pipeline.cli.ingest import _resolve_ingest_rbac_authorization

        mock_args = argparse.Namespace(auto_auth=False, auth_token=None)
        meta: dict = {}
        with self.assertRaises(RBACAuthorizationError) as ctx:
            _resolve_ingest_rbac_authorization(mock_args, "Amit Ayer", None, meta)
        self.assertIn("MUTATION_REJECTED", str(ctx.exception))

    def test_adversarial_malformed_auth_token_fails_closed(self):
        """Category: Malformed input - Corrupted token payload rejects before execution."""
        py_bin = sys.executable
        cmd = [
            py_bin,
            str(ROOT / "nee.py"),
            "ingest-document",
            "--input",
            str(self.doc_file),
            "--project",
            "project_nexus",
            "--title",
            "Adversarial Review",
            "--date",
            "10-12-2026",
            "--auth-token",
            '{"sub": "Amit Ayer", corrupted_json}',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("RBACAuthorizationError", res.stderr)
        self.assertIn("Malformed JSON", res.stderr)

    def test_exact_production_regression_core_rbac_remains_fail_closed(self):
        """Category: Exact production regression - Programmatic validate_mutation_authorization(None, reviewer) fails closed."""
        with self.assertRaises(RBACAuthorizationError) as ctx:
            validate_mutation_authorization(auth_token=None, reviewer="Amit Ayer")

        self.assertIn(
            "MUTATION_REJECTED: Authoritative GraphDB mutation (--mutate-graph) requires a valid "
            "--auth-token with 'knowledge:release' capability",
            str(ctx.exception),
        )

    def test_boundary_empty_token_file_fails_closed(self):
        """Category: Boundary payload - Zero-byte token file fails closed with RBAC error."""
        empty_token = self.tmp_path / "empty_token.json"
        empty_token.write_text("", encoding="utf-8")

        py_bin = sys.executable
        cmd = [
            py_bin,
            str(ROOT / "nee.py"),
            "ingest-document",
            "--input",
            str(self.doc_file),
            "--project",
            "project_nexus",
            "--title",
            "Adversarial Review",
            "--date",
            "10-12-2026",
            "--auth-token",
            str(empty_token),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("RBACAuthorizationError", res.stderr)


if __name__ == "__main__":
    unittest.main()
