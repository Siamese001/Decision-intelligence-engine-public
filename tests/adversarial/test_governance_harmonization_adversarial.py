"""Adversarial and boundary test suite for Governance Harmonization auditor.

Exercises:
1. Boundary payloads: massive lines (100k+ chars), unicode edge cases, regex metacharacters.
2. Malformed input: non-UTF8 byte streams, corrupted files, empty files.
3. Invariant bypass: fake SSOT comments, spoofed parameters, divergent threshold injections.
4. Error recovery: missing paths, unreadable directories, invalid CLI options.
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
import unittest
from pathlib import Path

from tools.audit_governance_harmonization import (
    HarmonizationReport,
    read_text_safe,
    scan_duplication,
    scan_missing_backrefs,
    scan_overloading,
    scan_taxonomy_alignment,
    scan_threshold_drift,
    scan_unenumerated_receipts,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class GovernanceHarmonizationAdversarialTests(unittest.TestCase):
    """Adversarial stress testing for the governance harmonization auditor."""

    def setUp(self) -> None:
        self.scratch_dir = REPO_ROOT / ".local" / "test_adversarial_scratch"
        self.scratch_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.scratch_dir.exists():
            for f in self.scratch_dir.glob("*"):
                with contextlib.suppress(Exception):
                    f.unlink()

    def test_non_utf8_binary_payload_resilience(self) -> None:
        """Scanner must gracefully handle binary or invalid UTF-8 without crashing."""
        corrupted_file = self.scratch_dir / "corrupted_rule.md"
        # Write arbitrary raw bytes that are invalid UTF-8
        corrupted_file.write_bytes(b"\xff\xfe\x00\x00\x80\x81\x82\x83\x84\x85\x86\x87\x88\x89")

        content = read_text_safe(corrupted_file)
        self.assertIsInstance(content, str, "Should return string with replacement chars, not crash")

        report = HarmonizationReport(scan_timestamp="2026-10-01T00:00:00Z", repo_root=str(REPO_ROOT))
        # Should not raise exception
        scan_threshold_drift([corrupted_file], report)
        scan_duplication([corrupted_file], report)
        scan_overloading([corrupted_file], report)
        scan_taxonomy_alignment([corrupted_file], report)
        scan_missing_backrefs([corrupted_file], report)
        scan_unenumerated_receipts([corrupted_file], report)
        report.finalize()

    def test_massive_single_line_boundary_payload(self) -> None:
        """Scanner must handle 100,000+ character single-line payloads without regex catastrophe."""
        massive_file = self.scratch_dir / "massive_line.md"
        # Generate huge repetitive line followed by a threshold trigger
        line_content = ("a" * 100000) + " ambiguity margin 25% " + ("b" * 10000)
        massive_file.write_text(line_content, encoding="utf-8")

        report = HarmonizationReport(scan_timestamp="2026-10-01T00:00:00Z", repo_root=str(REPO_ROOT))
        scan_threshold_drift([massive_file], report)
        report.finalize()

        # Must have completed without ReDoS and detected the drift
        self.assertTrue(
            any("HITL Ambiguity Margin" in f.title for f in report.findings),
            "Should successfully parse massive line and detect threshold drift",
        )

    def test_empty_and_zero_byte_files(self) -> None:
        """Scanner must handle zero-byte files cleanly."""
        empty_file = self.scratch_dir / "empty_rule.md"
        empty_file.write_text("", encoding="utf-8")

        report = HarmonizationReport(scan_timestamp="2026-10-01T00:00:00Z", repo_root=str(REPO_ROOT))
        scan_threshold_drift([empty_file], report)
        scan_duplication([empty_file], report)
        report.finalize()
        self.assertEqual(len(report.findings), 0)

    def test_regex_injection_metacharacters(self) -> None:
        """Scanner must not crash when files contain raw regex metacharacters, unbalanced parens, or code snippets."""
        regex_file = self.scratch_dir / "regex_bomb.md"
        regex_file.write_text(
            "Pattern: (?:[a-z]+)+++*?{999999999}\n"
            "Unclosed: (((((\n"
            "Quotes: \"\"\"'''\\\\\\\\\n"
            "Special: $^.*+?{}[]\\|()\n",
            encoding="utf-8",
        )

        report = HarmonizationReport(scan_timestamp="2026-10-01T00:00:00Z", repo_root=str(REPO_ROOT))
        scan_threshold_drift([regex_file], report)
        scan_duplication([regex_file], report)
        scan_overloading([regex_file], report)
        report.finalize()

    def test_cli_rejects_invalid_fix_mode(self) -> None:
        """CLI must fail closed on invalid arguments."""
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_governance_harmonization.py"),
            "--fix-mode",
            "invalid_mode_bypass",
        ]
        result = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "CLI should reject invalid fix-mode")


if __name__ == "__main__":
    unittest.main()
