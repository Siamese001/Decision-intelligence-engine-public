"""tests/end_to_end/test_merge_result_queue_e2e.py

End-to-End test suite for Speculative Merge-Result Testing & Queue Assembly (Wave 7 / Acceptance Criteria).
Verifies:
1. Non-conflicting branches assemble cleanly into a conflict-free speculative cohort.
2. A planted physical/content conflict between two branches is caught before merge.
3. Speculative cohort receipts contain consolidated files and traceable commit trailers.
4. Empty or invalid cohort inputs fail gracefully with descriptive error receipts.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.merge_train_core.speculative_queue import assemble_speculative_cohort


class MergeResultQueueE2ETests(unittest.TestCase):
    def test_empty_cohort_handling(self) -> None:
        """Verifies that an empty branch list fails closed gracefully."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            receipt = assemble_speculative_cohort("empty_cohort", [], root=root)
            self.assertFalse(receipt.is_conflict_free)
            self.assertFalse(receipt.simulation_passed)
            self.assertIn("No branches provided", receipt.conflicts[0])

    def test_conflict_free_speculative_cohort(self) -> None:
        """Verifies that two non-conflicting feature branches simulate and assemble cleanly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            # 1. Initialize git repo
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "ci@nee.local"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=root, check=True)

            # Base commit on main
            (root / "base.txt").write_text("base content\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init base"], cwd=root, check=True, capture_output=True
            )

            # Branch A: modifies file_a.txt
            subprocess.run(
                ["git", "checkout", "-b", "feat/branch-a"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "file_a.txt").write_text("feature a\n", encoding="utf-8")
            subprocess.run(["git", "add", "file_a.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "feat a"], cwd=root, check=True, capture_output=True
            )

            # Branch B: modifies file_b.txt (off main)
            subprocess.run(
                ["git", "checkout", "master" if self._is_master(root) else "main"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            base_ref = "master" if self._is_master(root) else "main"
            subprocess.run(
                ["git", "checkout", "-b", "feat/branch-b", base_ref],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "file_b.txt").write_text("feature b\n", encoding="utf-8")
            subprocess.run(["git", "add", "file_b.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "feat b"], cwd=root, check=True, capture_output=True
            )

            # Assemble cohort
            receipt = assemble_speculative_cohort(
                cohort_name="platform_train",
                branches=["feat/branch-a", "feat/branch-b"],
                root=root,
                base_branch=base_ref,
            )

            self.assertTrue(
                receipt.is_conflict_free,
                f"Expected conflict-free assembly, got: {receipt.conflicts}",
            )
            self.assertTrue(receipt.simulation_passed)
            self.assertEqual(len(receipt.conflicts), 0)
            self.assertIn("file_a.txt", receipt.consolidated_files)
            self.assertIn("file_b.txt", receipt.consolidated_files)
            self.assertEqual(len(receipt.commit_trailers), 2)
            self.assertTrue(any("feat/branch-a" in t for t in receipt.commit_trailers))
            self.assertTrue(any("feat/branch-b" in t for t in receipt.commit_trailers))

    def test_planted_conflict_caught_before_merge(self) -> None:
        """Verifies that a planted content conflict between two branches is caught before merge."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "ci@nee.local"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=root, check=True)

            # Base commit with shared file
            (root / "shared.txt").write_text("original line 1\noriginal line 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "shared.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init base"], cwd=root, check=True, capture_output=True
            )
            base_ref = "master" if self._is_master(root) else "main"

            # Branch 1: rewrites line 1 to 'ALPHA'
            subprocess.run(
                ["git", "checkout", "-b", "feat/conflict-alpha", base_ref],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "shared.txt").write_text("ALPHA line 1\noriginal line 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "shared.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "alpha change"], cwd=root, check=True, capture_output=True
            )

            # Simulate merge of Alpha into base
            subprocess.run(["git", "checkout", base_ref], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "merge", "--no-ff", "feat/conflict-alpha", "-m", "merge alpha"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            # Branch 2: rewrites line 1 to 'BETA' from original base commit
            first_commit = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "checkout", "-b", "feat/conflict-beta", first_commit],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "shared.txt").write_text("BETA line 1\noriginal line 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "shared.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "beta change"], cwd=root, check=True, capture_output=True
            )

            # Assemble speculative cohort against updated base (which has ALPHA)
            receipt = assemble_speculative_cohort(
                cohort_name="conflict_test_cohort",
                branches=["feat/conflict-beta"],
                root=root,
                base_branch=base_ref,
            )

            # The planted conflict MUST be caught!
            self.assertFalse(receipt.is_conflict_free, "Planted content conflict must be detected")
            self.assertFalse(receipt.simulation_passed)
            self.assertTrue(any("Merge conflict detected" in c for c in receipt.conflicts))

    def _is_master(self, root: Path) -> bool:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip() == "master"


if __name__ == "__main__":
    unittest.main()
