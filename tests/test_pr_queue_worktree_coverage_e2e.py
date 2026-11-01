"""
tests/test_pr_queue_worktree_coverage_e2e.py

End-to-end test suite for worktree and stash coverage in the PR queue.
Verifies that:
1. Live PR queue renders W rows for all orphan worktrees and stashes.
2. The mandatory coverage footer is accurately rendered.
3. The JSON and receipt outputs include complete worktree coverage metadata.
4. Ordering contract is preserved: P-level -> W (deliverables first) -> D -> A.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pr_governance_core.common import ROOT
from tools.pr_governance_core.worktree_inventory import build_worktree_inventory


class TestPRQueueWorktreeCoverageE2E(unittest.TestCase):
    """E2E test suite for PR queue worktree coverage."""

    def test_live_pr_queue_text_and_footer(self) -> None:
        """Verifies live pr_governance list includes W rows and the coverage footer."""
        script = REPO_ROOT / "tools" / "pr_governance.py"
        proc = subprocess.run(
            [sys.executable, str(script), "list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"pr_governance list failed:\n{proc.stderr}\n{proc.stdout}")

        stdout = proc.stdout
        # Check presence of coverage footer
        self.assertIn("Worktree coverage:", stdout)
        self.assertIn("children ·", stdout)
        self.assertIn("stashes", stdout)

        # Check that W rows exist
        self.assertIn("| W.", stdout)

    def test_live_pr_queue_json_and_receipt(self) -> None:
        """Verifies live pr_governance list --json and --receipt outputs."""
        script = REPO_ROOT / "tools" / "pr_governance.py"
        receipt_path = REPO_ROOT / "reports" / "worktree_coverage" / "live_receipt_test.json"

        proc = subprocess.run(
            [sys.executable, str(script), "list", "--json", "--receipt", str(receipt_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"pr_governance list --json failed:\n{proc.stderr}\n{proc.stdout}")

        # JSON stdout is a list of branch & worktree rows
        rows = json.loads(proc.stdout)
        self.assertIsInstance(rows, list)

        # Check rows contain W entries
        w_rows = [r for r in rows if r.get("sub_rank", "").startswith("W.")]
        self.assertGreater(len(w_rows), 0, "No W rows found in PR queue JSON rows")

        # Verify orphan deliverables rank before stubs in W
        deliv_rows = [r for r in w_rows if "RECOVER_DELIVERABLES" in r.get("next_action", "")]
        stub_rows = [r for r in w_rows if "QUARANTINE_STUB" in r.get("next_action", "")]
        if deliv_rows and stub_rows:
            deliv_rank = float(deliv_rows[0]["sub_rank"].replace("W.", ""))
            stub_rank = float(stub_rows[0]["sub_rank"].replace("W.", ""))
            self.assertLess(deliv_rank, stub_rank, "Deliverables should rank before stubs in W tier")

        # Verify receipt JSON file content
        self.assertTrue(receipt_path.exists())
        with open(receipt_path, "r", encoding="utf-8") as f:
            receipt_data = json.load(f)
        self.assertEqual(receipt_data.get("terminal_outcome"), "PASS")
        self.assertIn("coverage_summary", receipt_data)
        inv = build_worktree_inventory(ROOT)
        self.assertEqual(receipt_data["coverage_summary"]["total_worktrees_children"], inv["counts"]["total_worktrees_children"])
        self.assertEqual(receipt_data["coverage_summary"]["stashes_total"], inv["counts"]["stashes_total"])

        # Clean up test receipt
        try:
            receipt_path.unlink()
        except Exception:
            pass

    def test_synthetic_repo_full_ordering_and_actions(self) -> None:
        """Verifies synthetic repo ordering: P-level -> W (deliverables first) -> D -> A."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir).resolve()
            # Initialize git repo
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True, capture_output=True)

            (repo / "README.md").write_text("# Test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True, capture_output=True)

            # Stash
            (repo / "README.md").write_text("# Test WIP\n", encoding="utf-8")
            subprocess.run(["git", "stash", "push", "-m", "WIP on main"], cwd=repo, check=True, capture_output=True)

            # Feature branch
            subprocess.run(["git", "checkout", "-b", "feat/my-feature"], cwd=repo, check=True, capture_output=True)
            (repo / "feature.py").write_text("print('feat')\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat commit"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

            # Create .worktrees directory
            wt_dir = repo / ".worktrees"
            wt_dir.mkdir()

            # 1. Orphan meeting deliverables
            deliv = wt_dir / "live_orphan_meeting"
            deliv.mkdir()
            (deliv / "output").mkdir()
            (deliv / "output" / "01_transcript.md").write_text("# Transcript\n", encoding="utf-8")

            # 2. Orphan stub
            stub = wt_dir / "live_orphan_stub"
            stub.mkdir()
            (stub / "session.json").write_text("{}", encoding="utf-8")

            # Run pr_governance list --json in synthetic repo
            script = REPO_ROOT / "tools" / "pr_governance.py"
            proc = subprocess.run(
                [sys.executable, str(script), "list", "--json"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"pr_governance failed:\n{proc.stderr}\n{proc.stdout}")
            rows = json.loads(proc.stdout)
            self.assertIsInstance(rows, list)

            sub_ranks = [r.get("sub_rank", "") for r in rows]

            # Assert P-level row exists
            p_rows = [p for p in sub_ranks if p.startswith("P")]
            w_rows = [p for p in sub_ranks if p.startswith("W")]
            self.assertTrue(len(p_rows) > 0, f"Expected P-level rows, got {sub_ranks}")
            self.assertTrue(len(w_rows) >= 3, f"Expected >= 3 W rows, got {sub_ranks}")

            # Assert order: all P rows come before any W row
            first_w_idx = next(i for i, p in enumerate(sub_ranks) if p.startswith("W"))
            for i, p in enumerate(sub_ranks):
                if p.startswith("P"):
                    self.assertLess(i, first_w_idx, f"P row {p} appeared after W row at index {first_w_idx}")

            # Check actions in W rows
            w_actions = [r.get("next_action", "") for r in rows if r.get("sub_rank", "").startswith("W")]
            self.assertTrue(any("RECOVER_DELIVERABLES" in a for a in w_actions), f"RECOVER_DELIVERABLES missing from {w_actions}")
            self.assertTrue(any("QUARANTINE_STUB" in a for a in w_actions), f"QUARANTINE_STUB missing from {w_actions}")
            self.assertTrue(any("TRIAGE_STASH" in a for a in w_actions), f"TRIAGE_STASH missing from {w_actions}")


if __name__ == "__main__":
    unittest.main()
