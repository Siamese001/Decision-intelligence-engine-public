"""End-to-End tests for Wave 3: Ephemeralization of Generated Reports.

Verifies:
1. Progress runner executes and regenerates timing DB from scratch when file is missing.
2. Testing gap audit regenerates JSON and Markdown reports from scratch.
3. Post-merge regenerator runs without errors and rebuilds all ephemeral reports.
4. Git ignore and untracked invariants hold for ephemeral reports.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.post_merge_report_regenerator import regenerate_all_reports
from tools.test_timing_db import TestTimingDB


class GeneratedReportsE2ETests(unittest.TestCase):
    def setUp(self):
        self.repo_root = ROOT
        self.timing_file = self.repo_root / "reports" / "test_timing_db.json"
        self.gaps_json = self.repo_root / "reports" / "testing_gaps_audit_report.json"
        self.gaps_md = self.repo_root / "governance" / "decision_intelligence" / "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md"

    def test_01_clean_checkout_without_timing_db_progress_runner(self):
        """Verifies progress runner executes seamlessly without timing DB and creates it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_db = Path(tmpdir) / "reports" / "test_timing_db.json"
            self.assertFalse(temp_db.exists())

            # TestTimingDB handles non-existent file cleanly
            db = TestTimingDB(temp_db)
            self.assertEqual(db.get_duration("tests.test_fake"), 0.0)
            self.assertEqual(db.entry_count(), 0)

            # Record and save
            db.record("tests.test_fake", 0.25)
            db.save()

            self.assertTrue(temp_db.exists())
            with open(temp_db, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data.get("tests.test_fake"), 0.25)

    def test_02_audit_testing_gaps_regenerates_reports_from_scratch(self):
        """Verifies audit_testing_gaps generates markdown and json from scratch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            md_path = out_dir / "audit.md"
            json_path = out_dir / "audit.json"

            py = sys.executable
            cmd = [
                py,
                "-B",
                str(self.repo_root / "tools" / "audit_testing_gaps.py"),
                "--repo-root",
                str(self.repo_root),
                "--output",
                str(md_path),
                "--json-output",
                str(json_path),
            ]
            res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"audit_testing_gaps failed:\n{res.stderr}")

            self.assertTrue(md_path.is_file(), "Markdown audit report must be created.")
            self.assertTrue(json_path.is_file(), "JSON audit report must be created.")

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("overall_score", data)
            self.assertIn("dimension_scores", data)
            self.assertGreaterEqual(data["overall_score"], 80.0)

    def test_03_post_merge_regenerator_full_lifecycle(self):
        """Verifies post_merge_report_regenerator API recreates reports with status SUCCESS."""
        results = regenerate_all_reports(repo_root=self.repo_root, update_baseline=False)
        self.assertEqual(results["testing_gaps"]["status"], "SUCCESS")
        self.assertIn(results["timing_db"]["status"], ("EXISTS", "INITIALIZED"))

        self.assertTrue(self.gaps_json.is_file(), "testing_gaps_audit_report.json must exist after regeneration.")
        self.assertTrue(self.gaps_md.is_file(), "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md must exist after regeneration.")
        self.assertTrue(self.timing_file.is_file(), "test_timing_db.json must exist after regeneration.")

    def test_04_git_untracked_and_ignored_invariants(self):
        """Verifies that ephemeral reports are absent from git ls-files and present in .gitignore."""
        cmd = ["git", "ls-files", "reports", "governance/decision_intelligence"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, check=True)
        tracked_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]

        for forbidden in [
            "reports/test_timing_db.json",
            "reports/testing_gaps_audit_report.json",
            "governance/decision_intelligence/REPO_TESTING_GAPS_AUDIT_REPORT_V1.md",
        ]:
            self.assertNotIn(
                forbidden,
                tracked_files,
                f"Generated report {forbidden} must NOT be tracked by git.",
            )

        gitignore_content = (self.repo_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("reports/test_timing_db.json", gitignore_content)
        self.assertIn("reports/testing_gaps_audit_report.json", gitignore_content)
        self.assertIn("governance/decision_intelligence/REPO_TESTING_GAPS_AUDIT_REPORT_V1.md", gitignore_content)


if __name__ == "__main__":
    unittest.main()
