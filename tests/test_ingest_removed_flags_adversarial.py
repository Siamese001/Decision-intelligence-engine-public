"""Adversarial test suite verifying complete elimination of simulated run modes and flags.

Verifies:
1. Passing --package-only exits non-zero with 'flag removed'
2. Passing --dry-run to ingest-document or ingest-meeting exits non-zero with 'flag removed'
3. Passing --dry-run-graph exits non-zero with 'flag removed'
4. Passing --no-mutate-graph exits non-zero with 'flag removed'
5. Passing --provider offline outside test mode exits non-zero with 'flag removed'
6. Subprocess CLI invocations fail closed with code 1
7. Help text for ingest commands does not advertise any removed flags
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli.parser import GovernedArgumentParser, build_parser
from dual_pipeline.core import ROOT


class TestIngestRemovedFlagsAdversarial(unittest.TestCase):
    """Adversarial test suite proving complete rejection of simulated run flags."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_adversarial_package_only_flag_rejected(self) -> None:
        """Verify passing --package-only to ingest-document or ingest-meeting fails closed."""
        for cmd in ("ingest-document", "ingest-meeting"):
            with self.subTest(cmd=cmd):
                with self.assertRaises(SystemExit) as ctx:
                    with patch("sys.stderr", new=io.StringIO()) as fake_err:
                        self.parser.parse_args([cmd, "--input", "fake.txt", "--package-only"])
                self.assertEqual(ctx.exception.code, 1)
                self.assertIn("flag removed: '--package-only' has been eliminated", fake_err.getvalue())

    def test_adversarial_package_only_equals_syntax_rejected(self) -> None:
        """Verify passing --package-only=true fails closed."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new=io.StringIO()) as fake_err:
                self.parser.parse_args(["ingest-document", "--input", "fake.txt", "--package-only=true"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("flag removed: '--package-only' has been eliminated", fake_err.getvalue())

    def test_adversarial_dry_run_flag_rejected_on_ingest(self) -> None:
        """Verify passing --dry-run to ingest-document or ingest-meeting fails closed."""
        for cmd in ("ingest-document", "ingest-meeting"):
            with self.subTest(cmd=cmd):
                with self.assertRaises(SystemExit) as ctx:
                    with patch("sys.stderr", new=io.StringIO()) as fake_err:
                        self.parser.parse_args([cmd, "--input", "fake.txt", "--dry-run"])
                self.assertEqual(ctx.exception.code, 1)
                self.assertIn("flag removed: '--dry-run' has been eliminated", fake_err.getvalue())

    def test_adversarial_dry_run_graph_flag_rejected(self) -> None:
        """Verify passing --dry-run-graph fails closed."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new=io.StringIO()) as fake_err:
                self.parser.parse_args(["ingest-document", "--input", "fake.txt", "--dry-run-graph"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("flag removed: '--dry-run-graph' has been eliminated", fake_err.getvalue())

    def test_adversarial_no_mutate_graph_flag_rejected(self) -> None:
        """Verify passing --no-mutate-graph fails closed."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", new=io.StringIO()) as fake_err:
                self.parser.parse_args(["ingest-document", "--input", "fake.txt", "--no-mutate-graph"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("flag removed: '--no-mutate-graph' has been eliminated", fake_err.getvalue())

    def test_adversarial_provider_offline_rejected_outside_test_mode(self) -> None:
        """Verify passing --provider offline outside authorized test harness fails closed."""
        env_cleared = {
            "PYTEST_CURRENT_TEST": "",
            "NEE_ALLOW_TEST_FIXTURES": "",
            "RUNNING_TESTS_WITH_PROGRESS": "",
            "NEE_TEST_MODE": "",
        }
        with patch.dict(os.environ, env_cleared, clear=False):
            # Test with space separator
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.stderr", new=io.StringIO()) as fake_err:
                    self.parser.parse_args(["ingest-document", "--input", "fake.txt", "--provider", "offline"])
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("flag removed: '--provider offline' has been eliminated", fake_err.getvalue())

            # Test with equals separator
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.stderr", new=io.StringIO()) as fake_err:
                    self.parser.parse_args(["ingest-document", "--input", "fake.txt", "--provider=offline"])
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("flag removed: '--provider offline' has been eliminated", fake_err.getvalue())

    def test_adversarial_subprocess_execution_rejects_removed_flags(self) -> None:
        """Verify real CLI subprocess execution rejects removed flags with code 1."""
        python_bin = sys.executable
        nee_py = ROOT / "nee.py"

        # 1. Test --package-only via subprocess
        proc1 = subprocess.run(
            [python_bin, str(nee_py), "ingest-document", "--input", "fake.txt", "--package-only"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(proc1.returncode, 1)
        self.assertIn("flag removed: '--package-only' has been eliminated", proc1.stderr)

        # 2. Test --dry-run via subprocess
        proc2 = subprocess.run(
            [python_bin, str(nee_py), "ingest-meeting", "--input", "fake.txt", "--dry-run"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(proc2.returncode, 1)
        self.assertIn("flag removed: '--dry-run' has been eliminated", proc2.stderr)

    def test_help_does_not_advertise_removed_flags(self) -> None:
        """Verify help output for ingest-document and ingest-meeting omits all removed flags."""
        for cmd in ("ingest-document", "ingest-meeting"):
            with self.subTest(cmd=cmd):
                with patch("sys.stdout", new=io.StringIO()) as fake_out:
                    with self.assertRaises(SystemExit):
                        self.parser.parse_args([cmd, "--help"])
                help_text = fake_out.getvalue()
                self.assertNotIn("--package-only", help_text)
                self.assertNotIn("--dry-run-graph", help_text)
                self.assertNotIn("--no-mutate-graph", help_text)
                self.assertNotIn("--dry-run ", help_text)
                self.assertNotIn("--auto-mutate-on-pass", help_text)
                self.assertNotIn("--mutate-graph", help_text)


if __name__ == "__main__":
    unittest.main()
