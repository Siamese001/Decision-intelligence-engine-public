"""
tests/adversarial/test_worktree_inventory_adversarial.py

Adversarial and edge-case testing for the worktree inventory engine:
- Symlink traversal escape prevention
- Names with spaces and unicode
- Non-directory children in .worktrees/
- Stale registrations (prunable missing paths)
- Content hash collisions vs updates
- Held .provisioning.lock exclusion
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.worktree_inventory import (
    build_worktree_inventory,
    classify_orphan_directory,
)


class TestWorktreeInventoryAdversarial(unittest.TestCase):
    """Adversarial stress testing for worktree inventory engine."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "adversary@example.com"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Adversary Runner"],
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

    def test_symlink_escaping_root_is_quarantined(self) -> None:
        """Verifies that symlinks pointing outside the repository root are ignored."""
        outside_dir = tempfile.TemporaryDirectory()
        outside_file = Path(outside_dir.name) / "secret.md"
        outside_file.write_text("classified data", encoding="utf-8")

        orphan = self.wt_dir / "live_malicious_symlink"
        orphan.mkdir()
        link_target = orphan / "escaped_link.md"
        os.symlink(outside_file, link_target)

        classification, unique_files = classify_orphan_directory(orphan, self.repo)
        # Should not treat escaped symlink as an internal deliverable
        self.assertEqual(classification, "ORPHAN_STUB")
        self.assertEqual(len(unique_files), 0)
        outside_dir.cleanup()

    def test_unicode_and_spaces_in_directory_names(self) -> None:
        """Verifies that worktree names with spaces and unicode do not break inventory."""
        weird_name = "live_meeting 測試 2026-10-04"
        weird_dir = self.wt_dir / weird_name
        weird_dir.mkdir()
        (weird_dir / "output").mkdir()
        (weird_dir / "output" / "01_transcript.md").write_text("Unicode notes", encoding="utf-8")

        data = build_worktree_inventory(self.repo)
        found = [w for w in data["worktrees"] if w["name"] == weird_name]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["classification"], "ORPHAN_DELIVERABLES")

    def test_non_directory_file_in_worktrees_container(self) -> None:
        """Verifies that accidental plain files in .worktrees/ are reported as NON_DIRECTORY."""
        stray_file = self.wt_dir / "stray_file.txt"
        stray_file.write_text("accidental file", encoding="utf-8")

        data = build_worktree_inventory(self.repo)
        found = [w for w in data["worktrees"] if w["name"] == "stray_file.txt"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["classification"], "NON_DIRECTORY")

    def test_stale_registration_detected(self) -> None:
        """Verifies that worktrees deleted from disk but registered in git are flagged as STALE_REGISTRATION."""
        reg_dir = self.wt_dir / "feat_to_delete"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feat/to-delete", str(reg_dir), "main"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        # Manually rmtree the folder on disk without git worktree remove
        import shutil

        shutil.rmtree(reg_dir)

        data = build_worktree_inventory(self.repo)
        stale = [w for w in data["worktrees"] if w["classification"] == "STALE_REGISTRATION"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["branch"], "feat/to-delete")

    def test_same_path_different_content_detected_as_unique(self) -> None:
        """Verifies that an orphan file sharing a path with main but having different content is classified unique."""
        # Create common file in main
        doc_main = self.repo / "output" / "doc.md"
        doc_main.parent.mkdir(parents=True, exist_ok=True)
        doc_main.write_text("Initial main version", encoding="utf-8")
        subprocess.run(["git", "add", "output/doc.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add doc"], cwd=self.repo, check=True, capture_output=True)

        orphan = self.wt_dir / "live_modified_deliverable"
        orphan.mkdir()
        (orphan / "output").mkdir(parents=True, exist_ok=True)
        (orphan / "output" / "doc.md").write_text("Divergent modified content in worktree", encoding="utf-8")

        classification, unique_files = classify_orphan_directory(orphan, self.repo)
        self.assertEqual(classification, "ORPHAN_DELIVERABLES")
        self.assertEqual(len(unique_files), 1)
        self.assertEqual(unique_files[0]["path"], "output/doc.md")

    def test_held_provisioning_lock_ignored_from_children(self) -> None:
        """Verifies that .provisioning.lock in .worktrees/ is ignored."""
        lock_file = self.wt_dir / ".provisioning.lock"
        lock_file.write_text("locked", encoding="utf-8")

        data = build_worktree_inventory(self.repo)
        lock_entries = [w for w in data["worktrees"] if w["name"] == ".provisioning.lock"]
        self.assertEqual(len(lock_entries), 0)


if __name__ == "__main__":
    unittest.main()
