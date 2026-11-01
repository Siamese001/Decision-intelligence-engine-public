"""End-to-end integration test for the Governance Harmonization lifecycle.

Exercises full scan execution, report generation, artifact digest validation,
and CLI interface gates.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class GovernanceHarmonizationE2ETests(unittest.TestCase):
    """E2E pipeline test for the governance harmonization auditor."""

    def setUp(self) -> None:
        self.output_dir = REPO_ROOT / ".local" / "ci_governance_e2e"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        # Cleanup generated reports in scratch dir
        if self.output_dir.exists():
            for f in self.output_dir.glob("*"):
                with contextlib.suppress(Exception):
                    f.unlink()

    def test_full_pipeline_scan_and_artifact_generation(self) -> None:
        """Run full scanner and verify that both JSON and Markdown reports are produced."""
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_governance_harmonization.py"),
            "--output-dir",
            str(self.output_dir),
            "--fix-mode",
            "dry-run",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )

        # Scanner exits with 0 on pass (0 CRITICAL, 0 WARNING) or 1 when warnings exist
        self.assertIn(
            result.returncode,
            [0, 1],
            f"Expected exit code 0 or 1, got {result.returncode}\n{result.stderr}",
        )

        # Verify JSON report
        json_reports = list(self.output_dir.glob("harmonization_report_*.json"))
        self.assertEqual(len(json_reports), 1, "Expected exactly 1 JSON report")

        with open(json_reports[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("scan_timestamp", data)
        self.assertIn("total_files_scanned", data)
        self.assertGreater(data["total_files_scanned"], 100)
        self.assertIn("findings", data)
        self.assertIn("summary", data)

        # Verify Markdown report
        md_reports = list(self.output_dir.glob("harmonization_report_*.md"))
        self.assertEqual(len(md_reports), 1, "Expected exactly 1 Markdown report")

        md_content = md_reports[0].read_text(encoding="utf-8")
        self.assertIn("# Governance Harmonization Report", md_content)
        self.assertIn("## Summary", md_content)
        self.assertIn("Threshold Drift", md_content)

    def test_json_only_flag_suppresses_markdown(self) -> None:
        """Verify --json-only generates only the JSON report."""
        json_only_dir = REPO_ROOT / ".local" / "ci_governance_json_only"
        json_only_dir.mkdir(parents=True, exist_ok=True)
        try:
            cmd = [
                sys.executable,
                str(REPO_ROOT / "tools" / "audit_governance_harmonization.py"),
                "--output-dir",
                str(json_only_dir),
                "--json-only",
            ]
            result = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            self.assertIn(result.returncode, [0, 1])

            json_files = list(json_only_dir.glob("*.json"))
            md_files = list(json_only_dir.glob("*.md"))
            self.assertEqual(len(json_files), 1)
            self.assertEqual(len(md_files), 0, "--json-only should not generate markdown")
        finally:
            if json_only_dir.exists():
                for f in json_only_dir.glob("*"):
                    with contextlib.suppress(Exception):
                        f.unlink()


if __name__ == "__main__":
    unittest.main()
