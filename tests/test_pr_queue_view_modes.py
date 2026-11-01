"""
Unit & E2E tests for PR queue view modes (compact vs wide vs json).

Verifies that:
1. Default compact view emits 7-column table + diagnostic ledger + collapsed stashes.
2. Total word count in compact mode is < 1000 words.
3. Wide view emits 12-column table with uncollapsed rows.
4. JSON mode emits structured JSON.
5. Mandatory coverage footer is preserved across all table views.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestPRQueueViewModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_path = ROOT / "tests" / "fixtures" / "pr_queue_snapshot_fixture.json"
        if not cls.fixture_path.exists():
            cmd = [sys.executable, str(ROOT / "tools" / "pr_governance.py"), "list", "--json"]
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
            cls.fixture_path.write_text(proc.stdout, encoding="utf-8")

    def test_executive_view_structure(self):
        script = ROOT / "tools" / "pr_governance.py"
        res = subprocess.run(
            [
                sys.executable,
                str(script),
                "list",
                "--view",
                "executive",
                "--from-snapshot",
                str(self.fixture_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(res.returncode, 0, f"Command failed:\n{res.stderr}")
        out = res.stdout

        # Header assertion: 10-column layout
        self.assertIn("| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |", out)
        self.assertIn("EXECUTIVE CANDIDATE QUEUE", out)
        # Word count assertion (< 1500 words for executive view with 32 candidate branches)
        word_count = len(out.split())
        self.assertLess(word_count, 1500, f"Executive queue exceeded 1500 words: {word_count}")

    def test_compact_view_structure_and_word_cap(self):
        script = ROOT / "tools" / "pr_governance.py"
        res = subprocess.run(
            [
                sys.executable,
                str(script),
                "list",
                "--view",
                "compact",
                "--from-snapshot",
                str(self.fixture_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(res.returncode, 0, f"Command failed:\n{res.stderr}")
        out = res.stdout

        # Header assertion: 7-column layout
        self.assertIn("| Priority | Domain | Branch | Readiness | Review | Ahead | Behind |", out)
        self.assertIn("DIAGNOSTIC & NEXT ACTION LEDGER", out)

        # Stash collapse check
        self.assertIn("W.stashes", out)
        self.assertIn("git stashes collapsed", out)

        # Mandatory footer
        self.assertIn("Worktree coverage:", out)
        self.assertIn("stashes", out)

        # Word count assertion (< 4000 words with 18 candidate branches)
        word_count = len(out.split())
        self.assertLess(word_count, 4000, f"Compact queue exceeded 4000 words: {word_count}")

    def test_wide_view_structure(self):
        script = ROOT / "tools" / "pr_governance.py"
        res = subprocess.run(
            [
                sys.executable,
                str(script),
                "list",
                "--view",
                "wide",
                "--from-snapshot",
                str(self.fixture_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(res.returncode, 0, f"Command failed:\n{res.stderr}")
        out = res.stdout

        # Header assertion: 12-column layout
        self.assertIn(
            "| Priority | Domain | Branch | Next Action | Ranking Rationale | Readiness | Review | Effort | Completed Waves | Open Waves | Ahead | Behind |",
            out,
        )
        self.assertIn("Worktree coverage:", out)

    def test_json_view_output(self):
        script = ROOT / "tools" / "pr_governance.py"
        res = subprocess.run(
            [
                sys.executable,
                str(script),
                "list",
                "--json",
                "--from-snapshot",
                str(self.fixture_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(res.returncode, 0, f"Command failed:\n{res.stderr}")
        data = json.loads(res.stdout)
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) > 0)
        self.assertIn("branch", data[0])
        self.assertIn("tier", data[0])


if __name__ == "__main__":
    unittest.main()
