"""tests/end_to_end/test_pr_council_complexity_e2e.py

End-to-End integration test suite for 5-Agent Council deliberation,
refactoring complexity T-shirt sizing, and action item hardening across the
PR Governance CLI.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class TestPRCouncilComplexityE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_path = ROOT / "tests" / "fixtures" / "pr_queue_snapshot_fixture.json"
        if not cls.fixture_path.exists():
            cmd = [sys.executable, str(ROOT / "tools" / "pr_governance.py"), "list", "--json"]
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
            cls.fixture_path.write_text(proc.stdout, encoding="utf-8")

    def test_cli_council_json_output_e2e(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "pr_governance.py"),
            "council",
            "--json",
            "--from-snapshot",
            str(self.fixture_path),
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, f"Command failed:\n{proc.stderr}")

        data = json.loads(proc.stdout)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0, "Global queue should contain candidate branches or worktrees")

        for item in data:
            self.assertIn("branch", item)
            self.assertIn("complexity", item)
            comp = item["complexity"]
            self.assertIn("size", comp)
            self.assertIn(comp["size"], {"XS", "S", "M", "L", "XL"})
            self.assertIn("rank", comp)
            self.assertTrue(0 <= comp["rank"] <= 4)
            self.assertIn("score", comp)
            self.assertIn("rationale", comp)

    def test_cli_council_table_deliberation_e2e(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "pr_governance.py"),
            "council",
            "--from-snapshot",
            str(self.fixture_path),
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, f"Command failed:\n{proc.stderr}")
        out = proc.stdout

        # Verify 10-column header
        expected_header = "| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |"
        self.assertIn(expected_header, out)

        # Verify 6-agent council deliberation header and deliberation items
        self.assertIn("6-AGENT COUNCIL DELIBERATION & MERGE SEQUENCE RATIONALIZATION", out)
        self.assertIn("• Merge #1", out)

    def test_cli_list_council_flag_e2e(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "pr_governance.py"),
            "list",
            "--council",
            "--from-snapshot",
            str(self.fixture_path),
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, f"Command failed:\n{proc.stderr}")
        out = proc.stdout

        self.assertIn("6-AGENT COUNCIL DELIBERATION & MERGE SEQUENCE RATIONALIZATION", out)
        self.assertIn("• Merge #1", out)

    def test_cli_list_default_omits_council_deliberation_e2e(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "pr_governance.py"),
            "list",
            "--from-snapshot",
            str(self.fixture_path),
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, f"Command failed:\n{proc.stderr}")
        out = proc.stdout

        expected_header = "| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |"
        self.assertIn(expected_header, out)
        self.assertNotIn("COUNCIL DELIBERATION & MERGE SEQUENCE RATIONALIZATION", out)


if __name__ == "__main__":
    unittest.main()
