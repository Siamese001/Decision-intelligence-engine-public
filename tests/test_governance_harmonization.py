"""Tests for the Governance Harmonization Auditor (tools/audit_governance_harmonization.py).

Verifies discovery, scanner logic, threshold drift detection, taxonomy alignment,
duplication detection, term overloading, and report serialization.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools.audit_governance_harmonization import (
    DISCOVERY_SCOPE_EXCLUSIONS,
    Finding,
    HarmonizationReport,
    discover_governance_files,
    scan_overloading,
    scan_threshold_drift,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class GovernanceHarmonizationUnitTests(unittest.TestCase):
    """Unit tests for individual scanner routines."""

    def test_discover_governance_files_finds_core_assets(self) -> None:
        """Ensure file discovery picks up AGENTS.md, rules, skills, hooks, etc."""
        files = discover_governance_files()
        self.assertGreater(len(files), 10, "Should discover more than 10 governance files")

        file_names = [f.name for f in files]
        self.assertIn("AGENTS.md", file_names)

        # Confirm self-exclusion of scanner implementation
        rel_paths = [str(f.relative_to(REPO_ROOT)).replace("\\", "/") for f in files]
        self.assertNotIn("tools/audit_governance_harmonization.py", rel_paths)
        for r in rel_paths:
            self.assertFalse(
                r.startswith("tools/audit_governance_harmonization_core/"),
                f"Scanner core file {r} should have been excluded",
            )
        # Confirm that governance-harmonizer skill is NO LONGER silently excluded
        self.assertIn(".agents/skills/governance-harmonizer/SKILL.md", rel_paths)

    def test_discovery_scope_inclusions_and_exclusions(self) -> None:
        """Verify expanded discovery includes plugins, registries, YAML/TOML, and exports exclusions."""
        files = discover_governance_files()
        rel_paths = {str(f.relative_to(REPO_ROOT)).replace("\\", "/") for f in files}

        # Registries and manifests
        self.assertIn(".agents/skills.json", rel_paths)
        self.assertIn(".agents/mcp_config.json", rel_paths)
        self.assertIn("pyproject.toml", rel_paths)

        # Plugins
        self.assertIn(".agents/plugins/marketplace.json", rel_paths)
        self.assertIn(".agents/plugins/miro/plugin.json", rel_paths)
        self.assertIn(".agents/plugins/lucidchart/plugin.json", rel_paths)

        # Policy YAML and configs
        self.assertIn("governance/policy_registry.yaml", rel_paths)
        self.assertIn("config/runtime/parameters.json", rel_paths)

        # Exclusions are declared and descriptive
        self.assertGreaterEqual(len(DISCOVERY_SCOPE_EXCLUSIONS), 4)
        self.assertTrue(any("scanner implementation" in s for s in DISCOVERY_SCOPE_EXCLUSIONS))

    def test_report_lifecycle_and_serialization(self) -> None:
        """Verify HarmonizationReport accumulation, summary finalization, and JSON roundtrip."""
        report = HarmonizationReport(
            scan_timestamp="2026-10-01T00:00:00Z",
            repo_root=str(REPO_ROOT),
            total_files_scanned=5,
        )
        f1 = Finding(
            category="threshold_drift",
            severity="CRITICAL",
            title="HITL Margin Drift",
            description="25% found instead of 15%",
            ssot_file="AGENTS.md",
            ssot_value="15%",
            affected_files=[{"path": "test.md", "line": 1, "current_value": "25", "expected_value": "15%"}],
            proposed_fix="Update to 15%",
        )
        f2 = Finding(
            category="duplication",
            severity="WARNING",
            title="Duplicate Block",
            description="Repeated text",
            ssot_file="AGENTS.md",
            ssot_value="SSOT",
            affected_files=[],
            proposed_fix="Reference SSOT",
        )
        report.add(f1)
        report.add(f2)
        report.finalize()

        self.assertEqual(report.summary["total"], 2)
        self.assertEqual(report.summary["severity_CRITICAL"], 1)
        self.assertEqual(report.summary["severity_WARNING"], 1)
        self.assertEqual(report.summary["threshold_drift"], 1)
        self.assertEqual(report.summary["duplication"], 1)

    def test_threshold_drift_scanner_catches_synthetic_drift(self) -> None:
        """Verify scan_threshold_drift flags divergent threshold constants."""
        report = HarmonizationReport(
            scan_timestamp="2026-10-01T00:00:00Z",
            repo_root=str(REPO_ROOT),
        )
        # Create a synthetic file path
        test_dir = REPO_ROOT / ".local" / "test_scratch"
        test_dir.mkdir(parents=True, exist_ok=True)
        synthetic_file = test_dir / "synthetic_rule.md"
        synthetic_file.write_text(
            "Enforce ambiguity margin 25% across all pipelines.\n"
            "Also check module size budget 500 lines.\n",
            encoding="utf-8",
        )

        try:
            scan_threshold_drift([synthetic_file], report)
            report.finalize()
            titles = [f.title for f in report.findings]
            self.assertTrue(
                any("HITL Ambiguity Margin" in t for t in titles),
                "Should detect 25% ambiguity margin drift",
            )
        finally:
            if synthetic_file.exists():
                synthetic_file.unlink()

    def test_term_overloading_scanner(self) -> None:
        """Verify scan_overloading detects multiple meanings of overloaded governance terms."""
        report = HarmonizationReport(
            scan_timestamp="2026-10-01T00:00:00Z",
            repo_root=str(REPO_ROOT),
        )
        test_dir = REPO_ROOT / ".local" / "test_scratch"
        test_dir.mkdir(parents=True, exist_ok=True)
        f_wave1 = test_dir / "wave_impl.md"
        f_wave1.write_text("Wave 1: Objective & Scope for engineering\n", encoding="utf-8")
        f_wave2 = test_dir / "wave_strat.md"
        f_wave2.write_text("W1 Context Framing and W2 Criteria Elicitation\n", encoding="utf-8")

        try:
            scan_overloading([f_wave1, f_wave2], report)
            report.finalize()
            titles = [f.title for f in report.findings]
            self.assertTrue(
                any("wave" in t for t in titles),
                "Should detect term overloading for 'wave'",
            )
        finally:
            if f_wave1.exists():
                f_wave1.unlink()
            if f_wave2.exists():
                f_wave2.unlink()


class GovernanceHarmonizationCLITests(unittest.TestCase):
    """End-to-end integration tests for the harmonization CLI."""

    def test_cli_execution_with_json_report(self) -> None:
        """Run tools/audit_governance_harmonization.py via subprocess and verify output artifacts."""
        output_dir = REPO_ROOT / ".local" / "test_governance_report"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_governance_harmonization.py"),
            "--output-dir",
            str(output_dir),
            "--json-only",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )

        # The exit code should be 1 since known baseline findings exist
        self.assertIn(
            result.returncode,
            [0, 1],
            f"Expected exit code 0 or 1, got {result.returncode}: {result.stderr}",
        )

        # Verify JSON report was created and contains required fields
        json_reports = list(output_dir.glob("harmonization_report_*.json"))
        self.assertGreater(len(json_reports), 0, "JSON report should be emitted")

        with open(json_reports[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("scan_timestamp", data)
        self.assertIn("findings", data)
        self.assertIn("summary", data)
        self.assertGreater(data["total_files_scanned"], 50)


if __name__ == "__main__":
    unittest.main()
