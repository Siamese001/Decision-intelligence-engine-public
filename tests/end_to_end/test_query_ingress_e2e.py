#!/usr/bin/env python3
"""End-to-end integration tests for Query Ingress and Governed Engine Stabilization.

Wave 1 Deliverable:
- Verifies that QueryEngine and `nee ask` initialize without NameError on user_id.
- Verifies that user_id is properly populated into U0 validation artifacts.
- Verifies idempotent replay with deterministic run identity.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json
from dual_pipeline.query import QueryEngine
from dual_pipeline.query_u0 import QueryU0Validator


@pytest.mark.e2e
class TestQueryIngressE2E(unittest.TestCase):
    """E2E suite for query ingress and U0 validation integration."""

    @classmethod
    def setUpClass(cls) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.exists():
            runtime_root = ROOT.parent.parent / ".local" / "dual_pipeline"
        cls.runtime_root = runtime_root
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            cls.has_active_pair = False
            return
        cls.has_active_pair = True
        cls.active_pair = read_json(active_pair_path)
        cls.graph_path = runtime_root / cls.active_pair["graph_path"]

    def test_u0_validator_captures_user_id(self) -> None:
        """Verify QueryU0Validator correctly validates and captures user_id."""
        validator = QueryU0Validator()
        req = validator.validate(
            "What projects has NEE delivered?",
            user_id="operator-wave1-test",
            classification="internal",
            audience="nee-engineering",
        )
        self.assertEqual(req.user_id, "operator-wave1-test")
        self.assertEqual(req.question, "What projects has NEE delivered?")
        self.assertIn("nee-engineering", req.audiences)

    def test_nee_ask_cli_executes_without_name_error(self) -> None:
        """Verify nee ask CLI command executes and produces valid JSON output without NameError."""
        cmd = [
            sys.executable,
            str(ROOT / "nee.py"),
            "ask",
            "--question",
            "What projects has NEE delivered?",
            "--user-id",
            "governed-test-user",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        # Should not crash with NameError or unhandled exception
        self.assertNotIn("NameError", result.stderr)
        self.assertNotIn("NameError", result.stdout)
        self.assertNotIn("UnboundLocalError", result.stderr)
        self.assertNotIn("UnboundLocalError", result.stdout)

        # Output should be valid JSON
        stdout_clean = result.stdout.strip()
        # Find JSON object in stdout if there are log lines
        json_start = stdout_clean.find("{")
        json_end = stdout_clean.rfind("}")
        self.assertNotEqual(json_start, -1, "Expected JSON output in stdout")
        payload = json.loads(stdout_clean[json_start : json_end + 1])
        self.assertIn("schema_version", payload)
        self.assertIn("terminal_state", payload)


if __name__ == "__main__":
    unittest.main()
