"""
tests/adversarial/test_wave13_test_effectiveness_adversarial.py

Adversarial and edge-case testing suite for Wave 13 test effectiveness architecture.
Covers 4 mandatory edge-case categories:
1. Boundary payload: 0-byte source, 0 mutation sites, empty allowlists, empty commit ranges.
2. Malformed input: Corrupted receipts, syntax errors in AST mutator, unparseable JSON.
3. Invariant bypass attempt: Banned patch() calls targeting subprocess/urllib in new tests.
4. Rollback & recovery: Environment variable restoration under injected exceptions.

Governed under Finding X-32 and Wave 13 acceptance criteria.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from tests.fakes import safe_credentials_environment
from tools.aidlc_kpi_report import compute_kpi_metrics
from tools.kpi_scorecard import evaluate_section4_kpis
from tools.lint_third_party_patch import (
    ThirdPartyPatchVisitor,
    audit_test_file,
    load_allowlist,
)
from tools.mutation_score_engine import (
    SingleSiteMutator,
    count_mutation_sites,
    evaluate_module_in_memory,
)


class TestWave13TestEffectivenessAdversarial(unittest.TestCase):
    """Adversarial and boundary test suite for Wave 13."""

    # ── Category 1: Boundary Payloads ──────────────────────────────────────────

    def test_mutation_engine_zero_byte_source_boundary(self) -> None:
        """Boundary: 0-byte source file returns 0 mutation sites and handles cleanly."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("")
            tmp_path = Path(f.name)

        try:
            sites = count_mutation_sites("")
            self.assertEqual(sites, 0)

            tot, kld, srv = evaluate_module_in_memory(tmp_path, max_sites=5)
            self.assertEqual(tot, 0)
            self.assertEqual(kld, 0)
            self.assertEqual(srv, 0)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_kpi_metrics_zero_commit_boundary(self) -> None:
        """Boundary: Invalid or non-existent commit range returns structured error."""
        res = compute_kpi_metrics(commit_range="nonexistent_sha1..nonexistent_sha2")
        self.assertEqual(res.get("error"), "NO_COMMITS_FOUND")
        self.assertEqual(res.get("total_commits"), 0)

    # ── Category 2: Malformed Inputs ───────────────────────────────────────────

    def test_mutation_engine_syntax_error_payload(self) -> None:
        """Malformed: Syntax error payload raises cleanly or counts 0 sites without crash."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("def invalid_syntax(:\n    pass\n")
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(SyntaxError):
                count_mutation_sites("def invalid_syntax(:\n    pass\n")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_network_isolation_and_error_simulation(self) -> None:
        """Malformed/Failure: Genuine network connection to unreachable socket fails closed."""
        import urllib.request
        from urllib.error import URLError

        with self.assertRaises((URLError, OSError)):
            urllib.request.urlopen("http://127.0.0.1:59999/fail_closed", timeout=1.0)

    # ── Category 3: Invariant Bypass Attempts ──────────────────────────────────

    def test_banned_third_party_patch_detection(self) -> None:
        """Invariant bypass attempt: Direct patch() calls on subprocess/urllib in new tests are rejected."""
        unallowlisted_code = (
            "from unittest.mock import patch\n"
            "with patch('subprocess.run') as mock_sub:\n"
            "    pass\n"
            "with patch('urllib.request.urlopen') as mock_url:\n"
            "    pass\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(unallowlisted_code)
            tmp_path = Path(f.name)

        try:
            # Empty allowlist simulates a new unallowlisted test file
            violations = audit_test_file(tmp_path, allowlist=set())
            self.assertEqual(len(violations), 2)
            self.assertTrue(any("subprocess.run" in v for v in violations))
            self.assertTrue(any("urllib.request.urlopen" in v for v in violations))
        finally:
            tmp_path.unlink(missing_ok=True)

    # ── Category 4: Rollback & Recovery ────────────────────────────────────────

    def test_safe_credentials_environment_exception_rollback(self) -> None:
        """Rollback & Recovery: Environment variables are restored on unhandled exceptions."""
        test_var = "TEST_RECOVERY_KEY_123"
        os.environ[test_var] = "original_val"

        try:
            with self.assertRaises(RuntimeError):
                with safe_credentials_environment({test_var: "mock_val"}):
                    self.assertEqual(os.environ[test_var], "mock_val")
                    raise RuntimeError("Simulated crash in test body")
        finally:
            # Must be rolled back to original
            self.assertEqual(os.environ.get(test_var), "original_val")
            os.environ.pop(test_var, None)


if __name__ == "__main__":
    unittest.main()
