"""Tests for Wave 4 automated word-limit drift detection in governance linters."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.audit_governance_harmonization_core.common import Finding, HarmonizationReport
from tools.audit_governance_harmonization_core.drift_scanners import (
    _scan_stale_word_limit_claims,
    scan_threshold_drift,
)
from tools.lint_constraint_bounds import check_section_density_sum, check_word_limit_envelopes
from tools.scan_ssot_configuration_violations import check_parameter_file_parity, scan_source_code_violations


class TestGovernanceDriftWordLimits(unittest.TestCase):
    """Verify that word-limit drift scanners and linters catch violations and pass on valid code."""

    def test_stale_enforcement_claims_scanner_catches_seeded_violation(self) -> None:
        """Inject synthetic <=1400 chars and <=120 words claims and confirm scanner catches them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_rule = Path(tmpdir) / ".agents" / "rules" / "dummy-rule.md"
            fake_rule.parent.mkdir(parents=True, exist_ok=True)
            fake_rule.write_text(
                "# Dummy Rule\nResponses must be strictly <= 120 words and <= 1400 chars.\n",
                encoding="utf-8",
            )

            report = HarmonizationReport(
                scan_timestamp="2026-10-30T07:00:00Z",
                repo_root=str(Path(tmpdir)),
                total_files_scanned=1,
            )
            _scan_stale_word_limit_claims([fake_rule], report)

            findings = [f for f in report.findings if f.title == "Stale Word-Count Ceiling Enforcement Claim"]
            self.assertGreaterEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "CRITICAL")
            self.assertIn("1400", findings[0].description)

    def test_threshold_drift_scanner_catches_divergent_inv_output_max_words(self) -> None:
        """Inject wrong INV-OUTPUT-MAX-WORDS and verify threshold drift detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_doc = Path(tmpdir) / "governance" / "dummy_policy.md"
            fake_doc.parent.mkdir(parents=True, exist_ok=True)
            fake_doc.write_text(
                "| `INV-OUTPUT-MAX-WORDS` | Ceiling | `150 words` |\n",
                encoding="utf-8",
            )

            report = HarmonizationReport(
                scan_timestamp="2026-10-30T07:00:00Z",
                repo_root=str(Path(tmpdir)),
                total_files_scanned=1,
            )
            scan_threshold_drift([fake_doc], report)

            drift_findings = [f for f in report.findings if f.title == "Threshold Drift: INV-OUTPUT-MAX-WORDS"]
            self.assertEqual(len(drift_findings), 1)
            self.assertEqual(drift_findings[0].ssot_value, "300")

    def test_lint_constraint_bounds_section_density_and_envelopes(self) -> None:
        """Verify check_section_density_sum and envelopes pass with dynamic SSOT resolution."""
        defects = check_section_density_sum()
        self.assertEqual(defects, [], f"Unexpected section density defects: {defects}")

        envelope_defects = check_word_limit_envelopes()
        self.assertEqual(envelope_defects, [], f"Unexpected envelope defects: {envelope_defects}")

    def test_ssot_configuration_parity_and_prohibited_patterns(self) -> None:
        """Verify check_parameter_file_parity and source code scans pass."""
        violations = check_parameter_file_parity()
        self.assertEqual(violations, [], f"Unexpected parameter file parity violations: {violations}")

        code_violations = scan_source_code_violations()
        self.assertEqual(code_violations, [], f"Unexpected source code violations: {code_violations}")


if __name__ == "__main__":
    unittest.main()
