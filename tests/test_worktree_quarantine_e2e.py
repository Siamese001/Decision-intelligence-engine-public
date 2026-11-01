"""
tests/test_worktree_quarantine_e2e.py

End-to-end lifecycle test for non-destructive worktree quarantine.
Verifies that orphaned directories are relocated to .local/quarantine/ with
cryptographic manifests, rather than destroyed via rmtree.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_worktree_hygiene_core.pruner import (  # noqa: E402
    cleanup_orphaned_worktree_directories,
    purge_quarantine,
)


class TestWorktreeQuarantineE2E(unittest.TestCase):
    """E2E test suite for worktree quarantine and manifest generation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "quarantine@example.com"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Quarantine Tester"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        (self.repo / "README.md").write_text("# Main\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

        self.wt_dir = self.repo / ".worktrees"
        self.wt_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_quarantine_lifecycle_and_manifest_generation(self) -> None:
        """Verifies orphan directory is moved to quarantine and manifest is written."""
        orphan = self.wt_dir / "live_orphan_stub_session"
        orphan.mkdir()
        (orphan / "output").mkdir()
        test_file = orphan / "output" / "session_state.json"
        content = b'{"status": "test_stub_content"}'
        test_file.write_bytes(content)

        # Execute cleanup with confirmation token
        cleaned = cleanup_orphaned_worktree_directories(
            self.repo, confirm_token="CONFIRMED_STRICT_PRUNE"
        )
        self.assertIn("live_orphan_stub_session", cleaned)
        self.assertFalse(orphan.exists(), "Original orphan directory was not removed from .worktrees")

        today_str = datetime.now(timezone.utc).strftime("%m-%d-%Y")
        quarantine_dir = self.repo / ".local" / "quarantine" / "worktrees" / today_str / "live_orphan_stub_session"
        self.assertTrue(quarantine_dir.is_dir(), f"Quarantined directory does not exist at {quarantine_dir}")

        manifest_file = quarantine_dir / "quarantine_manifest.json"
        self.assertTrue(manifest_file.is_file(), "quarantine_manifest.json was not generated")

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest["directory_name"], "live_orphan_stub_session")
        self.assertEqual(manifest["classification"], "ORPHAN_STUB")
        self.assertEqual(len(manifest["files"]), 1)
        self.assertEqual(manifest["files"][0]["path"], "output/session_state.json")
        self.assertEqual(manifest["files"][0]["size"], len(content))

        # Verify restoration fidelity
        restored_file = quarantine_dir / "output" / "session_state.json"
        self.assertEqual(restored_file.read_bytes(), content)

        # Test purge_quarantine
        purged = purge_quarantine(self.repo, today_str, confirm_token="CONFIRMED_STRICT_PRUNE")
        self.assertIn("live_orphan_stub_session", purged)
        self.assertFalse(quarantine_dir.exists())


if __name__ == "__main__":
    unittest.main()
