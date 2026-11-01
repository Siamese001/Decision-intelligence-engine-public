"""
tests/test_worktree_inventory_e2e.py

End-to-end lifecycle test for the worktree and stash inventory engine.
Verifies read-only invariance, accurate enumeration of registered and orphan
worktrees, and zero filesystem mutation.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.common import ROOT
from tools.pr_governance_core.worktree_inventory import (
    build_worktree_inventory,
)


class TestWorktreeInventoryE2E(unittest.TestCase):
    """E2E tests for the worktree inventory engine."""

    def test_live_inventory_read_only_invariance(self) -> None:
        """Verifies that running inventory on the live repo performs zero writes."""
        worktrees_dir = ROOT / ".worktrees"
        if not worktrees_dir.exists():
            self.skipTest(".worktrees directory does not exist")

        # Snapshot mtimes of .worktrees entries
        before_mtimes = {
            p: p.stat().st_mtime_ns for p in worktrees_dir.iterdir() if not p.name.startswith(".")
        }

        data = build_worktree_inventory(ROOT)

        # Snapshot mtimes after
        after_mtimes = {
            p: p.stat().st_mtime_ns for p in worktrees_dir.iterdir() if not p.name.startswith(".")
        }

        self.assertEqual(before_mtimes, after_mtimes, "Inventory modified .worktrees mtimes!")
        self.assertIn("counts", data)
        self.assertIn("worktrees", data)
        self.assertIn("stashes", data)
        self.assertGreater(data["counts"]["total_worktrees_children"], 0)

    def test_synthetic_repo_full_lifecycle(self) -> None:
        """Tests inventory end-to-end against a synthetic isolated git repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir).resolve()
            # Init git repo
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test Runner"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            # Create an initial commit on main
            readme = repo / "README.md"
            readme.write_text("# Test Repo\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"], cwd=repo, check=True, capture_output=True
            )

            # Create a stash on main
            readme.write_text("# Test Repo Modified\n", encoding="utf-8")
            subprocess.run(["git", "stash", "push", "-m", "WIP on main: test stash"], cwd=repo, check=True, capture_output=True)

            wt_dir = repo / ".worktrees"
            wt_dir.mkdir()

            # 1. Registered worktree
            reg_dir = wt_dir / "feat_registered"
            subprocess.run(
                ["git", "worktree", "add", "-b", "feat/registered", str(reg_dir), "main"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            # 2. Orphan with deliverable markdown
            deliv_dir = wt_dir / "live_orphan_meeting"
            deliv_dir.mkdir()
            (deliv_dir / "output").mkdir()
            (deliv_dir / "output" / "01_transcript.md").write_text("Forensic transcript", encoding="utf-8")

            # 3. Orphan with receipts only
            receipt_dir = wt_dir / "feat_orphan_receipt"
            receipt_dir.mkdir()
            (receipt_dir / "reports").mkdir()
            (receipt_dir / "reports" / "summary.json").write_text("{}", encoding="utf-8")

            # 4. Orphan with audio only
            audio_dir = wt_dir / "live_orphan_audio"
            audio_dir.mkdir()
            (audio_dir / "output").mkdir()
            (audio_dir / "output" / "chunk_01.wav").write_bytes(b"RIFFdummywav")

            # 5. Orphan stub (empty or small metadata)
            stub_dir = wt_dir / "live_orphan_stub"
            stub_dir.mkdir()
            (stub_dir / "session_state.json").write_text("{}", encoding="utf-8")

            # Run inventory
            data = build_worktree_inventory(repo)
            c = data["counts"]

            self.assertEqual(c["total_worktrees_children"], 5)
            self.assertEqual(c["registered"], 1)
            self.assertEqual(c["orphan_deliverables"], 1)
            self.assertEqual(c["orphan_receipts_only"], 1)
            self.assertEqual(c["orphan_audio_only"], 1)
            self.assertEqual(c["orphan_stubs"], 1)
            self.assertEqual(c["stashes_total"], 1)
            self.assertEqual(c["stashes_main"], 1)

    def test_cli_inventory_json_output(self) -> None:
        """Verifies CLI inventory subcommand emits valid parseable JSON with required keys."""
        local_script = Path(__file__).resolve().parents[1] / "tools" / "pr_governance.py"
        script = local_script if local_script.exists() else (ROOT / "tools" / "pr_governance.py")
        proc = subprocess.run(
            [sys.executable, str(script), "inventory", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI inventory failed: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertIn("counts", data)
        self.assertIn("worktrees", data)
        self.assertIn("stashes", data)


if __name__ == "__main__":
    unittest.main()
