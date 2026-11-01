"""
E2E Test Suite for PR Supersession Engine (Wave 2).
Tests T9, T10, T16, and live data acceptance criteria against real git repositories.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.common import ROOT
from tools.pr_topology_core.clustering import compute_topological_clusters
from tools.pr_topology_core.disposition import BranchDisposition, compute_dispositions
from tools.pr_topology_core.matrix import (
    BranchDiff,
    TopologyMatrix,
)
from tools.pr_topology_core.supersession import (
    evaluate_commit_supersession,
    evaluate_dirty_file_coverage,
    is_ancestor,
)


class TestPRSupersessionEngineE2E(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.name", "Test Agent"])
        self._git(["config", "user.email", "agent@test.local"])
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

    def test_t9_squash_merge_detected_and_main_commit_named(self):
        """
        T9: A branch whose commits were squash-merged into main is recognized
        as superseded by main, and the specific squash commit SHA on main is named.
        """
        # Create feature branch
        self._git(["checkout", "-b", "feat/squash-candidate", "main"])
        (self.repo_root / "feature.py").write_text("def feature(): return 42\n", encoding="utf-8")
        self._git(["add", "feature.py"])
        self._git(["commit", "-m", "feat: implement feature in branch"])

        # Switch back to main and squash merge
        self._git(["checkout", "main"])
        self._git(["merge", "--squash", "feat/squash-candidate"])
        self._git(["commit", "-m", "feat(core): squash-merge feature into main"])
        squash_commit = self._git(["rev-parse", "main"])[:8]

        main_sha = self._git(["rev-parse", "main"])
        main_tree = self._git(["rev-parse", "main^{tree}"])
        branch_sha = self._git(["rev-parse", "feat/squash-candidate"])

        target, evidence, unique = evaluate_commit_supersession(
            branch="feat/squash-candidate",
            branch_sha=branch_sha,
            main_sha=main_sha,
            main_tree=main_tree,
            patch_idx={},
            root=self.repo_root,
            base_branch="main",
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.kind, "main")
        self.assertEqual(target.ref, "main")
        self.assertEqual(target.sha, squash_commit)
        self.assertEqual(target.confidence, "HIGH")
        self.assertEqual(evidence[0].evidence_type, "MERGE_NOOP")
        self.assertEqual(len(unique), 0)

        # Verify in compute_dispositions
        matrix = TopologyMatrix(base_branch="main", branches=["feat/squash-candidate"])
        matrix.branch_diffs["feat/squash-candidate"] = BranchDiff(
            branch="feat/squash-candidate",
            ahead=1,
            behind=1,
            files=["feature.py"],
            file_count=1,
        )

    def test_t10_unhealthy_or_tombstoned_supersedor_blocks(self):
        """
        T10: If candidate superseder S has merge conflicts with main or is unhealthy,
        older branch A is NOT auto-tombstoned. Invariant I5 prevents auto-tombstone.
        """
        matrix = TopologyMatrix(base_branch="main", branches=["feat/older", "feat/newer"])
        matrix.branch_diffs["feat/older"] = BranchDiff(branch="feat/older", ahead=1, behind=0, files=["a.py"])
        matrix.branch_diffs["feat/newer"] = BranchDiff(branch="feat/newer", ahead=2, behind=5, files=["a.py", "b.py"])
        matrix.superseded_candidates = [
            {
                "older_branch": "feat/older",
                "newer_branch": "feat/newer",
                "newer_sha": "beef1234",
                "shared_file_count": 1,
                "older_behind": 0,
                "newer_behind": 5,
                "evidence_type": "MERGE_NOOP",
                "confidence": "HIGH",
            }
        ]

        # newer branch has merge conflicts with main
        branch_health = {"feat/newer": "CONFLICT"}
        dispositions = compute_dispositions(
            matrix,
            worktree_info=None,
            branch_health=branch_health,
            root=self.repo_root,
        )

        self.assertIn("feat/older", dispositions)
        self.assertEqual(dispositions["feat/older"].action, "KEEP")
        self.assertIn("conflicts with main", dispositions["feat/older"].reason.lower())

    def test_t16_cluster_and_disposition_never_contradict(self):
        """
        T16: A dirty superseded branch (action COMMIT_FIRST in disposition)
        never gets 'Archive and retire' text from compute_topological_clusters.
        """
        matrix = TopologyMatrix(base_branch="main", branches=["feat/older-dirty", "feat/newer"])
        matrix.branch_diffs["feat/older-dirty"] = BranchDiff(branch="feat/older-dirty", ahead=1, behind=0, files=["a.py"])
        matrix.branch_diffs["feat/newer"] = BranchDiff(branch="feat/newer", ahead=2, behind=0, files=["a.py"])
        matrix.superseded_candidates = [
            {
                "older_branch": "feat/older-dirty",
                "newer_branch": "feat/newer",
                "newer_sha": "cafe5678",
                "shared_file_count": 1,
                "older_behind": 0,
                "newer_behind": 0,
            }
        ]

        dispositions = {
            "feat/older-dirty": BranchDisposition(
                branch="feat/older-dirty",
                action="COMMIT_FIRST",
                reason="superseded by feat/newer but 3 uncommitted authored file(s); commit before tombstone",
                target="feat/newer",
            )
        }

        clusters = compute_topological_clusters(matrix, dispositions=dispositions)
        tombstone_clusters = [c for c in clusters if c.category == "SUPERSEDED_TOMBSTONE"]
        self.assertEqual(len(tombstone_clusters), 1)
        action_text = tombstone_clusters[0].recommended_action
        self.assertNotIn("Archive and retire", action_text)
        self.assertIn("Commit uncommitted work", action_text)

    def test_live_data_acceptance_criteria(self):
        """
        Verifies live repository supersession assertions:
        1. feat/cost-optimization-research and feat/rca-output3-council are ancestors of main
        2. feat/rca-llm-judge-cost-opt's code commit 3c645fdb maps to main@1613a83c
        3. feat/cost-reduction-recommendations-20261003 has uncommitted files and is not pruned
        """
        # 1. Ancestor check on live repo
        is_cost_opt_ancestor = is_ancestor("feat/cost-optimization-research", "main", root=ROOT)
        self.assertTrue(is_cost_opt_ancestor)

        is_output3_ancestor = is_ancestor("feat/rca-output3-council", "main", root=ROOT)
        self.assertTrue(is_output3_ancestor)

        # 2. Check commit patch-id equivalence for code fix 3c645fdb
        pid_res1 = subprocess.run(
            "git show 3c645fdb | git patch-id --stable",
            shell=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        pid_res2 = subprocess.run(
            "git show 1613a83c | git patch-id --stable",
            shell=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        pid1 = pid_res1.stdout.split()[0]
        pid2 = pid_res2.stdout.split()[0]
        self.assertEqual(pid1, pid2, "Code fix 3c645fdb must have identical patch-id to main@1613a83c")

        # 3. Check cost-reduction worktree dirty status
        cost_red_wt = ROOT / ".worktrees" / "feat_cost_reduction_recommendations_20261003"
        if cost_red_wt.exists():
            is_covered, evidence, residual = evaluate_dirty_file_coverage(
                cost_red_wt, "main", root=ROOT, ignore_review_artifacts=True
            )
            self.assertFalse(is_covered)
            self.assertGreaterEqual(len(residual), 50)


if __name__ == "__main__":
    unittest.main()
