"""Unit tests for repository and reports clutter hygiene planner."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.repo_clutter_hygiene import run_clutter_plan
from tools.storage_hygiene_core.clutter import (
    plan_report_archival,
    plan_root_clutter,
    plan_uncommitted_receipts,
)
from tools.storage_hygiene_core.policy import StorageRetentionPolicy
from tools.storage_hygiene_core.receipt import verify_report_digest


class TestRepoClutterHygiene(unittest.TestCase):
    def test_uncommitted_receipts_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            reports_dir = repo_root / "reports"
            reports_dir.mkdir(parents=True)

            r1 = reports_dir / "pr_review_feat_test_receipt.json"
            r1.write_text('{"status": "PASS", "receipt_id": "r1"}', encoding="utf-8")

            r2 = reports_dir / "pr_review_feat_malformed_receipt.json"
            r2.write_text("{bad json syntax", encoding="utf-8")

            with patch("tools.storage_hygiene_core.clutter._run_git_cmd") as mock_git:
                mock_git.return_value = (
                    f"?? reports/{r1.name}\n?? reports/{r2.name}\n"
                )
                candidates = plan_uncommitted_receipts(repo_root)
                self.assertEqual(len(candidates), 2)

                c_map = {c.name: c for c in candidates}
                self.assertTrue(c_map[r1.name].is_valid_json)
                self.assertEqual(c_map[r1.name].action, "COMMIT_RECEIPT")

                self.assertFalse(c_map[r2.name].is_valid_json)
                self.assertEqual(c_map[r2.name].action, "REVIEW")

    def test_report_archival_respects_references(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            reports_dir = repo_root / "reports"
            reports_dir.mkdir(parents=True)

            # Old unreferenced report
            rep_unref = reports_dir / "old_unref_report.md"
            rep_unref.write_text("old report", encoding="utf-8")

            # Old referenced report
            rep_ref = reports_dir / "old_ref_report.md"
            rep_ref.write_text("old referenced report", encoding="utf-8")

            old_time = time.time() - (30 * 86400)
            os.utime(rep_unref, (old_time, old_time))
            os.utime(rep_ref, (old_time, old_time))

            pol = StorageRetentionPolicy()

            with patch("tools.storage_hygiene_core.clutter._run_git_cmd") as mock_git:
                def fake_git(root, args):
                    cmd_str = " ".join(args)
                    if "grep" in cmd_str:
                        return "docs/some_doc.md:Reference to old_ref_report.md\n"
                    return ""

                mock_git.side_effect = fake_git
                moves = plan_report_archival(repo_root, pol)
                self.assertEqual(len(moves), 2)
                m_map = {m.name: m for m in moves}

                self.assertEqual(m_map["old_unref_report.md"].action, "ARCHIVE_MOVE")
                self.assertIsNotNone(m_map["old_unref_report.md"].target_path)

                self.assertEqual(m_map["old_ref_report.md"].action, "KEEP_REFERENCED")
                self.assertTrue(m_map["old_ref_report.md"].is_referenced)
                self.assertIn("docs/some_doc.md", m_map["old_ref_report.md"].referrers)

    def test_root_clutter_never_touch_firewall_and_no_credential_reads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create credential file env_di
            env_file = repo_root / "env_di"
            env_file.write_text("SUPER_SECRET_TOKEN=xyz", encoding="utf-8")

            # Create debug txt
            debug_file = repo_root / "debug_stage26.txt"
            debug_file.write_text("debug log", encoding="utf-8")

            # Create entry point
            nee_py = repo_root / "nee.py"
            nee_py.write_text("import sys", encoding="utf-8")

            pol = StorageRetentionPolicy()

            # Assert env_di bytes are NEVER opened or read
            with patch("pathlib.Path.open", side_effect=AssertionError("Path.open called on credential!")):
                root_candidates = plan_root_clutter(repo_root, pol)

            rc_map = {rc.name: rc for rc in root_candidates}
            self.assertIn("env_di", rc_map)
            self.assertEqual(rc_map["env_di"].category, "KEEP")
            self.assertIn("never_touch firewall", rc_map["env_di"].reason)

            self.assertIn("debug_stage26.txt", rc_map)
            self.assertEqual(rc_map["debug_stage26.txt"].category, "ARCHIVE_CANDIDATE")

            self.assertIn("nee.py", rc_map)
            self.assertEqual(rc_map["nee.py"].category, "KEEP")

    def test_real_workspace_clutter_plan(self):
        """Run clutter plan on actual workspace and verify schema compliance."""
        report = run_clutter_plan(scheduled=False)
        self.assertNotIn("error", report)
        self.assertTrue(verify_report_digest(report))
        self.assertIn("reports_clutter_analysis", report)
        self.assertIn("root_clutter_analysis", report)

        root_analysis = report["root_clutter_analysis"]
        protected_names = [p["name"] for p in root_analysis["protected_files"]]
        self.assertIn("env_di", protected_names)


if __name__ == "__main__":
    unittest.main()
