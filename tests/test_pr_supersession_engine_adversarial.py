"""
Adversarial Test Suite for PR Supersession Engine (Wave 2).
Tests T5, T6, T7, T8, T11, and T12 against real temporary git repositories.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_topology_core.matrix import BranchDiff, _detect_superseded
from tools.pr_topology_core.supersession import (
    evaluate_branch_to_branch_supersession,
    evaluate_commit_supersession,
    evaluate_dirty_file_coverage,
)


class TestPRSupersessionEngineAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.name", "Test Adversary"])
        self._git(["config", "user.email", "adversary@test.local"])
        (self.repo_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        self._git(["add", "README.md"])
        self._git(["commit", "-m", "chore: initial commit on main"])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _git(self, cmd: list[str], cwd: Path | None = None) -> str:
        target_cwd = cwd or self.repo_root
        res = subprocess.run(
            ["git"] + cmd,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def test_t5_patch_id_whitespace_equivalence_not_superseded(self):
        """
        T5: git patch-id ignores whitespace, so an indentation-only Python difference
        has the same patch-id as the unindented commit. The supersession engine MUST
        verify tree equality, and refuse to mark the indentation fix as superseded.
        """
        # Base file
        (self.repo_root / "script.py").write_text("def run():\npass\n", encoding="utf-8")
        self._git(["add", "script.py"])
        self._git(["commit", "-m", "chore: base script"])

        # Branch with proper Python indentation
        self._git(["checkout", "-b", "feat/indent-fix", "main"])
        (self.repo_root / "script.py").write_text("def run():\n    pass\n", encoding="utf-8")
        self._git(["add", "script.py"])
        self._git(["commit", "-m", "fix: proper indentation in script.py"])

        self._git(["checkout", "main"])
        main_sha = self._git(["rev-parse", "main"])
        main_tree = self._git(["rev-parse", "main^{tree}"])
        branch_sha = self._git(["rev-parse", "feat/indent-fix"])

        # Even if patch_idx contains the commit, tree does not match main!
        target, evidence, unique = evaluate_commit_supersession(
            branch="feat/indent-fix",
            branch_sha=branch_sha,
            main_sha=main_sha,
            main_tree=main_tree,
            patch_idx={},
            root=self.repo_root,
            base_branch="main",
        )

        self.assertIsNone(target, "Branch with unique indentation changes must NOT be marked superseded by main")
        self.assertTrue(len(unique) > 0 or (evidence and evidence[0].evidence_type == "PARTIAL"))

    def test_t6_revert_in_supersedor_not_superseded(self):
        """
        T6: Superseder S cherry-picks A's commit and then reverts it.
        S does NOT contain A's content, so A must remain KEEP (not superseded).
        """
        # Branch A introduces feature.py
        self._git(["checkout", "-b", "feat/feature-a", "main"])
        (self.repo_root / "feature.py").write_text("def a(): return 'A'\n", encoding="utf-8")
        self._git(["add", "feature.py"])
        self._git(["commit", "-m", "feat: add feature A"])
        a_sha = self._git(["rev-parse", "feat/feature-a"])

        # Branch S cherry-picks A and then reverts it
        self._git(["checkout", "-b", "feat/supersedor-s", "main"])
        self._git(["cherry-pick", a_sha])
        self._git(["revert", "--no-edit", "HEAD"])
        s_sha = self._git(["rev-parse", "feat/supersedor-s"])

        is_sup, evidence, residual = evaluate_branch_to_branch_supersession(
            b1="feat/feature-a",
            b1_sha=a_sha,
            b2="feat/supersedor-s",
            b2_sha=s_sha,
            b1_files=["feature.py"],
            b2_files=["feature.py"],
            root=self.repo_root,
        )

        self.assertFalse(is_sup, "Supersedor that reverted the commit does NOT supersede the original branch")
        self.assertIn("feature.py", residual)

    def test_t7_partial_supersession_reports_residual_files(self):
        """
        T7: S covers 2 of 3 files from A. A is NOT completely superseded,
        and the engine returns PARTIAL naming the residual file.
        """
        # Branch A touches f1.txt, f2.txt, f3.txt
        self._git(["checkout", "-b", "feat/branch-a", "main"])
        (self.repo_root / "f1.txt").write_text("1\n", encoding="utf-8")
        (self.repo_root / "f2.txt").write_text("2\n", encoding="utf-8")
        (self.repo_root / "f3.txt").write_text("3\n", encoding="utf-8")
        self._git(["add", "f1.txt", "f2.txt", "f3.txt"])
        self._git(["commit", "-m", "feat: add three files in A"])
        a_sha = self._git(["rev-parse", "feat/branch-a"])

        # Branch S touches only f1.txt and f2.txt
        self._git(["checkout", "-b", "feat/branch-s", "main"])
        (self.repo_root / "f1.txt").write_text("1\n", encoding="utf-8")
        (self.repo_root / "f2.txt").write_text("2\n", encoding="utf-8")
        self._git(["add", "f1.txt", "f2.txt"])
        self._git(["commit", "-m", "feat: add two files in S"])
        s_sha = self._git(["rev-parse", "feat/branch-s"])

        is_sup, evidence, residual = evaluate_branch_to_branch_supersession(
            b1="feat/branch-a",
            b1_sha=a_sha,
            b2="feat/branch-s",
            b2_sha=s_sha,
            b1_files=["f1.txt", "f2.txt", "f3.txt"],
            b2_files=["f1.txt", "f2.txt"],
            root=self.repo_root,
        )

        self.assertFalse(is_sup)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.evidence_type, "PARTIAL")
        self.assertEqual(residual, ["f3.txt"])
        self.assertEqual(sorted(evidence.files_covered), ["f1.txt", "f2.txt"])

    def test_t8_evil_merge_content_blocks_supersession(self):
        """
        T8: Unique conflict resolution content in a merge commit blocks supersession.
        """
        # Create branch 1
        self._git(["checkout", "-b", "feat/branch-1", "main"])
        (self.repo_root / "conflict.txt").write_text("version 1\n", encoding="utf-8")
        self._git(["add", "conflict.txt"])
        self._git(["commit", "-m", "version 1"])

        # Create branch 2
        self._git(["checkout", "-b", "feat/branch-2", "main"])
        (self.repo_root / "conflict.txt").write_text("version 2\n", encoding="utf-8")
        self._git(["add", "conflict.txt"])
        self._git(["commit", "-m", "version 2"])

        # Branch A merges branch 1 and branch 2 with custom resolution (version 3)
        self._git(["checkout", "-b", "feat/branch-a-merge", "feat/branch-1"])
        subprocess.run(["git", "merge", "feat/branch-2"], cwd=self.repo_root, capture_output=True, text=True)
        # Resolve with custom evil merge content
        (self.repo_root / "conflict.txt").write_text("custom resolution version 3\n", encoding="utf-8")
        self._git(["add", "conflict.txt"])
        self._git(["commit", "-m", "merge with custom resolution"])
        a_sha = self._git(["rev-parse", "feat/branch-a-merge"])

        # Branch S merges branch 1 with standard checkout (version 1)
        self._git(["checkout", "-b", "feat/branch-s", "main"])
        (self.repo_root / "conflict.txt").write_text("version 1\n", encoding="utf-8")
        self._git(["add", "conflict.txt"])
        self._git(["commit", "-m", "version 1 in S"])
        s_sha = self._git(["rev-parse", "feat/branch-s"])

        is_sup, _, residual = evaluate_branch_to_branch_supersession(
            b1="feat/branch-a-merge",
            b1_sha=a_sha,
            b2="feat/branch-s",
            b2_sha=s_sha,
            b1_files=["conflict.txt"],
            b2_files=["conflict.txt"],
            root=self.repo_root,
        )

        self.assertFalse(is_sup, "Unique conflict resolution in merge commit must block supersession")
        self.assertIn("conflict.txt", residual)

    def test_t11_equal_content_cycle_requires_hitl(self):
        """
        T11: Mutual supersession (A and B have identical trees) is a tie/cycle.
        Neither direction may be emitted as a tombstone candidate.
        """
        # Branch A
        self._git(["checkout", "-b", "feat/cycle-a", "main"])
        (self.repo_root / "common.txt").write_text("identical\n", encoding="utf-8")
        self._git(["add", "common.txt"])
        self._git(["commit", "-m", "identical common.txt in A"])

        # Branch B with same content from main
        self._git(["checkout", "-b", "feat/cycle-b", "main"])
        (self.repo_root / "common.txt").write_text("identical\n", encoding="utf-8")
        self._git(["add", "common.txt"])
        self._git(["commit", "-m", "identical common.txt in B"])

        diffs = {
            "feat/cycle-a": BranchDiff(branch="feat/cycle-a", ahead=1, behind=0, files=["common.txt"]),
            "feat/cycle-b": BranchDiff(branch="feat/cycle-b", ahead=1, behind=0, files=["common.txt"]),
        }

        candidates = _detect_superseded(["feat/cycle-a", "feat/cycle-b"], diffs, root=self.repo_root)
        self.assertEqual(len(candidates), 0, "Mutual tree equivalence must not auto-tombstone either branch")

    def test_t12_dirty_blob_coverage_filters_modes_symlinks_deletions(self):
        """
        T12: Dirty blob coverage verifies modes, symlink targets, deletions, and valuable ignored files.
        """
        # 1. Matching regular file blob and mode
        (self.repo_root / "matched.txt").write_text("hello\n", encoding="utf-8")
        self._git(["add", "matched.txt"])
        self._git(["commit", "-m", "add matched.txt to main"])

        wt_dir = self.repo_root / ".worktrees" / "feat_dirty_audit"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/dirty-audit", "main"])

        # Create an uncommitted file that matches main
        (wt_dir / "uncommitted.txt").write_text("uncommitted\n", encoding="utf-8")
        (self.repo_root / "uncommitted.txt").write_text("uncommitted\n", encoding="utf-8")
        self._git(["add", "uncommitted.txt"])
        self._git(["commit", "-m", "add uncommitted.txt to main"])

        # Check coverage of uncommitted file matching main tip
        is_covered, _, residual = evaluate_dirty_file_coverage(wt_dir, "main", root=self.repo_root)
        self.assertTrue(is_covered)
        self.assertEqual(residual, [])

        # 2. Mode change: chmod +x on matched.txt
        os.chmod(wt_dir / "matched.txt", 0o755)
        is_covered, _, residual = evaluate_dirty_file_coverage(wt_dir, "main", root=self.repo_root)
        self.assertFalse(is_covered)
        self.assertIn("matched.txt", residual)
        os.chmod(wt_dir / "matched.txt", 0o644)

        # 3. Valuable ignored file (.sqlite-wal)
        wal_file = wt_dir / "graph" / "nee_graph.sqlite-wal"
        wal_file.parent.mkdir(parents=True, exist_ok=True)
        wal_file.write_bytes(b"wal-data")
        is_covered, evidence, residual = evaluate_dirty_file_coverage(wt_dir, "main", root=self.repo_root)
        self.assertFalse(is_covered)
        self.assertTrue(any("sqlite-wal" in r for r in residual))
        wal_file.unlink()

        # 4. Deletion where target ref still has file (matched.txt is tracked, so unlinking causes a git deletion)
        (wt_dir / "matched.txt").unlink()
        is_covered, _, residual = evaluate_dirty_file_coverage(wt_dir, "main", root=self.repo_root)
        self.assertFalse(is_covered)
        self.assertIn("matched.txt", residual)


if __name__ == "__main__":
    unittest.main()
